"""Safe Tipico-to-FotMob fixture resolution for the V0.5.3 HT path."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from .history_storage import FotMobHistoryStore
from .matching import (
    AUTO_LINK_STATUSES,
    MatchMatchResult,
    normalize_competition_name,
    normalize_country,
)
from .models import FotMobMatch

if TYPE_CHECKING:  # pragma: no cover - import only for type checkers
    from .service import FotMobService


CONFIRMED_LINK_STATUSES = AUTO_LINK_STATUSES


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


def _row_value(row: Any, name: str, default: Any = None) -> Any:
    """Read sqlite rows and small mapping fakes through one safe helper."""

    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(name, default)
    keys = getattr(row, "keys", None)
    if callable(keys):
        try:
            if name in keys():
                return row[name]
        except (TypeError, KeyError):
            pass
    return getattr(row, name, default)


def _row_to_match(row: Any) -> FotMobMatch:
    keys = set(row.keys()) if hasattr(row, "keys") else set()

    def value(*names: str, default: Any = None) -> Any:
        for name in names:
            if name in keys:
                return row[name]
        return default

    country = value("country_name", "country", "country_code")
    return FotMobMatch(
        provider_match_id=str(value("fotmob_match_id")),
        kickoff_at=value("kickoff_at", "kickoff_at_utc"),
        competition_id=(
            str(value("league_id")) if value("league_id") is not None else None
        ),
        competition_name=value("league_name", default="") or "",
        competition_country=country,
        home_team=str(value("home_team_name", default="")),
        away_team=str(value("away_team_name", default="")),
        home_team_id=(
            str(value("home_team_id")) if value("home_team_id") is not None else None
        ),
        away_team_id=(
            str(value("away_team_id")) if value("away_team_id") is not None else None
        ),
        season=str(value("season_label")) if value("season_label") is not None else None,
        round_name=value("round"),
        status=value("match_status"),
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

    def _record_resolution(self, result: ResolverResult) -> ResolverResult:
        recorder = getattr(self.service, "record_link_resolution", None)
        if callable(recorder):
            recorder(result.match_result)
        return result

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
        mapping = (
            self.service.store.competition_link_for_internal(str(competition_id))
            if competition_id is not None
            else None
        )
        if mapping is None:
            for candidate in self.mappings:
                if competition_id is not None and candidate.internal_competition_id == str(competition_id):
                    self.seed_default_competition_links()
                    mapping = self.service.store.competition_link_for_internal(str(competition_id))
                    break
        # A prior confirmed match can teach the resolver a competition mapping
        # even when the current Tipico feed does not expose a stable numeric
        # competition id.  The name/country check stays exact and scoped.
        if mapping is None and hasattr(self.service.store, "competition_links"):
            event_name = normalize_competition_name(getattr(event, "competition_name", ""))
            event_country = normalize_country(getattr(event, "competition_country", None))
            for candidate in self.service.store.competition_links("FOTMOB"):
                candidate_name = normalize_competition_name(candidate["tipico_competition_name"])
                candidate_country = normalize_country(candidate["tipico_country"])
                if (
                    event_name
                    and candidate_name == event_name
                    and (not event_country or not candidate_country or event_country == candidate_country)
                    and candidate["match_status"] in CONFIRMED_LINK_STATUSES
                ):
                    mapping = candidate
                    break
        if mapping is None or str(mapping["match_status"]).upper() not in CONFIRMED_LINK_STATUSES:
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

    @staticmethod
    def _event_dates(event: Any) -> tuple[date, ...]:
        event_time = _parse_time(getattr(event, "kickoff_time", None))
        if event_time is None:
            return ()
        center = event_time.date()
        return tuple(center + timedelta(days=offset) for offset in (-1, 0, 1))

    def _daily_rows(
        self,
        event: Any,
        *,
        force_network: bool = False,
    ) -> list[Any]:
        dates = self._event_dates(event)
        if not dates:
            return []
        network_mode = str(getattr(self.service, "network_mode", "off")).casefold()
        allow_network = bool(
            (network_mode == "worker" and getattr(self.service, "automated_worker_allowed", False))
            or (network_mode == "manual" and getattr(self.service, "manual_use_allowed", False))
        )
        pipeline = getattr(self.service, "history_pipeline", None)
        rows_by_key: dict[tuple[str, str], Any] = {}
        for day in dates:
            day_text = day.isoformat()
            rows = self.history_store.daily_index(
                start_date=day_text,
                end_date=day_text,
                limit=20000,
                order_by="kickoff_at_utc",
                ascending=True,
            )
            if pipeline is not None and allow_network and (not rows or force_network):
                try:
                    pipeline.load_daily_fixture_index(
                        day,
                        allow_network=True,
                        force=force_network,
                    )
                except (AttributeError, TypeError, ValueError, OSError, RuntimeError):
                    # Provider discovery is optional; the caller still gets
                    # the durable index and can use an explicit manual link.
                    pass
                rows = self.history_store.daily_index(
                    start_date=day_text,
                    end_date=day_text,
                    limit=20000,
                    order_by="kickoff_at_utc",
                    ascending=True,
                )
            for row in rows:
                key = (day_text, str(row["fotmob_match_id"]))
                rows_by_key[key] = row
        return list(rows_by_key.values())

    def _matches_event_scope(self, event: Any, row: Any, mapping: Any | None) -> bool:
        if mapping is not None and str(row["league_id"]) != str(mapping["provider_competition_id"]):
            return False
        if mapping is None:
            event_competition = normalize_competition_name(
                getattr(event, "competition_name", "")
            )
            row_competition = normalize_competition_name(row["league_name"] or "")
            if not event_competition or event_competition != row_competition:
                return False
            event_country = normalize_country(getattr(event, "competition_country", None))
            row_country = normalize_country(row["country_name"] or row["country_code"])
            if event_country and row_country and event_country != row_country:
                return False
        return True

    def candidate_rows(self, event: Any) -> list[Any]:
        mapping = self.mapping_for_event(event)
        event_time = _parse_time(getattr(event, "kickoff_time", None))
        if event_time is None:
            return []
        tolerance = max(1, int(self.service.settings.fotmob_matching_tolerance_minutes))
        rows = [
            row for row in self._daily_rows(event)
            if self._matches_event_scope(event, row, mapping)
        ]
        # Existing season indexes remain a safe compatibility fallback only
        # when a persistent competition mapping already narrows the pool.
        if not rows and mapping is not None:
            rows = [
                row for row in self.history_store.match_index_for_league(
                    str(mapping["provider_competition_id"]),
                )
            ]
        # If a current-day catalog was warm but did not yet contain a match,
        # perform one controlled daily-feed refresh.  The pipeline cache makes
        # this at most one request per day per TTL, not one request per event.
        if not rows and getattr(self.service, "automated_worker_allowed", False):
            rows = [
                row for row in self._daily_rows(event, force_network=True)
                if self._matches_event_scope(event, row, mapping)
            ]
        candidates = []
        for row in rows:
            kickoff_value = (
                row["kickoff_at"]
                if "kickoff_at" in row.keys()
                else row["kickoff_at_utc"]
            )
            kickoff = _parse_time(kickoff_value)
            if kickoff is None:
                continue
            delta = abs((event_time - kickoff).total_seconds()) / 60
            if delta <= tolerance:
                candidates.append(row)
        return candidates

    def candidates(self, event: Any) -> list[FotMobMatch]:
        return [_row_to_match(row) for row in self.candidate_rows(event)]

    def resolve(self, event: Any) -> ResolverResult:
        internal_id = self.service.ensure_tipico_event(event)
        existing = None
        direct_link = getattr(self.service, "provider_event_link_for_event", None)
        if callable(direct_link):
            existing = direct_link(event)
        if existing is None:
            existing = self.service.store.link_for_internal(internal_id)
        existing_status = str(_row_value(existing, "match_status", "")).upper()
        existing_method = str(_row_value(existing, "match_method", "")).upper()
        existing_reason = str(_row_value(existing, "reason", "")).casefold()
        if existing_status in {"INVALIDATED", "REJECTED"} and (
            existing_method.startswith("MANUAL") or "manual" in existing_reason
        ):
            # A deliberate user rejection is a durable safety decision.  Do
            # not let a later daily-index refresh silently turn it back into a
            # provider link; the user can explicitly confirm a new match.
            result = MatchMatchResult(
                status="INVALIDATED",
                confidence=float(_row_value(existing, "match_confidence", 0.0) or 0.0),
                provider_match_id=(
                    str(_row_value(existing, "fotmob_match_id"))
                    if _row_value(existing, "fotmob_match_id") not in (None, "")
                    else (
                        str(_row_value(existing, "provider_match_id"))
                        if _row_value(existing, "provider_match_id") not in (None, "")
                        else None
                    )
                ),
                reasons=["persisted_manual_invalidation"],
            )
            return self._record_resolution(
                ResolverResult(
                    internal_id,
                    result,
                    [],
                    self.mapping_for_event(event),
                )
            )
        if (
            existing is not None
            and str(_row_value(existing, "provider", "FOTMOB")).upper() == "FOTMOB"
            and existing_status in CONFIRMED_LINK_STATUSES
            and (
                _row_value(existing, "fotmob_match_id")
                if _row_value(existing, "fotmob_match_id") not in (None, "")
                else _row_value(existing, "provider_match_id")
            )
        ):
            provider_match_id = (
                _row_value(existing, "fotmob_match_id")
                if _row_value(existing, "fotmob_match_id") not in (None, "")
                else _row_value(existing, "provider_match_id")
            )
            result = MatchMatchResult(
                status=existing_status,
                confidence=float(_row_value(existing, "match_confidence", 0.0) or 0.0),
                provider_match_id=str(provider_match_id),
                reasons=["persisted_link_no_rematch"],
            )
            return self._record_resolution(
                ResolverResult(internal_id, result, [], self.mapping_for_event(event))
            )

        mapping = self.mapping_for_event(event)
        # A known mapping that fails its country/name guard is a hard reject;
        # otherwise a competition may be new to Tipico and still be safely
        # resolved from the daily index by exact competition/country scope.
        competition_id = getattr(event, "competition_id", None)
        known_mapping = (
            self.service.store.competition_link_for_internal(str(competition_id))
            if competition_id is not None
            else None
        )
        if mapping is None and known_mapping is not None:
            result = MatchMatchResult(
                status="UNMATCHED",
                confidence=0.0,
                provider_match_id=None,
                reasons=["competition_provider_mapping_missing_or_country_mismatch"],
            )
            return self._record_resolution(ResolverResult(internal_id, result, [], None))
        # A competition without a learned provider mapping is still eligible
        # for safe discovery. ``candidate_rows`` narrows the daily index by
        # the exact normalized competition and country before team/kickoff
        # scoring, so this does not become a global fuzzy search.
        candidates = self.candidates(event)
        result = self.service.match_tipico_event(
            event,
            candidates,
            _record_metrics=False,
        )
        return self._record_resolution(ResolverResult(internal_id, result, candidates, mapping))

    def resolve_many(self, events: list[Any]) -> list[ResolverResult]:
        return [self.resolve(event) for event in events]


__all__ = [
    "CONFIRMED_LINK_STATUSES",
    "CompetitionMapping",
    "DEFAULT_COMPETITION_MAPPINGS",
    "FotMobTipicoResolver",
    "ResolverResult",
]
