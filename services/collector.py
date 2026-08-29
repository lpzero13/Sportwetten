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
from typing import Callable

from config import Settings
from intelligence.service import MarketIntelligenceService
from models.event import LiveEvent
from models.market import EventDetails
from models.snapshot import Snapshot
from storage.database import Database
from storage.raw_storage import RawStorage
from storage.repositories import MarketRepository
from tipico.client import ApiResponse, RequestMetrics, TipicoApiError, TipicoClient
from tipico.parser import parse_event_details, parse_upcoming_feed
from services.event_service import EventService, EventRefreshResult


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
    second_half_seen: bool = False
    strategic_minutes_seen: set[int] = field(default_factory=set)
    core_market_present: bool = False
    last_core_enqueued_at: float | None = None
    core_sequence: int = 0
    goal_sequence: int = 0
    final_enqueued: bool = False
    last_outcome_availability: dict[str, bool] = field(default_factory=dict)
    latest_details: EventDetails | None = None
    reopen_count: int = 0


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
        self.client_factory = client_factory or (
            lambda: TipicoClient(settings, logger=self.logger)
        )

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
    ) -> bool:
        job = SnapshotJob(
            event_id=str(event_id),
            snapshot_type=snapshot_type,
            trigger_reason=trigger_reason,
            due_at=time.monotonic() + max(0.0, delay_seconds),
            raw_full=raw_full,
            fallback_event=fallback_event,
            sequence=sequence,
        )
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
            if previous is None:
                self._enqueue(
                    event_id,
                    "LIVE_PERIODIC",
                    "INITIAL_DISCOVERY",
                    raw_full=False,
                    fallback_event=event,
                )
            elif (
                previous.score_home is not None
                and previous.score_away is not None
                and event.score_home is not None
                and event.score_away is not None
                and (previous.score_home, previous.score_away)
                != (event.score_home, event.score_away)
            ):
                state.goal_sequence += 1
                self._enqueue(
                    event_id,
                    "EVENT_TRIGGERED",
                    "GOAL",
                    raw_full=True,
                    fallback_event=event,
                    sequence=state.goal_sequence,
                )

            halftime = _is_halftime(event)
            if halftime and not state.halftime_detected:
                state.halftime_detected = True
                for phase, delay in enumerate(
                    self.settings.collector_halftime_delays_seconds,
                    start=1,
                ):
                    self._enqueue(
                        event_id,
                        "HALFTIME",
                        f"HT_PHASE_{phase}",
                        delay_seconds=delay,
                        raw_full=True,
                        fallback_event=event,
                        sequence=phase,
                    )

            if _is_second_half(event):
                state.second_half_seen = True
                minute = _parse_minute(event.display_minute)
                if minute is not None and event.status.strip().lower() in {"running", "live"}:
                    for target in self.settings.collector_strategic_minutes:
                        if minute >= target and target not in state.strategic_minutes_seen:
                            state.strategic_minutes_seen.add(target)
                            self._enqueue(
                                event_id,
                                "LIVE_PERIODIC",
                                f"MINUTE_{target}",
                                raw_full=False,
                                fallback_event=event,
                                sequence=target,
                            )
                if (
                    state.core_market_present
                    and (
                        state.last_core_enqueued_at is None
                        or time.monotonic() - state.last_core_enqueued_at
                        >= max(1, self.settings.collector_core_refresh_seconds)
                    )
                ):
                    state.core_sequence += 1
                    state.last_core_enqueued_at = time.monotonic()
                    self._enqueue(
                        event_id,
                        "LIVE_PERIODIC",
                        "CORE_30S",
                        raw_full=False,
                        fallback_event=event,
                        sequence=state.core_sequence,
                    )

            if _is_finished(event) and not state.final_enqueued:
                state.final_enqueued = True
                self._enqueue(
                    event_id,
                    "FINAL",
                    "FINISHED",
                    raw_full=True,
                    fallback_event=event,
                )

        for event_id in set(previous_events) - set(current_events):
            previous = previous_events[event_id]
            state = self._event_states.setdefault(event_id, CollectorEventState())
            if not state.final_enqueued:
                state.final_enqueued = True
                self._enqueue(
                    event_id,
                    "FINAL",
                    "EVENT_DISAPPEARED",
                    raw_full=True,
                    fallback_event=previous,
                )

        self._observed_events = current_events
        return result

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
            for minutes_before in (60, 15, 5, 1):
                due = kickoff - timedelta(minutes=minutes_before)
                seconds_until_due = (due - now).total_seconds()
                # A missed target is not reconstructed. A small grace window
                # handles a poll landing just after the scheduled second.
                if seconds_until_due < -5:
                    continue
                snapshot_type = "PRE_KICKOFF" if minutes_before == 1 else "PREMATCH"
                self._enqueue(
                    event.event_id,
                    snapshot_type,
                    f"T_MINUS_{minutes_before}",
                    delay_seconds=max(0.0, seconds_until_due),
                    raw_full=True,
                    fallback_event=event,
                    sequence=minutes_before,
                )

    def _fetch_detail(self, job: SnapshotJob) -> DetailFetchResult:
        delays = (0, *self.settings.collector_retry_delays_seconds)
        last_metrics: RequestMetrics | None = None
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
        if not result.success or result.details is None or result.payload is None:
            self._persist_failure(result)
            return

        details = result.details
        observed_at = result.metrics.response_received_at if result.metrics else _now_iso()
        raw_path: str | None = None
        if job.raw_full:
            try:
                raw_result = self.raw_storage.store(
                    "events",
                    job.event_id,
                    result.payload,
                    observed_at=observed_at,
                    halftime=job.snapshot_type == "HALFTIME",
                )
                resolved_path = raw_result.path or self.raw_storage.path_for_hash(
                    "events",
                    job.event_id,
                    raw_result.content_hash,
                    observed_at=observed_at,
                    halftime=job.snapshot_type == "HALFTIME",
                )
                raw_path = str(resolved_path) if resolved_path else None
            except OSError as exc:
                self._record_error(f"Could not store raw detail {job.event_id}: {exc}")

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
        )
        snapshot_id = self.database.create_snapshot(snapshot)
        snapshot.snapshot_id = snapshot_id
        self.market_repository.save_details(
            details,
            observed_at,
            store_odds_history=self.settings.store_odds_history,
            snapshot_id=snapshot_id,
        )
        try:
            self.intelligence_service.analyze(
                details,
                observed_at=observed_at,
                snapshot_id=snapshot_id,
                now=datetime.fromisoformat(observed_at.replace("Z", "+00:00")),
            )
        except Exception as exc:
            # Historical collection remains available even if a new V0.3
            # mapping encounters an unexpected provider shape.
            self._record_error(
                f"V0.3 intelligence failed: event={job.event_id}: {exc}"
            )
        for market in details.markets:
            self.database.add_market_presence(
                event_id=job.event_id,
                market_id=market.market_id,
                snapshot_id=snapshot_id,
                observed_at=observed_at,
                market_type=market.type,
                fixed_param=market.fixed_param,
                market_status=market.status,
            )

        state = self._event_states.setdefault(job.event_id, CollectorEventState())
        current_availability = {
            outcome.outcome_id: outcome.is_available
            for market in details.markets
            for outcome in market.outcomes
        }
        for outcome_id, available in current_availability.items():
            if state.last_outcome_availability.get(outcome_id) is False and available:
                state.reopen_count += 1
                self.reopens_detected += 1
                self.logger.info(
                    "Relevant outcome reopened: event=%s outcome=%s observed_at=%s",
                    job.event_id,
                    outcome_id,
                    observed_at,
                )
        state.last_outcome_availability = current_availability
        state.latest_details = details
        state.core_market_present = any(
            market.type.casefold() in CORE_MARKET_TYPES
            or market.type.casefold().startswith("points-more-less")
            or market.type.casefold().startswith("team-points-more-less")
            for market in details.markets
        )
        self.snapshot_counts[job.snapshot_type] += 1
        if job.snapshot_type == "HALFTIME":
            self._write_halftime_report(snapshot, details)

    def _persist_failure(self, result: DetailFetchResult) -> None:
        job = result.job
        self._record_error(
            f"Snapshot failed: event={job.event_id} type={job.snapshot_type} "
            f"reason={job.trigger_reason}: {result.error or 'unknown error'}"
        )
        fallback = job.fallback_event
        observed_at = result.metrics.response_received_at if result.metrics else _now_iso()
        if fallback is not None and job.snapshot_type == "FINAL":
            second_half_goals, second_half_class = _second_half_label(fallback)
            snapshot = Snapshot(
                event_id=job.event_id,
                observed_at=observed_at,
                snapshot_type="FINAL",
                trigger_reason=job.trigger_reason,
                match_status=("NO_LONGER_LIVE" if job.trigger_reason == "EVENT_DISAPPEARED" else fallback.status),
                display_time=fallback.display_minute,
                score_home=fallback.score_home,
                score_away=fallback.score_away,
                ht_score_home=fallback.ht_score_home,
                ht_score_away=fallback.ht_score_away,
                snapshot_quality="FINAL_STATE_ONLY",
                second_half_goals=second_half_goals,
                second_half_goal_class=second_half_class,
            )
        else:
            snapshot = Snapshot(
                event_id=job.event_id,
                observed_at=observed_at,
                snapshot_type=job.snapshot_type,
                trigger_reason=job.trigger_reason,
                match_status=fallback.status if fallback else None,
                display_time=fallback.display_minute if fallback else None,
                score_home=fallback.score_home if fallback else None,
                score_away=fallback.score_away if fallback else None,
                ht_score_home=fallback.ht_score_home if fallback else None,
                ht_score_away=fallback.ht_score_away if fallback else None,
                snapshot_quality="FAILED",
            )
        self.database.create_snapshot(snapshot)
        self.snapshot_counts[job.snapshot_type] += 1

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
        finally:
            self._running = False
            self._finished_at = _now_iso()
            if self._executor is not None:
                self._executor.shutdown(wait=True)
                self._executor = None
            self._write_status(force=True)
        return self.status()
