"""Deterministic Tipico-to-FotMob match matching.

Matching is deliberately explainable.  A date-only or same-day match is never
enough; kickoff tolerance, competition/country and home/away order all matter.
Fuzzy matching is a discovery aid only and cannot reverse home and away teams.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any, Mapping

from .models import FotMobMatch


MATCH_STATUSES = (
    "EXACT",
    "HIGH_CONFIDENCE",
    "AMBIGUOUS",
    "UNMATCHED",
    "MANUALLY_CONFIRMED",
    "REJECTED",
)


def normalize_name(value: Any) -> str:
    """Normalize names without erasing reserve, youth or gender markers."""

    if value is None:
        return ""
    text = unicodedata.normalize("NFKD", str(value)).casefold()
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.replace("ß", "ss")
    text = text.replace("&", " and ")
    text = re.sub(r"\bmunich\b", "munchen", text)
    text = re.sub(r"\bmuenchen\b", "munchen", text)
    text = re.sub(r"\bvienna\b", "wien", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def normalize_team_name(value: Any) -> str:
    return normalize_name(value)


def normalize_competition_name(value: Any) -> str:
    return normalize_name(value)


def normalize_country(value: Any) -> str:
    normalized = normalize_name(value)
    aliases = {
        "de": "deutschland",
        "germany": "deutschland",
        "ger": "deutschland",
        "at": "osterreich",
        "austria": "osterreich",
        "aut": "osterreich",
        "ch": "schweiz",
        "switzerland": "schweiz",
        "gb": "england",
        "uk": "england",
    }
    return aliases.get(normalized, normalized)


def _parse_datetime(value: Any) -> datetime | None:
    if value is None or not str(value).strip():
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _team_markers(value: Any) -> set[str]:
    tokens = set(normalize_team_name(value).split())
    markers = {
        "ii", "iii", "iv", "b", "reserve", "reserves", "u17", "u18", "u19",
        "u20", "u21", "u23", "youth", "women", "woman", "frauen", "ladies",
        "femenino", "feminino", "w", "ii",
    }
    return tokens & markers


@dataclass(slots=True, frozen=True)
class MatchIdentity:
    provider: str
    provider_match_id: str | None
    kickoff_at: str | None
    competition_id: str | None
    competition_name: str
    competition_country: str | None
    home_team: str
    away_team: str
    home_team_id: str | None = None
    away_team_id: str | None = None

    @classmethod
    def from_tipico_event(cls, event: Any) -> "MatchIdentity":
        return cls(
            provider="TIPICO",
            provider_match_id=str(getattr(event, "event_id", "")) or None,
            kickoff_at=getattr(event, "kickoff_time", None),
            competition_id=(
                str(getattr(event, "competition_id"))
                if getattr(event, "competition_id", None) is not None
                else None
            ),
            competition_name=str(getattr(event, "competition_name", "") or ""),
            competition_country=getattr(event, "competition_country", None),
            home_team=str(getattr(event, "home_team", "") or ""),
            away_team=str(getattr(event, "away_team", "") or ""),
            home_team_id=(
                str(getattr(event, "home_team_id"))
                if getattr(event, "home_team_id", None) is not None
                else None
            ),
            away_team_id=(
                str(getattr(event, "away_team_id"))
                if getattr(event, "away_team_id", None) is not None
                else None
            ),
        )

    @classmethod
    def from_fotmob_match(cls, match: FotMobMatch) -> "MatchIdentity":
        return cls(
            provider="FOTMOB",
            provider_match_id=match.provider_match_id,
            kickoff_at=match.kickoff_at,
            competition_id=match.competition_id,
            competition_name=match.competition_name or "",
            competition_country=match.competition_country,
            home_team=match.home_team,
            away_team=match.away_team,
            home_team_id=match.home_team_id,
            away_team_id=match.away_team_id,
        )


@dataclass(slots=True)
class MatchCandidate:
    provider_match_id: str
    score: float
    status: str
    reasons: list[str] = field(default_factory=list)
    kickoff_delta_minutes: float | None = None


@dataclass(slots=True)
class MatchMatchResult:
    status: str
    confidence: float
    provider_match_id: str | None
    reasons: list[str] = field(default_factory=list)
    candidates: list[MatchCandidate] = field(default_factory=list)

    @property
    def auto_linkable(self) -> bool:
        return self.status in {"EXACT", "HIGH_CONFIDENCE"}


def _alias_key(value: Any, aliases: Mapping[str, str] | None) -> str:
    normalized = normalize_team_name(value)
    if not aliases:
        return normalized
    return normalize_team_name(aliases.get(normalized, aliases.get(str(value), normalized)))


def _known_id(value: str | None, known: Mapping[str, str] | None) -> str | None:
    if not value or not known:
        return None
    return known.get(str(value))


class MatchMatcher:
    """Apply the V0.5 deterministic matching pipeline."""

    def __init__(
        self,
        *,
        tolerance_minutes: int = 15,
        team_aliases: Mapping[str, str] | None = None,
        competition_aliases: Mapping[str, str] | None = None,
        known_provider_ids: Mapping[str, str] | None = None,
    ) -> None:
        self.tolerance_minutes = max(1, int(tolerance_minutes))
        self.team_aliases = dict(team_aliases or {})
        self.competition_aliases = dict(competition_aliases or {})
        self.known_provider_ids = dict(known_provider_ids or {})

    def _team_equal(self, left: str, right: str) -> bool:
        return _alias_key(left, self.team_aliases) == _alias_key(right, self.team_aliases)

    def _competition_equal(self, left: str, right: str) -> bool:
        normalized_left = normalize_competition_name(left)
        normalized_right = normalize_competition_name(right)
        if normalized_left == normalized_right:
            return True
        mapped_left = normalize_competition_name(
            self.competition_aliases.get(normalized_left, normalized_left)
        )
        mapped_right = normalize_competition_name(
            self.competition_aliases.get(normalized_right, normalized_right)
        )
        return mapped_left == mapped_right

    def score_candidate(
        self,
        tipico: MatchIdentity,
        fotmob: MatchIdentity,
    ) -> MatchCandidate:
        reasons: list[str] = []
        if tipico.home_team_id and fotmob.home_team_id and tipico.home_team_id == fotmob.home_team_id:
            home_exact = True
            reasons.append("home_provider_id")
        else:
            home_exact = self._team_equal(tipico.home_team, fotmob.home_team)
            if home_exact:
                reasons.append("home_name_or_alias")
        if tipico.away_team_id and fotmob.away_team_id and tipico.away_team_id == fotmob.away_team_id:
            away_exact = True
            reasons.append("away_provider_id")
        else:
            away_exact = self._team_equal(tipico.away_team, fotmob.away_team)
            if away_exact:
                reasons.append("away_name_or_alias")

        # A reverse fixture is never accepted as the same event, even when a
        # stale/incorrect provider ID happens to look like a team ID match.
        reverse = self._team_equal(tipico.home_team, fotmob.away_team) and self._team_equal(
            tipico.away_team, fotmob.home_team
        )
        if reverse:
            return MatchCandidate(fotmob.provider_match_id or "", 0.0, "UNMATCHED", ["home_away_reversed"])

        marker_mismatch = bool(
            _team_markers(tipico.home_team) != _team_markers(fotmob.home_team)
            or _team_markers(tipico.away_team) != _team_markers(fotmob.away_team)
        )
        if marker_mismatch:
            return MatchCandidate(fotmob.provider_match_id or "", 0.0, "UNMATCHED", ["reserve_youth_gender_guard"])

        tipico_time = _parse_datetime(tipico.kickoff_at)
        fotmob_time = _parse_datetime(fotmob.kickoff_at)
        delta: float | None = None
        if tipico_time is None or fotmob_time is None:
            reasons.append("kickoff_missing")
            return MatchCandidate(fotmob.provider_match_id or "", 0.0, "UNMATCHED", reasons)
        delta = abs((tipico_time - fotmob_time).total_seconds()) / 60
        if delta > self.tolerance_minutes:
            return MatchCandidate(fotmob.provider_match_id or "", 0.0, "UNMATCHED", ["kickoff_outside_tolerance"])
        reasons.append(f"kickoff_delta_{delta:.1f}m")

        country_left = normalize_country(tipico.competition_country)
        country_right = normalize_country(fotmob.competition_country)
        if country_left and country_right and country_left != country_right:
            return MatchCandidate(fotmob.provider_match_id or "", 0.0, "UNMATCHED", ["country_mismatch"])
        competition_equal = self._competition_equal(tipico.competition_name, fotmob.competition_name)
        if competition_equal:
            reasons.append("competition_exact_or_alias")
        elif normalize_competition_name(tipico.competition_name) and normalize_competition_name(fotmob.competition_name):
            similarity = SequenceMatcher(
                None,
                normalize_competition_name(tipico.competition_name),
                normalize_competition_name(fotmob.competition_name),
            ).ratio()
            if similarity < 0.70:
                return MatchCandidate(fotmob.provider_match_id or "", 0.0, "UNMATCHED", ["competition_mismatch"])
            reasons.append(f"competition_fuzzy_{similarity:.2f}")

        if not home_exact or not away_exact:
            home_similarity = SequenceMatcher(
                None, normalize_team_name(tipico.home_team), normalize_team_name(fotmob.home_team)
            ).ratio()
            away_similarity = SequenceMatcher(
                None, normalize_team_name(tipico.away_team), normalize_team_name(fotmob.away_team)
            ).ratio()
            if min(home_similarity, away_similarity) < 0.78:
                return MatchCandidate(fotmob.provider_match_id or "", 0.0, "UNMATCHED", ["team_fuzzy_below_cutoff"])
            reasons.append(f"controlled_fuzzy_{home_similarity:.2f}/{away_similarity:.2f}")

        score = 0.0
        score += 0.42 if home_exact else 0.30
        score += 0.42 if away_exact else 0.30
        score += 0.10 if competition_equal else 0.05
        score += 0.06 if delta <= 5 else 0.03
        score += 0.04 if country_left and country_right else 0.0
        score = min(1.0, score)
        exact = home_exact and away_exact and competition_equal and delta <= 5
        status = "EXACT" if exact else ("HIGH_CONFIDENCE" if score >= 0.80 else "UNMATCHED")
        return MatchCandidate(
            fotmob.provider_match_id or "",
            score,
            status,
            reasons,
            delta,
        )

    def match(
        self,
        tipico: MatchIdentity,
        candidates: list[FotMobMatch],
    ) -> MatchMatchResult:
        scored: list[MatchCandidate] = []
        for candidate in candidates:
            identity = MatchIdentity.from_fotmob_match(candidate)
            known_for_tipico = self.known_provider_ids.get(identity.provider_match_id or "")
            if known_for_tipico and known_for_tipico == tipico.provider_match_id:
                scored.append(
                    MatchCandidate(
                        identity.provider_match_id or "",
                        1.0,
                        "EXACT",
                        ["known_provider_match_id"],
                        0.0,
                    )
                )
            else:
                scored.append(self.score_candidate(tipico, identity))
        scored.sort(key=lambda item: (-item.score, item.provider_match_id))
        viable = [item for item in scored if item.score > 0]
        if not viable:
            return MatchMatchResult("UNMATCHED", 0.0, None, ["no_viable_candidate"], scored)
        best = viable[0]
        if len(viable) > 1 and viable[1].score >= best.score - 0.05:
            return MatchMatchResult(
                "AMBIGUOUS",
                best.score,
                None,
                ["multiple_candidates_within_0.05", *best.reasons],
                scored,
            )
        return MatchMatchResult(best.status, best.score, best.provider_match_id, best.reasons, scored)
