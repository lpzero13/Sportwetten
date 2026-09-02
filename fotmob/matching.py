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
    "MANUAL",
    "MANUALLY_CONFIRMED",
    "INVALIDATED",
    "REJECTED",
)

# FotMob generally exposes ISO-like three-letter country codes while Tipico
# exposes localized country names. Keep the same conversion for matching and
# for labels stored by the daily-index persistence path.
COUNTRY_CODE_NAMES = {
    "AFG": "Afghanistan", "ALB": "Albanien", "ALG": "Algerien", "AND": "Andorra",
    "ANG": "Angola", "ARG": "Argentinien", "ARM": "Armenien", "AUS": "Australien",
    "AUT": "Österreich", "AZE": "Aserbaidschan", "BAH": "Bahamas", "BAN": "Bangladesch",
    "BAR": "Barbados", "BEL": "Belgien", "BEN": "Benin", "BER": "Bermuda",
    "BFA": "Burkina Faso", "BHR": "Bahrain", "BIH": "Bosnien und Herzegowina",
    "BLR": "Belarus", "BOL": "Bolivien", "BOT": "Botswana", "BRA": "Brasilien",
    "BRU": "Brunei", "BUL": "Bulgarien", "CAM": "Kamerun", "CAN": "Kanada",
    "CHA": "Tschad", "CHI": "Chile", "CHN": "China", "CIV": "Elfenbeinküste",
    "CMR": "Kamerun", "COD": "Demokratische Republik Kongo", "COL": "Kolumbien",
    "COM": "Komoren", "CPV": "Kap Verde", "CRC": "Costa Rica", "CRO": "Kroatien",
    "CUB": "Kuba", "CUW": "Curaçao", "CYP": "Zypern", "CZE": "Tschechien",
    "DEN": "Dänemark", "DJI": "Dschibuti", "DMA": "Dominica",
    "DOM": "Dominikanische Republik", "ECU": "Ecuador", "EGY": "Ägypten",
    "ENG": "England", "ERI": "Eritrea", "ESP": "Spanien", "EST": "Estland",
    "ETH": "Äthiopien", "FIJ": "Fidschi", "FIN": "Finnland", "FRA": "Frankreich",
    "GAB": "Gabun", "GAM": "Gambia", "GEO": "Georgien", "GER": "Deutschland",
    "GHA": "Ghana", "GIB": "Gibraltar", "GRE": "Griechenland", "GUA": "Guatemala",
    "GUI": "Guinea", "GUY": "Guyana", "HAI": "Haiti", "HKG": "Hongkong",
    "HON": "Honduras", "HUN": "Ungarn", "IDN": "Indonesien", "IND": "Indien",
    "IRL": "Irland", "IRN": "Iran", "IRQ": "Irak", "ISL": "Island", "ISR": "Israel",
    "ITA": "Italien", "JAM": "Jamaika", "JOR": "Jordanien", "JPN": "Japan",
    "KAZ": "Kasachstan", "KEN": "Kenia", "KGZ": "Kirgisistan", "KOR": "Südkorea",
    "KSA": "Saudi-Arabien", "KUW": "Kuwait", "LAO": "Laos", "LBN": "Libanon",
    "LBR": "Liberia", "LBY": "Libyen", "LIE": "Liechtenstein", "LTU": "Litauen",
    "LUX": "Luxemburg", "LVA": "Lettland", "MAC": "Macau", "MAD": "Madagaskar",
    "MAR": "Marokko", "MAS": "Malaysia", "MEX": "Mexiko", "MLT": "Malta",
    "MNE": "Montenegro", "MNG": "Mongolei", "MOZ": "Mosambik", "MRI": "Mauritius",
    "MTN": "Mauretanien", "MWI": "Malawi", "NAM": "Namibia", "NCA": "Nicaragua",
    "NED": "Niederlande", "NEP": "Nepal", "NGA": "Nigeria", "NIG": "Niger",
    "NOR": "Norwegen", "NZL": "Neuseeland", "OMA": "Oman", "PAK": "Pakistan",
    "PAN": "Panama", "PAR": "Paraguay", "PER": "Peru", "PHI": "Philippinen",
    "PLE": "Palästina", "POL": "Polen", "POR": "Portugal", "PRK": "Nordkorea",
    "PUR": "Puerto Rico", "QAT": "Katar", "ROU": "Rumänien", "RSA": "Südafrika",
    "RUS": "Russland", "RWA": "Ruanda", "SCO": "Schottland", "SEN": "Senegal",
    "SGP": "Singapur", "SLE": "Sierra Leone", "SLO": "Slowenien", "SMR": "San Marino",
    "SRB": "Serbien", "SUD": "Sudan", "SUI": "Schweiz", "SVK": "Slowakei",
    "SWE": "Schweden", "SWZ": "Eswatini", "SYR": "Syrien", "TAH": "Tahiti",
    "TAN": "Tansania", "THA": "Thailand", "TJK": "Tadschikistan", "TKM": "Turkmenistan",
    "TOG": "Togo", "TRI": "Trinidad und Tobago", "TUN": "Tunesien", "TUR": "Türkei",
    "UAE": "Vereinigte Arabische Emirate", "UGA": "Uganda", "UKR": "Ukraine",
    "URU": "Uruguay", "USA": "USA", "UZB": "Usbekistan", "VEN": "Venezuela",
    "VIE": "Vietnam", "WAL": "Wales", "YEM": "Jemen", "ZAM": "Sambia",
    "ZIM": "Simbabwe", "INT": "International",
}

