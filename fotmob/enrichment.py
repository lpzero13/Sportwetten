"""Safe Tipico-to-FotMob fixture resolution for the V0.5.3 HT path."""

from __future__ import annotations

import hashlib
import json
import threading
import time
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
    from_cache: bool = False
    cache_state: str | None = None

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
        self._negative_cache: dict[tuple[str, str], tuple[float, ResolverResult, int]] = {}
        self._negative_cache_lock = threading.RLock()

    def _daily_index_generation(self) -> int:
        """Read the shared index generation without issuing a network call."""

        pipeline = getattr(self.service, "history_pipeline", None)
        metrics_reader = getattr(pipeline, "runtime_metrics", None)
        if not callable(metrics_reader):
            return 0
        try:
            return int(metrics_reader().get("daily_index_generation", 0) or 0)
        except (AttributeError, TypeError, ValueError):
            return 0

    @staticmethod
    def resolver_input_fingerprint(event: Any) -> str:
        """Hash the identity fields that can change a provider match."""

        kickoff = _parse_time(getattr(event, "kickoff_time", None))
        identity = {
            "competition": str(getattr(event, "competition_name", "") or "").strip().casefold(),
            "country": normalize_country(getattr(event, "competition_country", None)),
            "home": str(getattr(event, "home_team", "") or "").strip().casefold(),
            "away": str(getattr(event, "away_team", "") or "").strip().casefold(),
            "kickoff": kickoff.isoformat() if kickoff is not None else None,
        }
        return hashlib.sha256(
            json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    def _negative_ttl(self, state: str) -> float:
        setting_name = {
            "NO_CANDIDATE": "fotmob_negative_resolve_no_candidate_ttl_seconds",
            "AMBIGUOUS": "fotmob_negative_resolve_ambiguous_ttl_seconds",
            "NO_DATA": "fotmob_negative_resolve_no_data_ttl_seconds",
        }.get(state, "fotmob_negative_resolve_no_candidate_ttl_seconds")
        defaults = {"NO_CANDIDATE": 600.0, "AMBIGUOUS": 300.0, "NO_DATA": 1800.0}
        return max(0.0, float(getattr(self.service.settings, setting_name, defaults.get(state, 600.0))))

    def _invalidate_material_changes(self, internal_id: str, fingerprint: str) -> None:
        removed = 0
        with self._negative_cache_lock:
            for key in list(self._negative_cache):
                if key[0] == internal_id and key[1] != fingerprint:
                    self._negative_cache.pop(key, None)
                    removed += 1
        recorder = getattr(self.service, "record_resolver_cache_invalidation", None)
        if removed and callable(recorder):
            recorder()

    def _negative_cache_get(
        self,
        internal_id: str,
        fingerprint: str,
        *,
        trigger: str,
        generation: int = 0,
    ) -> ResolverResult | None:
        bypass_triggers = {
            "PREMATCH", "LIVE_START", "HALFTIME", "HT_STABLE", "SELECTED",
            "MATERIAL_CHANGE", "DAILY_INDEX_REFRESH", "FORCE",
        }
        if str(trigger or "POLL").upper() in bypass_triggers:
            return None
        with self._negative_cache_lock:
            value = self._negative_cache.get((internal_id, fingerprint))
            if value is None:
                return None
            expires_at, result, cached_generation = value
            if expires_at <= time.monotonic() or (
                generation > 0 and cached_generation < generation
            ):
                self._negative_cache.pop((internal_id, fingerprint), None)
                if cached_generation < generation:
                    recorder = getattr(self.service, "record_resolver_cache_invalidation", None)
                    if callable(recorder):
                        recorder()
                return None
        recorder = getattr(self.service, "record_resolver_cache_hit", None)
        if callable(recorder):
            recorder()
        return ResolverResult(
            internal_id,
            result.match_result,
            list(result.candidates),
            result.mapping,
            from_cache=True,
            cache_state=result.cache_state,
        )

    def _negative_cache_put(
        self,
        internal_id: str,
        fingerprint: str,
        result: ResolverResult,
        state: str,
    ) -> ResolverResult:
        value = ResolverResult(
            internal_id,
            result.match_result,
            list(result.candidates),
            result.mapping,
            cache_state=state,
        )
        ttl = self._negative_ttl(state)
        if ttl > 0:
            with self._negative_cache_lock:
                self._negative_cache[(internal_id, fingerprint)] = (
                    time.monotonic() + ttl,
                    value,
                    self._daily_index_generation(),
                )
        return value

    def _stored_identity_changed(self, existing: Any, event: Any) -> bool:
        """Cheap raw-field check before paying for full revalidation."""

        stored_competition = _row_value(existing, "tipico_competition_id")
        current_competition = getattr(event, "competition_id", None)
        if stored_competition not in (None, "") and current_competition not in (None, ""):
            if str(stored_competition) != str(current_competition):
                return True
        for field, event_field in (("tipico_home_team", "home_team"), ("tipico_away_team", "away_team")):
            stored = _row_value(existing, field)
            current = getattr(event, event_field, None)
            if stored not in (None, "") and current not in (None, ""):
                if str(stored).strip().casefold() != str(current).strip().casefold():
                    return True
        stored_kickoff = _parse_time(_row_value(existing, "tipico_kickoff"))
        current_kickoff = _parse_time(getattr(event, "kickoff_time", None))
        if stored_kickoff is not None and current_kickoff is not None:
            tolerance = max(
                1,
                int(getattr(self.service.settings, "fotmob_matching_tolerance_minutes", 15)),
            )
            if abs((stored_kickoff - current_kickoff).total_seconds()) / 60 > tolerance:
                return True
        return False

    def _direct_link(self, event: Any, *, revalidate: bool) -> Any | None:
        direct_link = getattr(self.service, "provider_event_link_for_event", None)
        if not callable(direct_link):
            return None
        try:
            return direct_link(event, revalidate=revalidate)
        except TypeError as exc:
            if "unexpected keyword" not in str(exc):
                raise
            return direct_link(event)

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
        lookup_error = False
        for day in dates:
            day_text = day.isoformat()
            rows = self.history_store.daily_index(
                start_date=day_text,
                end_date=day_text,
                limit=20000,
                order_by="kickoff_at_utc",
                ascending=True,
            )
            if pipeline is not None and allow_network:
                try:
                    loaded = pipeline.load_daily_fixture_index(
                        day,
                        allow_network=True,
                        # Only an explicit refresh trigger may bypass the
                        # service-level TTL; a missing event never does so.
                        force=bool(force_network),
                    )
                    if isinstance(loaded, tuple) and len(loaded) > 1 and loaded[1]:
                        lookup_error = True
                except (AttributeError, TypeError, ValueError, OSError, RuntimeError):
                    # Provider discovery is optional; the caller still gets
                    # the durable index and can use an explicit manual link.
                    lookup_error = True
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
        self._last_daily_lookup_state = "NO_DATA" if lookup_error else "NO_CANDIDATE"
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

    def candidate_rows(
        self,
        event: Any,
        *,
        trigger: str = "POLL",
        force_refresh: bool = False,
    ) -> list[Any]:
        mapping = self.mapping_for_event(event)
        event_time = _parse_time(getattr(event, "kickoff_time", None))
        if event_time is None:
            return []
        tolerance = max(1, int(self.service.settings.fotmob_matching_tolerance_minutes))
        rows = [
            row for row in self._daily_rows(event, force_network=force_refresh)
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

    def candidates(
        self,
        event: Any,
        *,
        trigger: str = "POLL",
        force_refresh: bool = False,
    ) -> list[FotMobMatch]:
        return [
            _row_to_match(row)
            for row in self.candidate_rows(
                event,
                trigger=trigger,
                force_refresh=force_refresh,
            )
        ]

    def resolve(
        self,
        event: Any,
        *,
        trigger: str = "POLL",
        priority: str | None = None,
        force: bool = False,
    ) -> ResolverResult:
        started = time.perf_counter()
        try:
            return self._resolve_impl(
                event,
                trigger=trigger,
                priority=priority,
                force=force,
            )
        finally:
            telemetry = getattr(self.service, "_slow_telemetry", None)
            if telemetry is not None:
                telemetry.record(
                    "fotmob_resolver",
                    (time.perf_counter() - started) * 1000.0,
                    details={"trigger": str(trigger).upper(), "priority": priority},
                )

    def _resolve_impl(
        self,
        event: Any,
        *,
        trigger: str = "POLL",
        priority: str | None = None,
        force: bool = False,
    ) -> ResolverResult:
        internal_id = self.service.ensure_tipico_event(event)
        fingerprint = self.resolver_input_fingerprint(event)
        self._invalidate_material_changes(internal_id, fingerprint)
        existing = self._direct_link(event, revalidate=False)
        existing_status_hint = str(_row_value(existing, "match_status", "")).upper()
        if (
            existing is not None
            and existing_status_hint in CONFIRMED_LINK_STATUSES
            and self._stored_identity_changed(existing, event)
        ):
            existing = self._direct_link(event, revalidate=True)
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
            fast_path_recorder = getattr(self.service, "record_confirmed_link_fast_path", None)
            if callable(fast_path_recorder):
                fast_path_recorder()
            return self._record_resolution(
                ResolverResult(
                    internal_id,
                    result,
                    [],
                    None,
                    from_cache=True,
                    cache_state="CONFIRMED",
                )
            )

        cached = self._negative_cache_get(
            internal_id,
            fingerprint,
            trigger=trigger,
            generation=self._daily_index_generation(),
        )
        if cached is not None and not force:
            return self._record_resolution(cached)

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
            negative = ResolverResult(internal_id, result, [], None, cache_state="NO_CANDIDATE")
            return self._record_resolution(
                self._negative_cache_put(internal_id, fingerprint, negative, "NO_CANDIDATE")
            )
        # A competition without a learned provider mapping is still eligible
        # for safe discovery. ``candidate_rows`` narrows the daily index by
        # the exact normalized competition and country before team/kickoff
        # scoring, so this does not become a global fuzzy search.
        attempt_recorder = getattr(self.service, "record_resolver_attempt", None)
        if callable(attempt_recorder):
            attempt_recorder(candidate_scan=True, trigger=trigger)
        candidates = self.candidates(
            event,
            trigger=trigger,
            force_refresh=bool(
                force and str(trigger or "").upper() in {"FORCE", "DAILY_INDEX_REFRESH"}
            ),
        )
        result = self.service.match_tipico_event(
            event,
            candidates,
            _record_metrics=False,
        )
        state: str | None = None
        result_status = str(result.status or "").upper()
        if result_status == "AMBIGUOUS" or len(candidates) > 1:
            state = "AMBIGUOUS"
        elif not candidates and result_status not in CONFIRMED_LINK_STATUSES:
            state = getattr(self, "_last_daily_lookup_state", "NO_CANDIDATE")
        resolved = ResolverResult(internal_id, result, candidates, mapping, cache_state=state)
        if state:
            resolved = self._negative_cache_put(internal_id, fingerprint, resolved, state)
        return self._record_resolution(resolved)

    def resolve_many(self, events: list[Any]) -> list[ResolverResult]:
        return [self.resolve(event) for event in events]


__all__ = [
    "CONFIRMED_LINK_STATUSES",
    "CompetitionMapping",
    "DEFAULT_COMPETITION_MAPPINGS",
    "FotMobTipicoResolver",
    "ResolverResult",
]
