"""Single-event detail polling and market/odds persistence."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from config import Settings
from models.event import LiveEvent
from models.market import EventDetails
from storage.database import Database
from storage.raw_storage import RawStorage
from storage.repositories import MarketRepository
from tipico.client import RequestMetrics, TipicoApiError, TipicoClient
from tipico.parser import parse_event_details


@dataclass(slots=True)
class MarketRefreshResult:
    success: bool
    details: EventDetails | None
    metrics: RequestMetrics | None = None
    error: str | None = None
    odds_changes: int = 0
    raw_path: str | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MarketService:
    """Fetches and saves details only for the event selected by the user."""

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
        self.repository = MarketRepository(database)
        self.cache: dict[str, EventDetails] = {}
        self.last_metrics: RequestMetrics | None = None
        self.last_error: str | None = None
        self.request_count = 0
        self.error_count = 0
        self.parse_error_count = 0

    def _store_raw(
        self,
        event_id: str,
        payload: dict,
        observed_at: str,
        *,
        halftime: bool,
    ) -> str | None:
        if not self.settings.store_raw_responses:
            return None
        try:
            result = self.raw_storage.store(
                "events",
                event_id,
                payload,
                observed_at=observed_at,
                halftime=halftime,
            )
            if result.changed and result.path:
                self.logger.debug("Stored changed event raw payload: %s", result.path)
                return str(result.path)
        except OSError as exc:
            self.logger.warning("Could not store event raw payload %s: %s", event_id, exc)
        return None

    def load_event_details(
        self,
        event_id: str,
        *,
        overview_event: LiveEvent | None = None,
    ) -> MarketRefreshResult:
        resolved_event_id = str(event_id)
        self.request_count += 1
        try:
            response = self.client.get_event_details(resolved_event_id)
            self.last_metrics = response.metrics
            details = parse_event_details(
                response.payload,
                event_id=resolved_event_id,
                logger=self.logger,
            )
            if overview_event is not None:
                details.event.competition_name = overview_event.competition_name
                if not details.event.competition_country:
                    details.event.competition_country = overview_event.competition_country
            observed_at = response.metrics.response_received_at
            raw_path = self._store_raw(
                resolved_event_id,
                response.payload,
                observed_at,
                halftime=details.event.period == "HALF_TIME",
            )
            odds_changes = self.repository.save_details(
                details,
                observed_at,
                store_odds_history=self.settings.store_odds_history,
            )
        except TipicoApiError as exc:
            self.error_count += 1
            self.last_error = str(exc)
            self.logger.error("Detail request failed: event=%s %s", resolved_event_id, exc)
            return MarketRefreshResult(
                success=False,
                details=self.cache.get(resolved_event_id),
                metrics=exc.metrics,
                error=str(exc),
            )
        except (TypeError, ValueError, KeyError) as exc:
            self.parse_error_count += 1
            self.last_error = f"Event detail parse error: {exc}"
            self.logger.exception(
                "Event detail parse error: event=%s",
                resolved_event_id,
            )
            return MarketRefreshResult(
                success=False,
                details=self.cache.get(resolved_event_id),
                metrics=self.last_metrics,
                error=self.last_error,
            )

        self.cache[resolved_event_id] = details
        self.last_error = None
        self.logger.info(
            "Event detail: event=%s %s markets / %s outcomes",
            resolved_event_id,
            details.market_count,
            details.outcome_count,
        )
        if odds_changes:
            self.logger.info(
                "Odds changes stored: event=%s count=%s",
                resolved_event_id,
                odds_changes,
            )
        return MarketRefreshResult(
            success=True,
            details=details,
            metrics=response.metrics,
            odds_changes=odds_changes,
            raw_path=raw_path,
        )