AUTO_LINK_STATUSES = frozenset(
    {"EXACT", "HIGH_CONFIDENCE", "MANUAL", "MANUALLY_CONFIRMED"}
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
    # Keep letters/digits from every Unicode script.  Restricting this to
    # ASCII makes two unrelated non-Latin clubs normalize to the same empty
    # string, which can turn a missing-name pair into a false exact match.
    text = "".join(char if char.isalnum() else " " for char in text)
    return " ".join(text.split())


def normalize_team_name(value: Any) -> str:
    normalized = normalize_name(value)
    # Cross-provider display names used in the current Bundesliga feed.  The
    # mapping is deliberately narrow; reserve/youth/gender markers are still
    # retained and rejected by the marker guard below.
    aliases = {
        "borussia m gladbach": "borussia monchengladbach",
        "m gladbach": "borussia monchengladbach",
        "fc augsburg": "augsburg",
        "sc freiburg": "freiburg",
        "1 fc koln": "koln",
        "fc koln": "koln",
        "fsv mainz 05": "mainz 05",
        "sc paderborn 07": "paderborn",
        "paderborn 07": "paderborn",
        "hsv": "hamburger sv",
    }
    return aliases.get(normalized, normalized)


_TEAM_DECORATIONS = frozenset(
    {
        "ac",
        "afc",
        "as",
        "bsc",
        "calcio",
        "cf",
        "club",
        "fc",
        "fk",
        "sc",
        "sk",
        "ssc",
        "sv",
        "tsv",
        "us",
        "vfb",
        "vfl",
    }
)


def _team_core(value: Any) -> str:
    """Remove harmless club-form decorations for cross-provider matching."""

    tokens = normalize_team_name(value).split()
    core = [token for token in tokens if token not in _TEAM_DECORATIONS]
    return " ".join(core) or normalize_team_name(value)


def normalize_competition_name(value: Any) -> str:
    return normalize_name(value)


def normalize_country(value: Any) -> str:
    normalized = normalize_name(value)
    aliases = {
        "de": "deutschland",
        "germany": "deutschland",
        "ger": "deutschland",
        "deu": "deutschland",
        "at": "osterreich",
        "austria": "osterreich",
        "aut": "osterreich",
        "ch": "schweiz",
        "switzerland": "schweiz",
        "che": "schweiz",
        "gb": "england",
        "uk": "england",
        "eng": "england",
        "ita": "italien",
        "italy": "italien",
        "esp": "spanien",
        "spain": "spanien",
        "fra": "frankreich",
        "france": "frankreich",
        "ned": "niederlande",
        "netherlands": "niederlande",
        "nld": "niederlande",
        "bel": "belgien",
        "belgium": "belgien",
        "prt": "portugal",
        "por": "portugal",
        "tur": "turkei",
        "turkey": "turkei",
        "grc": "griechenland",
        "gre": "griechenland",
        "den": "danemark",
        "dnk": "danemark",
        "sweden": "schweden",
        "swe": "schweden",
        "norway": "norwegen",
        "nor": "norwegen",
        "poland": "polen",
        "pol": "polen",
        "czech republic": "tschechien",
        "czechia": "tschechien",
        "cze": "tschechien",
        "croatia": "kroatien",
        "hrv": "kroatien",
        "serbia": "serbien",
        "srb": "serbien",
        "romania": "rumanien",
        "rou": "rumanien",
        "hungary": "ungarn",
        "hun": "ungarn",
        "slovakia": "slowakei",
        "svk": "slowakei",
        "slovenia": "slowenien",
        "svn": "slowenien",
        "switzerland": "schweiz",
        "ukraine": "ukraine",
        "ukr": "ukraine",
        "russia": "russland",
        "rus": "russland",
        "brazil": "brasilien",
        "bra": "brasilien",
        "argentina": "argentinien",
        "arg": "argentinien",
        "mexico": "mexiko",
        "mex": "mexiko",
        "usa": "usa",
        "united states": "usa",
        "canada": "kanada",
        "can": "kanada",
        "japan": "japan",
        "jpn": "japan",
        "south korea": "sudkorea",
        "kor": "sudkorea",
        "australia": "australien",
        "aus": "australien",
        "ireland": "irland",
        "irl": "irland",
        "scotland": "schottland",
        "sco": "schottland",
        "armenia": "armenien",
        "azerbaijan": "aserbaidschan",
        "bosnia and herzegovina": "bosnien und herzegowina",
        "cote divoire": "elfenbeinkuste",
        "ivory coast": "elfenbeinkuste",
        "democratic republic of the congo": "demokratische republik kongo",
        "hong kong": "hongkong",
        "kazakhstan": "kasachstan",
        "kyrgyzstan": "kirgisistan",
        "north korea": "nordkorea",
        "new zealand": "neuseeland",
        "saudi arabia": "saudi arabien",
        "south africa": "sudafrika",
        "south korea": "sudkorea",
        "turkey": "turkei",
        "united arab emirates": "vereinigte arabische emirate",
        "international": "international",
    }
    aliases.update(
        {
            code.casefold(): normalize_name(name)
            for code, name in COUNTRY_CODE_NAMES.items()
        }
    )
    return aliases.get(normalized, normalized)


def country_name_for_code(value: Any) -> str | None:
    """Return a stable German/UI label for a provider country code."""

    return COUNTRY_CODE_NAMES.get(normalize_name(value).upper())


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
        return self.status in AUTO_LINK_STATUSES


def _alias_key(value: Any, aliases: Mapping[str, str] | None) -> str:
    normalized = normalize_team_name(value)
    if not aliases:
        return normalized
    return normalize_team_name(aliases.get(normalized, aliases.get(str(value), normalized)))


def team_names_equivalent(
    left: Any,
    right: Any,
    aliases: Mapping[str, str] | None = None,
) -> bool:
    """Compare team names with the same cautious rules used by the matcher."""

    left_key = _alias_key(left, aliases)
    right_key = _alias_key(right, aliases)
    if not left_key or not right_key:
        return False
    return left_key == right_key or _team_core(left_key) == _team_core(right_key)


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
        return team_names_equivalent(left, right, self.team_aliases)

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
        if any(
            not normalize_team_name(value)
            for value in (
                tipico.home_team,
                tipico.away_team,
                fotmob.home_team,
                fotmob.away_team,
            )
        ):
            return MatchCandidate(
                fotmob.provider_match_id or "",
                0.0,
                "UNMATCHED",
                ["missing_team_name"],
            )
        # Team IDs belong to provider-specific namespaces just like match
        # IDs. Never compare a raw Tipico team ID directly with a FotMob team
        # ID; only the centralized names/aliases are safe at this stage.
        home_exact = self._team_equal(tipico.home_team, fotmob.home_team)
        if home_exact:
            reasons.append("home_name_or_alias")
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
            # A persisted provider ID is a fast-path link, not evidence that a
            # newly supplied candidate still represents the same event.  The
            # candidate must pass the normal competition/team/kickoff checks;
            # Tipico and FotMob IDs are provider-specific namespaces.
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
