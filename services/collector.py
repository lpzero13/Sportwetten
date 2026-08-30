"""Independent background collector for Tipico historical observations.

The collector owns scheduling and persistence.  It does not import Streamlit
and can therefore run as a separate long-lived process while the dashboard is
closed or restarted.
"""

from __future__ import annotations

import heapq
import json
import logging
import re
import threading
import time
from collections import Counter, deque
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, median
from typing import TYPE_CHECKING, Any, Callable

from config import Settings
from intelligence.models import MarketAnalysis
from intelligence.service import MarketIntelligenceService
from models.event import LiveEvent
from models.market import EventDetails
from models.snapshot import Snapshot
from storage.database import Database
from storage.parquet_archive import ParquetArchive, build_snapshot_payload
from storage.raw_storage import RawStorage
from storage.repositories import MarketRepository
from tipico.client import ApiResponse, RequestMetrics, TipicoApiError, TipicoClient
from tipico.parser import parse_event_details, parse_upcoming_feed
from services.event_service import EventService, EventRefreshResult

if TYPE_CHECKING:  # pragma: no cover - import only for type checkers
    from fotmob.service import FotMobService


CORE_MARKET_TYPES = {
    "points-more-less-rest",
    "next-point",
    "team-points-more-less",
    "score-both",
    "points-more-less",
    # Tipico uses this related section type for some sport/competition variants.
    "section-points-more-less",
}

