"""Live overview polling and event-state persistence."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from config import Settings
from models.event import LiveEvent
from storage.database import Database
from storage.raw_storage import RawStorage
from storage.repositories import EventRepository
from tipico.client import ApiResponse, RequestMetrics, TipicoApiError, TipicoClient
from tipico.parser import parse_live_feed


@dataclass(slots=True)
class EventRefreshResult:
    success: bool
    events: list[LiveEvent]
    metrics: RequestMetrics | None = None
    error: str | None = None
    raw_path: str | None = None
    changed_state_count: int = 0


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
        if not self.settings.store_raw_responses:
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

    def refresh(self) -> EventRefreshResult:
        self.request_count += 1
        try:
            response = self.client.get_live_football_events()
            self.last_metrics = response.metrics
            observed_at = response.metrics.response_received_at
            raw_path = self._persist_raw(response, observed_at)
            if not isinstance(response.payload.get("LIVE"), dict):
                raise ValueError("Tipico live response did not contain LIVE")
            parsed_events = parse_live_feed(response.payload, logger=self.logger)
        except TipicoApiError as exc:
            self.error_count += 1
            self.last_error = str(exc)
            self.logger.error("Live feed request failed: %s", exc)
            return EventRefreshResult(
                success=False,
                events=self.events,
                metrics=exc.metrics,
                error=str(exc),
            )
        except (TypeError, ValueError, KeyError) as exc:
            self.parse_error_count += 1
            self.last_error = f"Live feed parse error: {exc}"
            self.logger.exception("Live feed parse error")
            return EventRefreshResult(
                success=False,
                events=self.events,
                metrics=self.last_metrics,
                error=self.last_error,
            )

        previous_ids = set(self._events)
        next_events: dict[str, LiveEvent] = {}
        changed_state_count = 0
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
            if self.repository.save_observation(event, observed_at):
                changed_state_count += 1

        for disappeared_id in previous_ids - set(next_events):
            self.logger.info("Event left live feed: %s", disappeared_id)
            self.database.mark_event_no_longer_live(disappeared_id, observed_at)

        self._events = next_events
        self.last_success_at = observed_at
        self.last_error = None
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
            changed_state_count=changed_state_count,
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
