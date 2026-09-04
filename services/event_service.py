"""Live overview polling and event-state persistence."""

from __future__ import annotations

import logging
import time
from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from contextlib import nullcontext
from typing import Any

from config import Settings
from models.event import LiveEvent
from storage.database import ACTIVE_EVENT_STATUSES, Database
from storage.raw_storage import RawStorage
from storage.repositories import EventRepository
from tipico.client import ApiResponse, RequestMetrics, TipicoApiError, TipicoClient
from tipico.parser import parse_live_feed
from telemetry import SlowOperationTelemetry


@dataclass(slots=True)
class EventRefreshResult:
    success: bool
    events: list[LiveEvent]
    metrics: RequestMetrics | None = None
    error: str | None = None
    raw_path: str | None = None
    changed_state_count: int = 0
    history_state_count: int = 0
    reconciled_event_ids: tuple[str, ...] = ()
    ignored_event_ids: tuple[str, ...] = ()
    network_ms: float = 0.0
    parse_ms: float = 0.0
    persistence_ms: float = 0.0
    reconciliation_ms: float = 0.0
    sql_metrics: dict[str, int] | None = None
    feed_state: str | None = None


FEED_RECONCILIATION_STATES = frozenset(
    {
        "STARTUP_UNKNOWN",
        "ACTIVE_CONFIRMED",
        "ACTIVE_UNCONFIRMED",
        "STALE_SUSPECTED",
        "STALE_RECONCILED",
        "PROVIDER_FEED_INVALID",
    }
)


def _has_provider_error(payload: dict[str, Any], live: dict[str, Any]) -> bool:
    for container in (payload, live):
        for key in ("error", "errors", "errorCode", "providerError", "provider_error"):
            value = container.get(key)
            if value not in (None, "", [], {}, False, 0):
                return True
        status = str(container.get("status") or "").strip().casefold()
        if status in {"error", "failed", "failure"}:
            return True
    return False


def _feed_item_id(item: Any) -> str | None:
    """Extract an event id from both compact and expanded soccer indexes."""

    if item is None:
        return None
    if isinstance(item, dict):
        for key in ("id", "eventId", "event_id", "matchId", "match_id"):
            value = item.get(key)
            if value not in (None, ""):
                return str(value)
        return None
    value = str(item).strip()
    return value or None