FINISHED_STATUSES = {"finished", "ended", "complete", "completed", "final"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_minute(value: str | None) -> int | None:
    if not value:
        return None
    match = re.search(r"(\d+)", value)
    return int(match.group(1)) if match else None


def _is_halftime(event: LiveEvent) -> bool:
    return (
        event.period.strip().upper() in {"HALF_TIME", "HALFTIME", "HT"}
        or event.display_minute.strip().upper() == "HZ"
    )


def _is_second_half(event: LiveEvent) -> bool:
    if _is_halftime(event):
        return False
    if event.section_number is not None and event.section_number >= 2:
        return True
    minute = _parse_minute(event.display_minute)
    return minute is not None and minute >= 46


def _is_finished(event: LiveEvent) -> bool:
    return event.status.strip().lower() in FINISHED_STATUSES or event.period.strip().upper() in {
        "FINISHED",
        "FINAL",
        "ENDED",
    }


def _quality(details: EventDetails) -> str:
    if details.market_count == 0 or details.outcome_count == 0:
        return "PARTIAL"
    if details.open_outcome_count == 0 and details.paused_outcome_count > 0:
        return "MOSTLY_PAUSED"
    if details.open_outcome_count < details.outcome_count / 2:
        return "PARTIAL"
    return "COMPLETE"


def _second_half_label(event: LiveEvent) -> tuple[int | None, str | None]:
    if (
        event.score_home is None
        or event.score_away is None
        or event.ht_score_home is None
        or event.ht_score_away is None
    ):
        return None, None
    goals = (event.score_home + event.score_away) - (
        event.ht_score_home + event.ht_score_away
    )
    if goals < 0:
        return None, None
    return goals, "2_PLUS" if goals >= 2 else str(goals)


@dataclass(slots=True)
class SnapshotJob:
    event_id: str
    snapshot_type: str
    trigger_reason: str
    due_at: float
    raw_full: bool
    fallback_event: LiveEvent | None = None
    sequence: int = 0
    goal_at: str | None = None
    reopen_delay_seconds: float | None = None

    @property
    def key(self) -> tuple[str, str, str, int]:
        return (self.event_id, self.snapshot_type, self.trigger_reason, self.sequence)


@dataclass(slots=True)
class DetailFetchResult:
    job: SnapshotJob
    success: bool
    details: EventDetails | None = None
    payload: dict | None = None
    metrics: RequestMetrics | None = None
    error: str | None = None
    api_error: bool = False
    attempts: int = 0
    attempt_metrics: list[RequestMetrics] = field(default_factory=list)
    attempt_kinds: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RequestStats:
    """Request-level metrics kept separate for feed and event details."""

    requests: int = 0
    errors: int = 0
    parsing_errors: int = 0
    response_times_ms: list[int] = field(default_factory=list)
    payload_sizes: list[int] = field(default_factory=list)

    def record(
        self,
        metrics: RequestMetrics | None,
        *,
        error: bool = False,
        parsing_error: bool = False,
    ) -> None:
        self.requests += 1
        if error:
            self.errors += 1
        if parsing_error:
            self.parsing_errors += 1
        if metrics is not None:
            self.response_times_ms.append(metrics.response_time_ms)
            self.payload_sizes.append(metrics.payload_size)

    def summary(self) -> dict[str, float | int]:
        timings = self.response_times_ms
        payloads = self.payload_sizes
        ordered = sorted(timings)
        p95_index = max(0, min(len(ordered) - 1, int(len(ordered) * 0.95) - 1))
        return {
            "requests": self.requests,
            "errors": self.errors,
            "parsing_errors": self.parsing_errors,
            "error_rate": self.errors / self.requests if self.requests else 0.0,
            "median_response_ms": median(timings) if timings else 0.0,
            "p95_response_ms": ordered[p95_index] if ordered else 0,
            "max_response_ms": max(timings) if timings else 0,
            "average_response_ms": mean(timings) if timings else 0.0,
            "average_payload_bytes": mean(payloads) if payloads else 0.0,
            "max_payload_bytes": max(payloads) if payloads else 0,
        }


@dataclass(slots=True)
class CollectorEventState:
    halftime_detected: bool = False
    fotmob_halftime_detected: bool = False
    first_h2_goal_seen: bool = False
    first_h2_goal_at: str | None = None
    reopen_enqueued: bool = False
    minute_slots_seen: set[int] = field(default_factory=set)
    reopen_probe_sequence: int = 0
    final_enqueued: bool = False
    last_outcome_availability: dict[str, bool] = field(default_factory=dict)
    latest_details: EventDetails | None = None
    last_status: str | None = None


class Collector:
    """Schedule and persist historical Tipico snapshots independently of UI."""

    def __init__(
        self,
        client: TipicoClient,
        database: Database,
        raw_storage: RawStorage,
        settings: Settings,
        *,
        logger: logging.Logger | None = None,
        event_service: EventService | None = None,
        market_repository: MarketRepository | None = None,
        client_factory: Callable[[], TipicoClient] | None = None,
        fotmob_service: "FotMobService | None" = None,
    ) -> None:
        self.client = client
        self.database = database
        self.raw_storage = raw_storage
        self.settings = settings
        self.logger = logger or logging.getLogger("tipico")
        self.event_service = event_service or EventService(
            client,
            database,
            raw_storage,
            settings,
            logger=self.logger,
        )
        self.market_repository = market_repository or MarketRepository(database)
        self.intelligence_service = MarketIntelligenceService(
            database,
            settings,
            logger=self.logger,
        )
        self.archive = ParquetArchive(
            settings.archive_path,
            compression=settings.parquet_compression,
            logger=self.logger,
        )
        self.client_factory = client_factory or (
            lambda: TipicoClient(settings, logger=self.logger)
        )
        # Optional enrichment is injected so Tipico collection remains fully
        # usable when FotMob is disabled, unavailable or policy-blocked.
        self.fotmob_service = fotmob_service

        self.feed_stats = RequestStats()
        self.prematch_stats = RequestStats()
        self.detail_stats = RequestStats()
        self.snapshot_counts: Counter[str] = Counter()
        self.retries = 0
        self.reopens_detected = 0
        self.errors: deque[str] = deque(maxlen=50)
        self._event_states: dict[str, CollectorEventState] = {}
        self._observed_events: dict[str, LiveEvent] = {}
        self._queue: list[tuple[float, int, SnapshotJob]] = []
        self._pending_keys: set[tuple[str, str, str, int]] = set()
        self._active_keys: set[tuple[str, str, str, int]] = set()
        self._futures: dict[Future[DetailFetchResult], SnapshotJob] = {}
        self._sequence = 0
        self._last_feed_poll_at: float | None = None
        self._last_prematch_poll_at: float | None = None
        self._started_at: str | None = None
        self._finished_at: str | None = None
        self._last_status_write_at = 0.0
        self._executor: ThreadPoolExecutor | None = None
        self._running = False
        self._last_archive_export_at: float | None = None

    @property
    def queue_depth(self) -> int:
        return len(self._queue) + len(self._futures)

    def _record_error(self, message: str) -> None:
        self.errors.append(f"{_now_iso()} {message}")
        self.logger.error(message)

    def _enqueue(
        self,
        event_id: str,
        snapshot_type: str,
        trigger_reason: str,
        *,
        delay_seconds: float = 0.0,
        raw_full: bool,
        fallback_event: LiveEvent | None = None,
        sequence: int = 0,
        goal_at: str | None = None,
        reopen_delay_seconds: float | None = None,
    ) -> bool:
        job = SnapshotJob(
            event_id=str(event_id),
            snapshot_type=snapshot_type,
            trigger_reason=trigger_reason,
            due_at=time.monotonic() + max(0.0, delay_seconds),
            raw_full=raw_full,
            fallback_event=fallback_event,
            sequence=sequence,
            goal_at=goal_at,
            reopen_delay_seconds=reopen_delay_seconds,
        )
        if snapshot_type not in {"REOPEN_PROBE"} and self.database.snapshot_exists(
            event_id, snapshot_type
        ):
            return False
        if snapshot_type == "REOPEN_PROBE" and any(
            key[0] == str(event_id) and key[1] == snapshot_type
            for key in self._pending_keys | self._active_keys
        ):
            return False
        if job.key in self._pending_keys or job.key in self._active_keys:
            return False
        self._pending_keys.add(job.key)
        self._sequence += 1
        heapq.heappush(self._queue, (job.due_at, self._sequence, job))
        return True

    def _poll_feed(self) -> EventRefreshResult:
        previous_events = dict(self._observed_events)
        result = self.event_service.refresh()
        self._last_feed_poll_at = time.monotonic()
        self.feed_stats.record(
            result.metrics,
            error=not result.success and not (result.error or "").startswith("Live feed parse error"),
            parsing_error=not result.success and (result.error or "").startswith("Live feed parse error"),
        )
        if not result.success:
            if result.error:
                self._record_error(result.error)
            return result

        current_events = {event.event_id: event for event in result.events}
        for event_id, event in current_events.items():
            previous = previous_events.get(event_id)
            state = self._event_states.setdefault(event_id, CollectorEventState())
            observed_at = result.metrics.response_received_at if result.metrics else _now_iso()
            previous_h2 = self._second_half_goals(previous) if previous is not None else None
            current_h2 = self._second_half_goals(event)
            if not _is_finished(event) and current_h2 is not None and current_h2 > 0 and (
                previous_h2 is None or current_h2 > previous_h2
            ) and not state.first_h2_goal_seen:
                state.first_h2_goal_seen = True
                state.first_h2_goal_at = observed_at
                self.logger.info("First second-half goal detected: event=%s", event_id)

            if _is_halftime(event) and self.settings.snapshot_ht_enabled and not state.halftime_detected:
                state.halftime_detected = True
                self._enqueue(
                    event_id,
                    "HALFTIME",
                    "FIRST_HALF_TO_HALF_TIME",
                    raw_full=self.settings.raw_at_halftime,
                    fallback_event=event,
                )
                if self.settings.snapshot_ht_stable_enabled:
                    self._enqueue(
                        event_id,
                        "HT_STABLE",
                        "HALF_TIME_STABLE_DELAY",
                        delay_seconds=self.settings.snapshot_ht_stable_delay_seconds,
                        raw_full=self.settings.raw_at_halftime,
                        fallback_event=event,
                    )

            if _is_halftime(event) and not state.fotmob_halftime_detected:
                state.fotmob_halftime_detected = True
                self._enrich_fotmob_halftime(event)

            minute = _parse_minute(event.display_minute)
            if _is_second_half(event) and minute is not None:
                minute_settings = {
                    60: self.settings.snapshot_60_enabled,
                    70: self.settings.snapshot_70_enabled,
                    80: self.settings.snapshot_80_enabled,
                    85: self.settings.snapshot_85_enabled,
                    90: self.settings.snapshot_90_enabled,
                }
                for target, enabled in minute_settings.items():
                    if enabled and minute >= target and target not in state.minute_slots_seen:
                        state.minute_slots_seen.add(target)
                        self._enqueue(
                            event_id,
                            f"MINUTE_{target}",
                            f"FIRST_MINUTE_{target}_CROSSING",
                            raw_full=False,
                            fallback_event=event,
                        )

            if (
                state.first_h2_goal_seen
                and not state.reopen_enqueued
                and not _is_finished(event)
                and self.settings.snapshot_first_h2_goal_reopen_enabled
            ):
                state.reopen_probe_sequence += 1
                self._enqueue(
                    event_id,
                    "REOPEN_PROBE",
                    "WAIT_FOR_FIRST_H2_MARKET_REOPEN",
                    raw_full=False,
                    fallback_event=event,
                    sequence=state.reopen_probe_sequence,
                    goal_at=state.first_h2_goal_at,
                )

            if _is_finished(event) and self.settings.snapshot_final_enabled and not state.final_enqueued:
                state.final_enqueued = True
                self._enqueue(
                    event_id,
                    "FINAL",
                    "REGULAR_FINISHED_FEED_STATE",
                    raw_full=False,
                    fallback_event=event,
                )

        self._observed_events = current_events
        return result

    def _enrich_fotmob_halftime(self, event: LiveEvent) -> None:
        """Fetch one confirmed FotMob FirstHalf detail at the Tipico HZ edge."""

        service = self.fotmob_service
        if service is None or not getattr(service, "automated_worker_allowed", False):
            return
        try:
            resolved = self._resolve_fotmob_link(event)
            if resolved is None:
                return
            status = getattr(getattr(resolved, "match_result", None), "status", "UNMATCHED")
            if status not in {"EXACT", "HIGH_CONFIDENCE", "MANUALLY_CONFIRMED"}:
                self.logger.info(
                    "FotMob HZ enrichment unavailable: event=%s status=%s",
                    event.event_id,
                    status,
                )
                return
            result = service.refresh_for_tipico_event(event, snapshot_type="HALFTIME")
            if not getattr(result, "success", False):
                self.logger.warning(
                    "FotMob HZ enrichment failed: event=%s reason=%s",
                    event.event_id,
                    getattr(result, "error", None) or "unknown error",
                )
        except Exception as exc:  # enrichment must never stop Tipico polling
            self.logger.warning(
                "FotMob HZ enrichment error: event=%s reason=%s",
                event.event_id,
                exc,
            )

    def _resolve_fotmob_link(self, event: LiveEvent) -> Any | None:
        """Resolve an index-only provider link without making a detail request."""

        service = self.fotmob_service
        if service is None or not getattr(service, "automated_worker_allowed", False):
            return None
        try:
            return service.resolver.resolve(event)
        except Exception as exc:  # optional enrichment must never stop polling
            self.logger.warning(
                "FotMob provider-link resolution failed: event=%s reason=%s",
                event.event_id,
                exc,
            )
            return None

    @staticmethod
    def _second_half_goals(event: LiveEvent | None) -> int | None:
        if event is None:
            return None
        if (
            event.score_home is None
            or event.score_away is None
            or event.ht_score_home is None
            or event.ht_score_away is None
        ):
            return None
        return max(
            0,
            int(event.score_home + event.score_away - event.ht_score_home - event.ht_score_away),
        )

    def _poll_prematch(self) -> None:
        """Discover future football events and schedule only future targets."""

        get_upcoming = getattr(self.client, "get_upcoming_football_events", None)
        if get_upcoming is None:
            return
        self._last_prematch_poll_at = time.monotonic()
        try:
            response: ApiResponse = get_upcoming("today", max_markets=1)
            self.prematch_stats.record(response.metrics)
            events = parse_upcoming_feed(response.payload, logger=self.logger)
        except TipicoApiError as exc:
            self.prematch_stats.record(exc.metrics, error=True)
            self._record_error(str(exc))
            return
        except (TypeError, ValueError, KeyError) as exc:
            self.prematch_stats.record(
                getattr(self.client, "last_metrics", None),
                parsing_error=True,
            )
            self._record_error(f"Upcoming feed parse error: {exc}")
            return

        now = datetime.now(timezone.utc)
        for event in events:
            observed_at = response.metrics.response_received_at
            # Keep the pre-match event/competition metadata available even
            # before the first detail snapshot is due.
            self.event_service.repository.save_observation(event, observed_at)
            self._resolve_fotmob_link(event)
            if not event.kickoff_time or event.status.strip().lower() not in {
                "pre_match",
                "prematch",
            }:
                continue
            try:
                kickoff = datetime.fromisoformat(event.kickoff_time.replace("Z", "+00:00"))
                kickoff = kickoff.astimezone(timezone.utc)
            except ValueError:
                continue
            if kickoff <= now:
                continue
            if not self.settings.snapshot_pre_enabled:
                continue
            due = kickoff - timedelta(minutes=1)
            seconds_until_due = (due - now).total_seconds()
            # A missed target is not reconstructed. A small grace window
            # handles a poll landing just after the scheduled second.
            if seconds_until_due < -5:
                continue
            self._enqueue(
                event.event_id,
                "PRE_KICKOFF",
                "T_MINUS_1",
                delay_seconds=max(0.0, seconds_until_due),
                raw_full=False,
                fallback_event=event,
            )

    def _fetch_detail(self, job: SnapshotJob) -> DetailFetchResult:
        delays = (0, *self.settings.collector_retry_delays_seconds)
        last_metrics: RequestMetrics | None = None
        last_payload: dict | None = None
        last_error = "detail request failed"
        api_error = False
        attempt_metrics: list[RequestMetrics] = []
        attempt_kinds: list[str] = []
        for attempt, delay in enumerate(delays, start=1):
            if delay:
                time.sleep(delay)
            client = self.client_factory()
            try:
                response: ApiResponse = client.get_event_details(job.event_id)
                last_metrics = response.metrics
                last_payload = response.payload
                attempt_metrics.append(response.metrics)
                details = parse_event_details(
                    response.payload,
                    event_id=job.event_id,
                    logger=self.logger,
                )
                if job.fallback_event is not None and not details.event.competition_name:
                    details.event.competition_name = job.fallback_event.competition_name
                if job.fallback_event is not None and not details.event.competition_country:
                    details.event.competition_country = job.fallback_event.competition_country
                return DetailFetchResult(
                    job=job,
                    success=True,
                    details=details,
                    payload=response.payload,
                    metrics=response.metrics,
                    attempts=attempt,
                    attempt_metrics=attempt_metrics,
                    attempt_kinds=[*attempt_kinds, "ok"],
                )
            except TipicoApiError as exc:
                api_error = True
                last_metrics = exc.metrics
                if exc.metrics is not None:
                    attempt_metrics.append(exc.metrics)
                attempt_kinds.append("api")
                last_error = str(exc)
                if attempt < len(delays):
                    self.retries += 1
            except (TypeError, ValueError, KeyError) as exc:
                last_metrics = getattr(client, "last_metrics", None)
                if last_metrics is not None:
                    attempt_metrics.append(last_metrics)
                attempt_kinds.append("parse")
                last_error = f"Event detail parse error: {exc}"
                api_error = False
                break
            finally:
                if isinstance(client, TipicoClient):
                    client.close()
        return DetailFetchResult(
            job=job,
            success=False,
            payload=last_payload,
            metrics=last_metrics,
            error=last_error,
            api_error=api_error,
            attempts=len(delays),
            attempt_metrics=attempt_metrics,
            attempt_kinds=attempt_kinds,
        )

    def _drain_due_jobs(self) -> None:
        if self._executor is None:
            return
        max_workers = max(1, min(5, self.settings.collector_detail_workers))
        while self._queue and len(self._futures) < max_workers:
            due_at, _, job = self._queue[0]
            if due_at > time.monotonic():
                break
            heapq.heappop(self._queue)
            self._pending_keys.discard(job.key)
            self._active_keys.add(job.key)
            future = self._executor.submit(self._fetch_detail, job)
            self._futures[future] = job

    def _collect_finished(self, *, block: bool = False) -> None:
        if not self._futures:
            return
        futures = list(self._futures)
        if block:
            # Waiting on one future is enough; the next loop picks up all
            # already completed requests without blocking the feed scheduler.
            wait(futures, return_when=FIRST_COMPLETED)
        for future in futures:
            if not future.done():
                continue
            job = self._futures.pop(future)
            self._active_keys.discard(job.key)
            try:
                result = future.result()
            except Exception as exc:  # keep one bad job from killing the collector
                result = DetailFetchResult(
                    job=job,
                    success=False,
                    error=f"collector worker error: {exc}",
                )
            self._persist_result(result)

    def _update_current_state(
        self,
        details: EventDetails,
        observed_at: str,
    ) -> MarketAnalysis | None:
        """Write only the replaceable operational view for a detail response."""

        self.market_repository.save_current_details(details, observed_at)
        try:
            return self.intelligence_service.analyze(
                details,
                observed_at=observed_at,
                now=datetime.fromisoformat(observed_at.replace("Z", "+00:00")),
                persist=True,
            )
        except Exception as exc:
            self._record_error(f"Current intelligence failed: event={details.event.event_id}: {exc}")
            return None

    @staticmethod
    def _has_relevant_tradeable_market(analysis: MarketAnalysis | None) -> bool:
        if analysis is None:
            return False
        zero = analysis.zero_equivalence.best_odds
        return bool(zero is not None and zero.selected is not None and zero.selected.is_open)

    def _make_snapshot(
        self,
        job: SnapshotJob,
        details: EventDetails,
        analysis: MarketAnalysis | None,
        observed_at: str,
        *,
        raw_path: str | None = None,
        reopen_at: str | None = None,
    ) -> tuple[Snapshot, dict[str, Any]]:
        second_half_goals, second_half_class = (
            _second_half_label(details.event) if job.snapshot_type == "FINAL" else (None, None)
        )
        snapshot = Snapshot(
            event_id=job.event_id,
            observed_at=observed_at,
            snapshot_type=job.snapshot_type,
            trigger_reason=job.trigger_reason,
            match_status=details.event.status,
            display_time=details.event.display_minute,
            score_home=details.event.score_home,
            score_away=details.event.score_away,
            ht_score_home=details.event.ht_score_home,
            ht_score_away=details.event.ht_score_away,
            market_count=details.market_count,
            outcome_count=details.outcome_count,
            open_outcome_count=details.open_outcome_count,
            paused_outcome_count=details.paused_outcome_count,
            snapshot_quality=_quality(details),
            raw_payload_path=raw_path,
            second_half_goals=second_half_goals,
            second_half_goal_class=second_half_class,
            competition_id=details.event.competition_id,
            competition_name=details.event.competition_name,
            competition_country=details.event.competition_country,
            home_team=details.event.home_team,
            away_team=details.event.away_team,
            kickoff_time=details.event.kickoff_time,
            goal_at=job.goal_at,
            reopen_at=reopen_at,
            reopen_delay_seconds=job.reopen_delay_seconds,
        )
        payload = build_snapshot_payload(details, analysis, snapshot)
        snapshot.match_minute = payload.get("match_minute")
        snapshot.q_zero_best = payload.get("q_zero_best")
        snapshot.q_zero_source_type = payload.get("q_zero_source_type")
        snapshot.q_zero_market_id = payload.get("q_zero_market_id")
        snapshot.q_zero_outcome_id = payload.get("q_zero_outcome_id")
        snapshot.q_two_plus_best = payload.get("q_two_plus_best")
        snapshot.q_two_plus_source_type = payload.get("q_two_plus_source_type")
        snapshot.q_two_plus_market_id = payload.get("q_two_plus_market_id")
        snapshot.q_two_plus_outcome_id = payload.get("q_two_plus_outcome_id")
        snapshot.remaining_under_05 = payload.get("remaining_under_05")
        snapshot.remaining_over_05 = payload.get("remaining_over_05")
        snapshot.remaining_under_15 = payload.get("remaining_under_15")
        snapshot.remaining_over_15 = payload.get("remaining_over_15")
        snapshot.p0_market = payload.get("p0_market")
        snapshot.p1_market = payload.get("p1_market")
        snapshot.p2plus_market = payload.get("p2plus_market")
        snapshot.p1_break_even = payload.get("p1_break_even")
        snapshot.p1_buffer = payload.get("p1_buffer")
        snapshot.win_roi = payload.get("win_roi")
        snapshot.normalizer_version = payload.get("normalizer_version")
        snapshot.strategy_version = payload.get("strategy_version")
        snapshot.relevant_markets_json = payload.get("relevant_markets_json")
        return snapshot, payload

    def _persist_snapshot(
        self,
        job: SnapshotJob,
        details: EventDetails,
        analysis: MarketAnalysis | None,
        observed_at: str,
        *,
        payload: dict[str, Any] | None = None,
        raw_path: str | None = None,
        reopen_at: str | None = None,
    ) -> bool:
        snapshot, built_payload = self._make_snapshot(
            job,
            details,
            analysis,
            observed_at,
            raw_path=raw_path,
            reopen_at=reopen_at,
        )
        if payload is None:
            payload = built_payload
        else:
            payload.update(built_payload)
        snapshot_id, created_outbox = self.database.enqueue_historical_snapshot(
            snapshot,
            payload,
        )
        snapshot.snapshot_id = snapshot_id
        created = created_outbox
        if created:
            self.snapshot_counts[job.snapshot_type] += 1
        if job.snapshot_type == "FINAL":
            self._persist_match_result(details.event, observed_at, snapshot)
        if job.snapshot_type == "HALFTIME" and self.settings.raw_at_halftime:
            self._write_halftime_report(snapshot, details)
        return created

    def _persist_match_result(
        self,
        event: LiveEvent,
        finished_at: str,
        snapshot: Snapshot,
    ) -> None:
        if (
            event.score_home is None
            or event.score_away is None
            or event.ht_score_home is None
            or event.ht_score_away is None
        ):
            return
        if not _is_finished(event):
            return
        second_half_goals = snapshot.second_half_goals
        if second_half_goals is None or second_half_goals < 0:
            return
        self.database.upsert_match_result(
            {
                "event_id": event.event_id,
                "competition_id": event.competition_id,
                "competition_name": event.competition_name,
                "competition_country": event.competition_country,
                "home_team": event.home_team,
                "away_team": event.away_team,
                "kickoff_at": event.kickoff_time,
                "ht_home": event.ht_score_home,
                "ht_away": event.ht_score_away,
                "ft_home": event.score_home,
                "ft_away": event.score_away,
                "first_half_goals": event.ht_score_home + event.ht_score_away,
                "second_half_goals": second_half_goals,
                "second_half_goal_class": snapshot.second_half_goal_class,
                "final_status": event.status,
                "finished_at": finished_at,
                "extra_time": None if event.extra_time is None else int(event.extra_time),
                "penalties": None if event.penalties is None else int(event.penalties),
            }
        )

    def _persist_result(self, result: DetailFetchResult) -> None:
        job = result.job
        if result.attempt_metrics:
            for metrics, kind in zip(result.attempt_metrics, result.attempt_kinds):
                self.detail_stats.record(
                    metrics,
                    error=kind == "api",
                    parsing_error=kind == "parse",
                )
        else:
            self.detail_stats.record(
                result.metrics,
                error=not result.success and result.api_error,
                parsing_error=not result.success and not result.api_error,
            )
        observed_at = result.metrics.response_received_at if result.metrics else _now_iso()
        if not result.success or result.details is None or result.payload is None:
            if result.payload is not None and not result.api_error:
                try:
                    debug_result = self.raw_storage.store(
                        "debug",
                        job.event_id,
                        result.payload,
                        observed_at=observed_at,
                    )
                    if debug_result.changed and debug_result.path:
                        self.logger.info(
                            "Stored parser-error raw payload: event=%s path=%s",
                            job.event_id,
                            debug_result.path,
                        )
                except OSError as exc:
                    self._record_error(
                        f"Could not store parser-error raw {job.event_id}: {exc}"
                    )
            self._record_error(
                f"Detail probe failed: event={job.event_id} type={job.snapshot_type} "
                f"reason={job.trigger_reason}: {result.error or 'unknown error'}"
            )
            # A feed-confirmed regular final still yields a useful result row
            # if the detail endpoint is temporarily unavailable. A vanished
            # event is intentionally never turned into a false FINAL.
            if (
                job.snapshot_type == "FINAL"
                and job.fallback_event is not None
                and job.trigger_reason != "EVENT_DISAPPEARED"
                and _is_finished(job.fallback_event)
                and job.fallback_event.score_home is not None
                and job.fallback_event.score_away is not None
                and job.fallback_event.ht_score_home is not None
                and job.fallback_event.ht_score_away is not None
            ):
                details = EventDetails(
                    event=job.fallback_event,
                    markets=[],
                    categories=[],
                    raw_data={},
                )
                analysis = None
                self._persist_snapshot(job, details, analysis, observed_at)
            return

        details = result.details
        analysis = self._update_current_state(details, observed_at)
        state = self._event_states.setdefault(job.event_id, CollectorEventState())
        state.latest_details = details
        state.last_status = details.event.status
        state.last_outcome_availability = {
            outcome.outcome_id: outcome.is_available
            for market in details.markets
            for outcome in market.outcomes
        }

        if job.snapshot_type == "REOPEN_PROBE":
            if (
                state.first_h2_goal_seen
                and not state.reopen_enqueued
                and self._has_relevant_tradeable_market(analysis)
            ):
                state.reopen_enqueued = True
                self.reopens_detected += 1
                reopen_at = observed_at
                delay = None
                if state.first_h2_goal_at:
                    try:
                        delay = max(
                            0.0,
                            (
                                datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
                                - datetime.fromisoformat(state.first_h2_goal_at.replace("Z", "+00:00"))
                            ).total_seconds(),
                        )
                    except ValueError:
                        delay = None
                self._persist_snapshot(
                    SnapshotJob(
                        event_id=job.event_id,
                        snapshot_type="FIRST_H2_GOAL_REOPEN",
                        trigger_reason="FIRST_RELEVANT_MARKET_REOPEN",
                        due_at=job.due_at,
                        raw_full=False,
                        fallback_event=job.fallback_event,
                        goal_at=state.first_h2_goal_at,
                        reopen_delay_seconds=delay,
                    ),
                    details,
                    analysis,
                    observed_at,
                    reopen_at=reopen_at,
                )
            return

        raw_path: str | None = None
        if job.raw_full and result.payload is not None:
            try:
                raw_result = self.raw_storage.store(
                    "events",
                    job.event_id,
                    result.payload,
                    observed_at=observed_at,
                    halftime=job.snapshot_type in {"HALFTIME", "HT_STABLE"},
                )
                resolved_path = raw_result.path or self.raw_storage.path_for_hash(
                    "events",
                    job.event_id,
                    raw_result.content_hash,
                    observed_at=observed_at,
                    halftime=job.snapshot_type in {"HALFTIME", "HT_STABLE"},
                )
                raw_path = str(resolved_path) if resolved_path else None
            except OSError as exc:
                self._record_error(f"Could not store snapshot raw {job.event_id}: {exc}")
        self._persist_snapshot(
            job,
            details,
            analysis,
            observed_at,
            raw_path=raw_path,
        )

    def _write_halftime_report(self, snapshot: Snapshot, details: EventDetails) -> None:
        date_dir = datetime.fromisoformat(
            snapshot.observed_at.replace("Z", "+00:00")
        ).astimezone(timezone.utc).strftime("%Y-%m-%d")
        directory = self.settings.halftime_reports_path / date_dir
        directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "event_id": details.event.event_id,
            "competition": details.event.competition_name,
            "teams": {
                "home": details.event.home_team,
                "away": details.event.away_team,
            },
            "halftime_score": {
                "home": details.event.ht_score_home,
                "away": details.event.ht_score_away,
            },
            "score_at_snapshot": {
                "home": details.event.score_home,
                "away": details.event.score_away,
            },
            "snapshot_id": snapshot.snapshot_id,
            "observed_at": snapshot.observed_at,
            "market_count": details.market_count,
            "outcome_count": details.outcome_count,
            "open_outcome_count": details.open_outcome_count,
            "paused_outcome_count": details.paused_outcome_count,
            "markets": [
                {
                    "market_id": market.market_id,
                    "type": market.type,
                    "caption": market.caption,
                    "fixedParam": market.fixed_param,
                    "status": market.status,
                    "outcomes": [
                        {
                            "outcome_id": outcome.outcome_id,
                            "caption": outcome.caption,
                            "choiceParam": outcome.choice_param,
                            "odds": outcome.odds,
                            "quoteFloatValue": outcome.quote_float_value,
                            "quote": outcome.quote_raw,
                            "status": outcome.status,
                            "available": outcome.is_available,
                        }
                        for outcome in market.outcomes
                    ],
                }
                for market in details.markets
            ],
        }
        path = directory / f"{details.event.event_id}_{snapshot.snapshot_id}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _export_snapshots_if_due(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if (
            not force
            and self._last_archive_export_at is not None
            and now - self._last_archive_export_at
            < max(1, self.settings.snapshot_outbox_export_interval_seconds)
        ):
            return
        result = self.archive.export_pending(
            self.database,
            batch_size=self.settings.snapshot_outbox_batch_size,
        )
        self._last_archive_export_at = now
        if int(result.get("errors") or 0):
            self._record_error(f"Parquet export reported {result['errors']} error(s)")
        service = self.fotmob_service
        if service is not None and getattr(service, "automated_worker_allowed", False):
            try:
                fotmob_result = service.export_pending()
                if int(fotmob_result.get("errors") or 0):
                    self._record_error(
                        f"FotMob Parquet export reported {fotmob_result['errors']} error(s)"
                    )
            except Exception as exc:  # optional sink must not stop collection
                self._record_error(f"FotMob Parquet export failed: {exc}")

    def status(self) -> dict:
        started = self._started_at
        return {
            "status": "RUNNING" if self._running else "COMPLETED",
            "started_at": started,
            "finished_at": self._finished_at,
            "updated_at": _now_iso(),
            "queue_depth": self.queue_depth,
            "active_detail_requests": len(self._futures),
            "snapshot_counts": dict(self.snapshot_counts),
            "retries": self.retries,
            "reopens_detected": self.reopens_detected,
            "errors": list(self.errors),
            "feed": self.feed_stats.summary(),
            "prematch": self.prematch_stats.summary(),
            "detail": self.detail_stats.summary(),
            "archive": {
                "path": str(self.archive.snapshot_root),
                "size_bytes": self.archive.total_size_bytes,
                "last_export_at": self.archive.last_export_at,
                "last_error": self.archive.last_error,
                "pending": self.database.count_rows("snapshot_outbox"),
            },
            "coverage": self.database.collection_metrics_for_date(),
        }

    def _write_status(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_status_write_at < 2.0:
            return
        path = self.settings.collector_status_path
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self.status(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
        self._last_status_write_at = now

    def _tick(self) -> None:
        now = time.monotonic()
        if (
            getattr(self.client, "get_upcoming_football_events", None) is not None
            and (
                self._last_prematch_poll_at is None
                or now - self._last_prematch_poll_at
                >= max(1, self.settings.collector_prematch_refresh_seconds)
            )
        ):
            try:
                self._poll_prematch()
            except Exception as exc:
                self._record_error(f"collector pre-match worker error: {exc}")
        if (
            self._last_feed_poll_at is None
            or now - self._last_feed_poll_at
            >= max(1, self.settings.collector_feed_refresh_seconds)
        ):
            try:
                self._poll_feed()
            except Exception as exc:
                self._record_error(f"collector feed worker error: {exc}")
        self._collect_finished()
        self._drain_due_jobs()
        self._export_snapshots_if_due()
        self._write_status()

    def run_once(self) -> dict:
        """Poll once and process all detail jobs that are due immediately."""

        self._started_at = _now_iso()
        self._finished_at = None
        self._running = True
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, min(5, self.settings.collector_detail_workers)),
            thread_name_prefix="tipico-detail",
        )
        try:
            if getattr(self.client, "get_upcoming_football_events", None) is not None:
                self._poll_prematch()
            self._poll_feed()
            self._drain_due_jobs()
            while self._futures:
                self._collect_finished(block=True)
                self._drain_due_jobs()
            self._export_snapshots_if_due(force=True)
        finally:
            self._running = False
            self._finished_at = _now_iso()
            if self._executor is not None:
                self._executor.shutdown(wait=True)
                self._executor = None
            self._write_status(force=True)
        return self.status()

    def run_forever(
        self,
        *,
        duration_minutes: float | None = None,
        stop_event: threading.Event | None = None,
    ) -> dict:
        """Run until stopped or the optional wall-clock duration elapses."""

        self._started_at = _now_iso()
        self._finished_at = None
        self._running = True
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, min(5, self.settings.collector_detail_workers)),
            thread_name_prefix="tipico-detail",
        )
        deadline = (
            time.monotonic() + float(duration_minutes) * 60
            if duration_minutes is not None
            else None
        )
        try:
            while True:
                if deadline is not None and time.monotonic() >= deadline:
                    break
                if stop_event is not None and stop_event.is_set():
                    break
                self._tick()
                time.sleep(0.10)
            # Let requests already submitted finish and persist. Queued jobs
            # that were never started remain visible in the final status.
            while self._futures:
                self._collect_finished(block=True)
                self._drain_due_jobs()
            self._export_snapshots_if_due(force=True)
        finally:
            self._running = False
            self._finished_at = _now_iso()
            if self._executor is not None:
                self._executor.shutdown(wait=True)
                self._executor = None
            self._write_status(force=True)
        return self.status()
