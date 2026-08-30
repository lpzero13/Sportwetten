"""Safe Tipico-to-FotMob fixture resolution for the V0.5.3 HT path."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from .history_storage import FotMobHistoryStore
from .matching import MatchMatchResult, normalize_competition_name, normalize_country
from .models import FotMobMatch

if TYPE_CHECKING:  # pragma: no cover - import only for type checkers
    from .service import FotMobService


CONFIRMED_LINK_STATUSES = {"EXACT", "HIGH_CONFIDENCE", "MANUALLY_CONFIRMED"}


@dataclass(frozen=True, slots=True)
class CompetitionMapping:
    internal_competition_id: str
    provider_competition_id: str
    tipico_competition_name: str
    tipico_country: str
    provider_competition_name: str
    provider_country: str
    confidence: float = 1.0
    match_status: str = "MANUALLY_CONFIRMED"
    source: str = "V053_DEFAULT_MAPPING"


DEFAULT_COMPETITION_MAPPINGS = (
    # Tipico 42301 is the German Bundesliga.  The Austrian Bundesliga has a
    # different Tipico competition ID and is intentionally not mapped here.
    CompetitionMapping(
        internal_competition_id="42301",
        provider_competition_id="54",
        tipico_competition_name="Bundesliga",
        tipico_country="Deutschland",
        provider_competition_name="Bundesliga",
        provider_country="GER",
    ),
)


def _parse_time(value: Any) -> datetime | None:
    if value is None or not str(value).strip():
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _row_to_match(row: Any) -> FotMobMatch:
    return FotMobMatch(
        provider_match_id=str(row["fotmob_match_id"]),
        kickoff_at=row["kickoff_at"],
        competition_id=str(row["league_id"]) if row["league_id"] is not None else None,
        competition_name=row["league_name"] or "",
        competition_country=row["country"],
        home_team=str(row["home_team_name"]),
        away_team=str(row["away_team_name"]),
        home_team_id=str(row["home_team_id"]) if row["home_team_id"] is not None else None,
        away_team_id=str(row["away_team_id"]) if row["away_team_id"] is not None else None,
        status=row["match_status"],
    )


@dataclass(slots=True)
class ResolverResult:
    internal_match_id: str
    match_result: MatchMatchResult
    candidates: list[FotMobMatch]
    mapping: Any | None = None

    @property
    def provider_match_id(self) -> str | None:
        return self.match_result.provider_match_id


class FotMobTipicoResolver:
    """Resolve one Tipico event against one mapped FotMob competition.

    The resolver is intentionally index-only: it never performs a provider
    detail request and never broadens a German competition to Austria (or the
    other way around).  Once a confirmed link exists it is returned as-is so
    minute-by-minute calls cannot silently rematch the event.
    """

    def __init__(
        self,
        service: "FotMobService",
        *,
        history_store: FotMobHistoryStore | None = None,
        mappings: tuple[CompetitionMapping, ...] = DEFAULT_COMPETITION_MAPPINGS,
    ) -> None:
        self.service = service
        self.history_store = history_store or FotMobHistoryStore(
            service.store.database,
            service.settings.archive_path,
        )
        self.mappings = mappings

    def seed_default_competition_links(self) -> int:
        for mapping in self.mappings:
            self.service.store.upsert_competition_provider_link(
                internal_competition_id=mapping.internal_competition_id,
                provider="FOTMOB",
                provider_competition_id=mapping.provider_competition_id,
                tipico_competition_name=mapping.tipico_competition_name,
                tipico_country=mapping.tipico_country,
                provider_competition_name=mapping.provider_competition_name,
                provider_country=mapping.provider_country,
                confidence=mapping.confidence,
                match_status=mapping.match_status,
                source=mapping.source,
                verified_at=datetime.now(timezone.utc).isoformat(),
            )
        return len(self.mappings)

    def mapping_for_event(self, event: Any) -> Any | None:
        competition_id = getattr(event, "competition_id", None)
        if competition_id is None:
            return None
        mapping = self.service.store.competition_link_for_internal(str(competition_id))
        if mapping is None:
            for candidate in self.mappings:
                if candidate.internal_competition_id == str(competition_id):
                    self.seed_default_competition_links()
                    mapping = self.service.store.competition_link_for_internal(str(competition_id))
                    break
        if mapping is None or mapping["match_status"] not in CONFIRMED_LINK_STATUSES:
            return None
        event_country = normalize_country(getattr(event, "competition_country", None))
        mapped_country = normalize_country(mapping["tipico_country"])
        if event_country and mapped_country and event_country != mapped_country:
            return None
        event_name = normalize_competition_name(getattr(event, "competition_name", ""))
        mapped_name = normalize_competition_name(mapping["tipico_competition_name"])
        if event_name and mapped_name and event_name != mapped_name:
            return None
        return mapping

    def candidate_rows(self, event: Any) -> list[Any]:
        mapping = self.mapping_for_event(event)
        if mapping is None:
            return []
        event_time = _parse_time(getattr(event, "kickoff_time", None))
        if event_time is None:
            return []
        tolerance = max(1, int(self.service.settings.fotmob_matching_tolerance_minutes))
        rows = []
        for row in self.history_store.match_index_for_league(
            str(mapping["provider_competition_id"]),
        ):
            kickoff = _parse_time(row["kickoff_at"])
            if kickoff is None:
                continue
            delta = abs((event_time - kickoff).total_seconds()) / 60
            if delta <= tolerance:
                rows.append(row)
        return rows

    def candidates(self, event: Any) -> list[FotMobMatch]:
        return [_row_to_match(row) for row in self.candidate_rows(event)]

    def resolve(self, event: Any) -> ResolverResult:
        internal_id = self.service.ensure_tipico_event(event)
        existing = self.service.store.link_for_internal(internal_id)
        if (
            existing is not None
            and existing["provider"] == "FOTMOB"
            and existing["match_status"] in CONFIRMED_LINK_STATUSES
            and existing["provider_match_id"]
        ):
            result = MatchMatchResult(
                status=str(existing["match_status"]),
                confidence=float(existing["match_confidence"]),
                provider_match_id=str(existing["provider_match_id"]),
                reasons=["persisted_link_no_rematch"],
            )
            return ResolverResult(internal_id, result, [], self.mapping_for_event(event))

        mapping = self.mapping_for_event(event)
        if mapping is None:
            result = MatchMatchResult(
                status="UNMATCHED",
                confidence=0.0,
                provider_match_id=None,
                reasons=["competition_provider_mapping_missing_or_country_mismatch"],
            )
            return ResolverResult(internal_id, result, [], None)
        candidates = self.candidates(event)
        result = self.service.match_tipico_event(event, candidates)
        return ResolverResult(internal_id, result, candidates, mapping)

    def resolve_many(self, events: list[Any]) -> list[ResolverResult]:
        return [self.resolve(event) for event in events]


__all__ = [
    "CONFIRMED_LINK_STATUSES",
    "CompetitionMapping",
    "DEFAULT_COMPETITION_MAPPINGS",
    "FotMobTipicoResolver",
    "ResolverResult",
]