def is_plausible_live_feed(
    payload: dict[str, Any],
    parsed_events: list[LiveEvent],
    *,
    persisted_active_count: int = 0,
    previous_active_count: int = 0,
    allow_stale_reconciliation: bool = False,
) -> tuple[bool, str | None]:
    """Validate the feed globally before it can trigger reconciliation.

    ``persisted_active_count`` protects the first poll after a restart.  The
    in-memory count is equally important afterwards: an empty but otherwise
    well-shaped provider response must never turn every currently observed
    event into ``NO_LONGER_LIVE``.
    """

    live = payload.get("LIVE") if isinstance(payload, dict) else None
    if not isinstance(live, dict):
        return False, "expected LIVE object is missing"
    if _has_provider_error(payload, live):
        return False, "provider-error payload"
    events = live.get("events")
    events_by_sport = live.get("eventsBySport")
    if not isinstance(events, dict):
        return False, "LIVE.events is missing or not an object"
    if not isinstance(events_by_sport, dict):
        return False, "LIVE.eventsBySport is missing or not an object"
    # Tipico omits the soccer bucket when the live universe currently only
    # contains other sports (for example tennis).  That is a valid no-soccer
    # state, not a malformed response.  Once soccer events were active,
    # however, an absent bucket is unsafe because it could terminalize the
    # persisted/in-memory universe; keep the reconciliation guard below
    # strict in that case.
    active_reference = max(int(persisted_active_count), int(previous_active_count))
    soccer_index_present = "soccer" in events_by_sport
    soccer_items = events_by_sport.get("soccer", [])
    if soccer_index_present and not isinstance(soccer_items, (list, tuple)):
        return False, "LIVE.eventsBySport.soccer is present but not a list"
    if not soccer_index_present and active_reference > 0 and not allow_stale_reconciliation:
        return False, "soccer index missing while previously active events exist"

    soccer_ids = {
        item_id
        for item in soccer_items
        if (item_id := _feed_item_id(item)) is not None
    }
    event_ids = {str(key) for key in events}
    for item in events.values():
        item_id = _feed_item_id(item)
        if item_id is not None:
            event_ids.add(item_id)
    if soccer_ids and not soccer_ids.issubset(event_ids):
        return False, "soccer index references missing event objects"
    if events and soccer_index_present and not soccer_ids and parsed_events:
        return False, "non-empty event map has no soccer index"

    parsed_ids = {str(event.event_id) for event in parsed_events}
    if soccer_ids and not soccer_ids.issubset(parsed_ids):
        return False, "soccer event could not be parsed completely"
    # A structurally valid empty/no-soccer feed is safe only when no
    # previously active event would be globally terminalized by that
    # emptiness.
    if active_reference > 0 and not parsed_events and not allow_stale_reconciliation:
        return False, "empty feed while previously active events exist"
    # A non-empty response can still be a truncated provider payload.  Keep
    # the threshold deliberately conservative so normal match completion and
    # staggered league updates remain valid, while a collapse of a sizeable
    # live universe cannot silently reconcile hundreds of events away.
    if active_reference >= 20 and len(parsed_events) < max(1, active_reference // 10) and not allow_stale_reconciliation:
        return False, (
            "implausible live event-count collapse "
            f"({len(parsed_events)} parsed vs {active_reference} previously active)"
        )
    return True, None


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class EventService:
    """Owns the current live-event snapshot and its persistence side effects."""

    def __init__(
        self,
        client: TipicoClient,
        database: Database,
        raw_storage: RawStorage,
        settings: Settings,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self.client = client
        self.database = database
        self.raw_storage = raw_storage
        self.settings = settings
        self.logger = logger or logging.getLogger("tipico")
        self.repository = EventRepository(database)
        self._events: dict[str, LiveEvent] = {}
        self.last_success_at: str | None = None
        self.last_error: str | None = None
        self.last_metrics: RequestMetrics | None = None
        self.request_count = 0
        self.error_count = 0
        self.parse_error_count = 0
        self.plausibility_error_count = 0
        self.persistence_error_count = 0
        self._startup_reconciliation_done = False
        self._feed_state = "STARTUP_UNKNOWN"
        self._feed_state_since = time.monotonic()
        self._stale_candidate_since: float | None = None
        self._structural_success_count = 0
        self._last_structural_signature: str | None = None
        self._feed_observations: deque[tuple[float, str]] = deque(maxlen=2000)
        self.feed_network_errors = 0
        self.feed_parse_errors = 0
        self.feed_provider_errors = 0
        self.feed_plausibility_rejects = 0
        self.feed_reconciliation_events = 0
        self.stale_state_reconciliations = 0
        self.last_feed_failure_kind: str | None = None
        self.last_reconciliation_reason: str | None = None
        self.last_timing: dict[str, float] = {}
        self.last_sql_metrics: dict[str, int] = {}
        self._slow_telemetry = SlowOperationTelemetry(
            threshold_ms=float(getattr(settings, "slow_operation_threshold_ms", 500.0))
        )

    @property
    def events(self) -> list[LiveEvent]:
        return list(self._events.values())

    @property
    def last_success_datetime(self) -> datetime | None:
        return _parse_iso(self.last_success_at)

    @property
    def data_age_seconds(self) -> float | None:
        moment = self.last_success_datetime
        if moment is None:
            return None
        return max(0.0, (datetime.now(timezone.utc) - moment.astimezone(timezone.utc)).total_seconds())

    @property
    def is_stale(self) -> bool:
        age = self.data_age_seconds
        return age is not None and age > self.settings.stale_overview_seconds

    @property
    def feed_state(self) -> str:
        return self._feed_state

    def _set_feed_state(self, state: str, *, reason: str | None = None) -> None:
        normalized = str(state).upper()
        if normalized not in FEED_RECONCILIATION_STATES:
            normalized = "PROVIDER_FEED_INVALID"
        if normalized != self._feed_state:
            self._feed_state_since = time.monotonic()
        self._feed_state = normalized
        if reason:
            self.last_reconciliation_reason = str(reason)

    def _record_feed_observation(self, kind: str) -> None:
        now = time.monotonic()
        self._feed_observations.append((now, str(kind).upper()))
        cutoff = now - 3600.0
        while self._feed_observations and self._feed_observations[0][0] < cutoff:
            self._feed_observations.popleft()

    def feed_window_metrics(self) -> dict[str, Any]:
        now = time.monotonic()
        result: dict[str, Any] = {}
        for window in (300, 900, 3600):
            counts: Counter[str] = Counter(
                kind for moment, kind in self._feed_observations if now - moment <= window
            )
            suffix = f"{window // 60}m"
            result[f"{suffix}_total"] = int(sum(counts.values()))
            for kind in (
                "NETWORK_ERROR",
                "PARSE_ERROR",
                "PROVIDER_ERROR",
                "PLAUSIBILITY_REJECT",
                "VALID_SUCCESS",
                "RECONCILIATION",
            ):
                result[f"{suffix}_{kind.casefold()}_count"] = int(counts.get(kind, 0))
            total = max(1, int(sum(counts.values())))
            result[f"{suffix}_error_rate"] = float(
                sum(counts.get(kind, 0) for kind in ("NETWORK_ERROR", "PARSE_ERROR", "PROVIDER_ERROR"))
                / total
            )
            result[f"{suffix}_plausibility_reject_rate"] = float(
                counts.get("PLAUSIBILITY_REJECT", 0) / total
            )
            # Keep an operator-friendly nested shape in addition to the
            # compact metric names used by the existing dashboard.
            result[f"last_{suffix}"] = {
                "total": int(sum(counts.values())),
                "feed_network_errors": int(counts.get("NETWORK_ERROR", 0)),
                "feed_parse_errors": int(counts.get("PARSE_ERROR", 0)),
                "feed_provider_errors": int(counts.get("PROVIDER_ERROR", 0)),
                "feed_plausibility_rejects": int(counts.get("PLAUSIBILITY_REJECT", 0)),
                "feed_reconciliation_events": int(counts.get("RECONCILIATION", 0)),
                "error_rate": result[f"{suffix}_error_rate"],
                "plausibility_reject_rate": result[f"{suffix}_plausibility_reject_rate"],
            }
        return result

    def reconciliation_status(self) -> dict[str, Any]:
        return {
            "state": self._feed_state,
            "state_since_monotonic": self._feed_state_since,
            "startup_reconciliation_done": bool(self._startup_reconciliation_done),
            "structural_success_count": int(self._structural_success_count),
            "stale_candidate_age_seconds": (
                max(0.0, time.monotonic() - self._stale_candidate_since)
                if self._stale_candidate_since is not None
                else None
            ),
            "last_failure_kind": self.last_feed_failure_kind,
            "last_reason": self.last_reconciliation_reason,
            "feed_network_errors": int(self.feed_network_errors),
            "feed_parse_errors": int(self.feed_parse_errors),
            "feed_provider_errors": int(self.feed_provider_errors),
            "feed_plausibility_rejects": int(self.feed_plausibility_rejects),
            "feed_reconciliation_events": int(self.feed_reconciliation_events),
            "stale_state_reconciliations": int(self.stale_state_reconciliations),
            "windows": self.feed_window_metrics(),
        }

    def _can_reconcile_stale(self) -> bool:
        if self._stale_candidate_since is None:
            return False
        minimum_observations = max(
            1,
            int(getattr(self.settings, "feed_stale_reconciliation_min_observations", 3)),
        )
        minimum_seconds = max(
            0.0,
            float(getattr(self.settings, "feed_stale_reconciliation_min_seconds", 60.0)),
        )
        elapsed = time.monotonic() - self._stale_candidate_since
        return self._structural_success_count >= minimum_observations and elapsed >= minimum_seconds

    def should_refresh(self) -> bool:
        if self.last_success_at is None:
            return True
        age = self.data_age_seconds
        return age is None or age >= self.settings.live_event_refresh_seconds

    def refresh_if_due(self) -> EventRefreshResult | None:
        if not self.should_refresh():
            return None
        return self.refresh()

    def _persist_raw(self, response: ApiResponse, observed_at: str) -> str | None:
        if not self.settings.raw_every_poll:
            return None
        try:
            result = self.raw_storage.store(
                "live",
                "live",
                response.payload,
                observed_at=observed_at,
            )
            if result.changed and result.path:
                self.logger.debug("Stored changed live raw payload: %s", result.path)
                return str(result.path)
        except OSError as exc:
            self.logger.warning("Could not store live raw payload: %s", exc)
        return None

    def _persist_debug_raw(self, response: ApiResponse, observed_at: str) -> None:
        """Keep parser-failure payloads for short-lived mapping diagnostics."""

        try:
            self.raw_storage.store(
                "debug",
                "live",
                response.payload,
                observed_at=observed_at,
            )
        except OSError as exc:
            self.logger.warning("Could not store live parser-error payload: %s", exc)

    def refresh(self) -> EventRefreshResult:
        self.request_count += 1
        response: ApiResponse | None = None
        raw_path: str | None = None
        network_started = time.perf_counter()
        try:
            response = self.client.get_live_football_events()
            self.last_metrics = response.metrics
            observed_at = response.metrics.response_received_at
            network_ms = (time.perf_counter() - network_started) * 1000.0
            raw_path = self._persist_raw(response, observed_at)
            parse_started = time.perf_counter()
            if not isinstance(response.payload, dict):
                raise ValueError("Tipico live response root is not an object")
            live = response.payload.get("LIVE")
            if not isinstance(live, dict):
                raise ValueError("Tipico live response did not contain LIVE")
            if _has_provider_error(response.payload, live):
                raise ValueError("Tipico live response contained a provider error")
            parsed_events = parse_live_feed(response.payload, logger=self.logger)
            parse_ms = (time.perf_counter() - parse_started) * 1000.0
        except TipicoApiError as exc:
            self.error_count += 1
            self.feed_network_errors += 1
            self.last_feed_failure_kind = "NETWORK_ERROR"
            self._record_feed_observation("NETWORK_ERROR")
            self._set_feed_state(
                "STALE_SUSPECTED" if self._events else "PROVIDER_FEED_INVALID",
                reason="network error; persisted active state retained",
            )
            self.last_error = str(exc)
            self.logger.error("Live feed request failed: %s", exc)
            self.last_timing = {
                "network_ms": (time.perf_counter() - network_started) * 1000.0,
                "parse_ms": 0.0,
                "persistence_ms": 0.0,
                "reconciliation_ms": 0.0,
            }
            return EventRefreshResult(
                success=False,
                events=self.events,
                metrics=exc.metrics,
                error=str(exc),
                network_ms=self.last_timing["network_ms"],
                feed_state=self.feed_state,
            )
        except (TypeError, ValueError, KeyError) as exc:
            self.parse_error_count += 1
            provider_failure = False
            if response is not None and isinstance(response.payload, dict):
                live_payload = response.payload.get("LIVE")
                if isinstance(live_payload, dict) and _has_provider_error(response.payload, live_payload):
                    provider_failure = True
                    self.feed_provider_errors += 1
                    self.last_feed_failure_kind = "PROVIDER_ERROR"
                    self._record_feed_observation("PROVIDER_ERROR")
            if not provider_failure:
                self.feed_parse_errors += 1
                self.last_feed_failure_kind = "PARSE_ERROR"
                self._record_feed_observation("PARSE_ERROR")
            self._set_feed_state(
                "STALE_SUSPECTED" if self._events else "PROVIDER_FEED_INVALID",
                reason=str(exc),
            )
            self.last_error = f"Live feed parse error: {exc}"
            if response is not None:
                self._persist_debug_raw(
                    response,
                    response.metrics.response_received_at,
                )
            self.logger.exception("Live feed parse error")
            self.last_timing = {
                "network_ms": (time.perf_counter() - network_started) * 1000.0,
                "parse_ms": 0.0,
                "persistence_ms": 0.0,
                "reconciliation_ms": 0.0,
            }
            return EventRefreshResult(
                success=False,
                events=self.events,
                metrics=self.last_metrics,
                error=self.last_error,
                network_ms=self.last_timing["network_ms"],
                feed_state=self.feed_state,
            )

        # A parsed response is structurally valid even if the plausibility
        # gate later rejects it.  Count these independent observations so a
        # persisted live universe can eventually be reconciled after a stale
        # provider state, rather than staying blocked indefinitely.
        self._record_feed_observation("STRUCTURAL_SUCCESS")
        self._structural_success_count += 1
        self.last_feed_failure_kind = None
        persisted_active_ids: list[str] = []
        if not self._startup_reconciliation_done:
            # This is deliberately after structure + parser validation.  A
            # malformed/empty error response must never trigger a global read
            # or reconciliation attempt.
            persisted_active_ids = self.database.current_event_ids_with_statuses(
                ACTIVE_EVENT_STATUSES
            )
        plausible, plausibility_reason = is_plausible_live_feed(
            response.payload,
            parsed_events,
            persisted_active_count=len(persisted_active_ids),
            previous_active_count=(
                len(self._events) if self._startup_reconciliation_done else 0
            ),
        )
        reconciled_stale = False
        if not plausible:
            self.plausibility_error_count += 1
            self.feed_plausibility_rejects += 1
            self._record_feed_observation("PLAUSIBILITY_REJECT")
            self.last_feed_failure_kind = "PLAUSIBILITY_REJECT"
            if self._stale_candidate_since is None:
                self._stale_candidate_since = time.monotonic()
            self._set_feed_state("STALE_SUSPECTED", reason=plausibility_reason)
            # Retest only after the response itself was structurally valid and
            # enough independent evidence has accumulated.  The ordinary
            # plausibility checks remain the default and still reject the
            # first suspicious empty/collapsed response.
            if self._can_reconcile_stale():
                plausible, _ = is_plausible_live_feed(
                    response.payload,
                    parsed_events,
                    persisted_active_count=len(persisted_active_ids),
                    previous_active_count=(
                        len(self._events) if self._startup_reconciliation_done else 0
                    ),
                    allow_stale_reconciliation=True,
                )
                reconciled_stale = bool(plausible)
            if not plausible:
                self.last_reconciliation_reason = plausibility_reason
                self.last_timing = {
                    "network_ms": network_ms,
                    "parse_ms": parse_ms,
                    "persistence_ms": 0.0,
                    "reconciliation_ms": 0.0,
                }
                self._persist_debug_raw(response, response.metrics.response_received_at)
                self.last_error = f"Live feed plausibility gate rejected response: {plausibility_reason}"
                return EventRefreshResult(
                    success=False,
                    events=self.events,
                    metrics=response.metrics,
                    error=self.last_error,
                    network_ms=network_ms,
                    parse_ms=parse_ms,
                    feed_state=self.feed_state,
                )

        previous_ids = (
            set(persisted_active_ids)
            if not self._startup_reconciliation_done
            else set(self._events)
        )
        next_events: dict[str, LiveEvent] = {}
        for event in parsed_events:
            previous = self._events.get(event.event_id)
            if previous is None:
                event.first_seen_at = observed_at
                self.logger.info(
                    "New event detected: %s %s - %s",
                    event.event_id,
                    event.home_team,
                    event.away_team,
                )
            else:
                event.first_seen_at = previous.first_seen_at or observed_at
                if previous.score_label != event.score_label:
                    self.logger.info(
                        "Score change: %s %s -> %s",
                        event.event_id,
                        previous.score_label,
                        event.score_label,
                    )
                if previous.display_minute != event.display_minute:
                    self.logger.info(
                        "Event state: %s %s -> %s",
                        event.event_id,
                        previous.display_minute,
                        event.display_minute,
                    )
                if previous.status != event.status:
                    self.logger.info(
                        "Event status: %s %s -> %s",
                        event.event_id,
                        previous.status,
                        event.status,
                    )
            event.last_seen_at = observed_at
            event.last_updated_at = observed_at
            next_events[event.event_id] = event

        persistence_started = time.perf_counter()
        sql_context = (
            self.database.trace_sql()
            if getattr(self.settings, "collector_sql_trace_enabled", False)
            else nullcontext({})
        )
        try:
            with sql_context as sql_metrics:
                persisted = self.database.persist_live_feed_batch(
                    next_events.values(),
                    observed_at,
                    disappeared_ids=previous_ids - set(next_events),
                    now=datetime.now(timezone.utc),
                    stale_prematch_grace_hours=getattr(
                        self.settings,
                        "stale_prematch_grace_hours",
                        6.0,
                    ),
                )
        except Exception as exc:
            self.persistence_error_count += 1
            self.last_error = f"Live feed persistence failed: {exc}"
            self.last_timing = {
                "network_ms": network_ms,
                "parse_ms": parse_ms,
                "persistence_ms": (time.perf_counter() - persistence_started) * 1000.0,
                "reconciliation_ms": 0.0,
            }
            self.logger.exception("Live feed persistence failed")
            return EventRefreshResult(
                success=False,
                events=self.events,
                metrics=response.metrics,
                error=self.last_error,
                network_ms=network_ms,
                parse_ms=parse_ms,
                persistence_ms=self.last_timing["persistence_ms"],
            )

        persistence_ms = (time.perf_counter() - persistence_started) * 1000.0
        self._slow_telemetry.record(
            "db_persistence",
            persistence_ms,
            details={
                "rows": len(next_events),
                "rows_changed": int(persisted.get("rows_changed") or 0),
                "db_transactions": int(persisted.get("db_transactions") or 0),
                "db_busy_lock": self.persistence_error_count > 0,
            },
        )
        reconciled_ids = tuple(str(value) for value in persisted.get("reconciled_event_ids", ()))
        ignored_ids = tuple(str(value) for value in persisted.get("ignored_event_ids", ()))
        if ignored_ids:
            ignored_set = set(ignored_ids)
            # A terminal/stale event may still be present in a provider
            # response.  It must not leak back into the in-memory live
            # universe after the database correctly rejected its reopen.
            next_events = {
                event_id: event
                for event_id, event in next_events.items()
                if event_id not in ignored_set
            }
        for disappeared_id in reconciled_ids:
            self.logger.info("Event reconciled after leaving live feed: %s", disappeared_id)

        self._events = next_events
        self.last_success_at = observed_at
        self.last_error = None
        self._startup_reconciliation_done = True
        self._record_feed_observation("VALID_SUCCESS")
        if reconciled_stale:
            self.feed_reconciliation_events += 1
            self.stale_state_reconciliations += 1
            self._record_feed_observation("RECONCILIATION")
            self._set_feed_state("STALE_RECONCILED", reason=plausibility_reason)
            self._stale_candidate_since = None
            self._structural_success_count = 0
        elif next_events:
            self._set_feed_state("ACTIVE_CONFIRMED")
            self._stale_candidate_since = None
            self._structural_success_count = 0
        else:
            self._set_feed_state("ACTIVE_UNCONFIRMED")
            self._stale_candidate_since = None
            self._structural_success_count = 0
        self.last_sql_metrics = {
            str(key): int(value)
            for key, value in (sql_metrics or {}).items()
            if isinstance(value, int)
        }
        # These counters are available even when the optional SQLite trace is
        # disabled.  The live-feed batch is one logical transaction by
        # construction; the trace adds statement-level detail only when
        # explicitly requested.
        for key in ("db_transactions", "db_commits", "db_rollbacks", "rows_changed"):
            if key in persisted:
                self.last_sql_metrics[key] = int(persisted.get(key) or 0)
        reconciliation_ms = float(persisted.get("reconciliation_ms") or 0.0)
        self.last_timing = {
            "network_ms": network_ms,
            "parse_ms": parse_ms,
            "persistence_ms": persistence_ms,
            "reconciliation_ms": reconciliation_ms,
        }
        self.logger.info(
            "Live feed normalized: %s soccer events in %s ms",
            len(next_events),
            response.metrics.response_time_ms,
        )
        return EventRefreshResult(
            success=True,
            events=self.events,
            metrics=response.metrics,
            raw_path=raw_path,
            changed_state_count=int(persisted.get("changed_state_count") or 0),
            history_state_count=int(persisted.get("history_state_count") or 0),
            reconciled_event_ids=reconciled_ids,
            ignored_event_ids=ignored_ids,
            network_ms=network_ms,
            parse_ms=parse_ms,
            persistence_ms=persistence_ms,
            reconciliation_ms=reconciliation_ms,
            sql_metrics=self.last_sql_metrics or None,
            feed_state=self.feed_state,
        )

    def filtered_events(self, search: str = "") -> list[LiveEvent]:
        query = search.strip().casefold()
        events = self.events
        if query:
            events = [
                event
                for event in events
                if query in event.competition_name.casefold()
                or query in event.home_team.casefold()
                or query in event.away_team.casefold()
            ]

        def sort_key(event: LiveEvent) -> tuple[str, int, str, str]:
            display = event.display_minute.upper()
            if display == "HZ":
                state_rank = 1
            elif event.status.lower() in {"running", "live"}:
                state_rank = 0
            else:
                state_rank = 2
            return (
                event.competition_name.casefold(),
                state_rank,
                event.kickoff_time or "",
                event.event_id,
            )

        return sorted(events, key=sort_key)
