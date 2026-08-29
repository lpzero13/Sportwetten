"""Upcoming Tipico football feed service for the V0.3 dashboard."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from config import Settings
from models.event import LiveEvent
from storage.database import Database
from storage.repositories import EventRepository
from tipico.client import RequestMetrics, TipicoApiError, TipicoClient
from tipico.parser import parse_upcoming_feed


@dataclass(slots=True)
class UpcomingRefreshResult:
    success: bool
    events: list[LiveEvent]
    metrics: RequestMetrics | None = None
    error: str | None = None


class UpcomingService:
    """Fetch upcoming events on demand and keep the page independent of UI code."""

    def __init__(
        self,
        client: TipicoClient,
        database: Database,
        settings: Settings,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self.client = client
        self.database = database
        self.settings = settings
        self.logger = logger or logging.getLogger("tipico")
        self.repository = EventRepository(database)
        self._events: dict[str, LiveEvent] = {}
        self.last_metrics: RequestMetrics | None = None
        self.last_success_at: str | None = None
        self.last_error: str | None = None
        self.request_count = 0

    @property
    def events(self) -> list[LiveEvent]:
        return list(self._events.values())

    def should_refresh(self) -> bool:
        if self.last_success_at is None:
            return True
        try:
            observed = datetime.fromisoformat(
                self.last_success_at.replace("Z", "+00:00")
            )
        except ValueError:
            return True
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        age = (
            datetime.now(timezone.utc) - observed.astimezone(timezone.utc)
        ).total_seconds()
        return age >= max(10, self.settings.collector_prematch_refresh_seconds)

    def refresh_if_due(self) -> UpcomingRefreshResult | None:
        if not self.should_refresh():
            return None
        return self.refresh()

    def refresh(self) -> UpcomingRefreshResult:
        self.request_count += 1
        try:
            response = self.client.get_upcoming_football_events("today", max_markets=1)
            self.last_metrics = response.metrics
            events = parse_upcoming_feed(response.payload, logger=self.logger)
        except TipicoApiError as exc:
            self.last_error = str(exc)
            return UpcomingRefreshResult(False, self.events, exc.metrics, str(exc))
        except (TypeError, ValueError, KeyError) as exc:
            self.last_error = f"Upcoming feed parse error: {exc}"
            return UpcomingRefreshResult(False, self.events, self.last_metrics, self.last_error)

        for event in events:
            observed_at = response.metrics.response_received_at
            self.repository.save_observation(event, observed_at)
            self._events[event.event_id] = event
        self.last_success_at = response.metrics.response_received_at
        self.last_error = None
        return UpcomingRefreshResult(True, self.events, response.metrics)
