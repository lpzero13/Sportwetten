"""UI-independent collection and ranking of current half-time events."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from config import Settings
from intelligence.models import MarketAnalysis
from intelligence.service import MarketIntelligenceService
from models.event import LiveEvent
from services.event_service import EventService
from services.market_service import MarketService
from storage.database import Database


def _is_halftime(event: LiveEvent) -> bool:
    return (
        event.period.strip().upper() in {"HALF_TIME", "HALFTIME", "HT"}
        or event.display_minute.strip().upper() == "HZ"
    )


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(slots=True)
class HalftimeScanItem:
    event: LiveEvent
    analysis: MarketAnalysis | None
    source: str
    error: str | None = None

    @property
    def rankable(self) -> bool:
        return bool(
            self.analysis
            and self.analysis.probability.status == "OK"
            and self.analysis.strategy.is_rankable
        )


class HalftimeScannerService:
    """Prefer a fresh collector HT snapshot; fetch only current HT events otherwise."""

    def __init__(
        self,
        event_service: EventService,
        market_service: MarketService,
        intelligence_service: MarketIntelligenceService,
        database: Database,
        settings: Settings,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self.event_service = event_service
        self.market_service = market_service
        self.intelligence_service = intelligence_service
        self.database = database
        self.settings = settings
        self.logger = logger or logging.getLogger("tipico")

    def current_events(self) -> list[LiveEvent]:
        return sorted(
            [event for event in self.event_service.events if _is_halftime(event)],
            key=lambda event: (
                event.competition_name.casefold(),
                event.home_team.casefold(),
                event.event_id,
            ),
        )

    def _fresh_snapshot(self, event_id: str, now: datetime) -> object | None:
        snapshot = self.database.latest_snapshot_for_event(
            event_id,
            snapshot_type="HALFTIME",
            usable_only=True,
        )
        if snapshot is None or not snapshot["raw_payload_path"]:
            return None
        try:
            age = (now - _parse_iso(str(snapshot["observed_at"]))).total_seconds()
        except ValueError:
            return None
        if age < 0 or age > self.settings.max_live_odds_age_seconds:
            return None
        return snapshot

    def scan(
        self,
        *,
        events: Iterable[LiveEvent] | None = None,
        total_stake: float | None = None,
        now: datetime | None = None,
    ) -> list[HalftimeScanItem]:
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        selected_events = list(events) if events is not None else self.current_events()
        items: list[HalftimeScanItem] = []
        for event in selected_events:
            snapshot = self._fresh_snapshot(event.event_id, current)
            if snapshot is not None:
                try:
                    analysis = self.intelligence_service.analyze_snapshot_row(
                        snapshot,
                        total_stake=total_stake,
                        now=_parse_iso(str(snapshot["observed_at"])),
                    )
                    if analysis is not None:
                        items.append(
                            HalftimeScanItem(
                                event=event,
                                analysis=analysis,
                                source="Collector-HZ-Snapshot",
                            )
                        )
                        continue
                except (OSError, TypeError, ValueError, KeyError) as exc:
                    self.logger.warning(
                        "Could not use HT snapshot for event %s: %s",
                        event.event_id,
                        exc,
                    )

            result = self.market_service.load_event_details(
                event.event_id,
                overview_event=event,
            )
            if not result.success or result.details is None:
                items.append(
                    HalftimeScanItem(
                        event=event,
                        analysis=None,
                        source="Aktueller HT-Detailabruf",
                        error=result.error or "Detailabruf fehlgeschlagen",
                    )
                )
                continue
            observed_at = (
                result.metrics.response_received_at
                if result.metrics is not None
                else current.isoformat()
            )
            try:
                analysis = self.intelligence_service.analyze(
                    result.details,
                    observed_at=observed_at,
                    total_stake=total_stake,
                    now=_parse_iso(observed_at),
                    persist=self.settings.persist_ui_refresh,
                )
            except (TypeError, ValueError, KeyError) as exc:
                items.append(
                    HalftimeScanItem(
                        event=event,
                        analysis=None,
                        source="Aktueller HT-Detailabruf",
                        error=f"Analyse fehlgeschlagen: {exc}",
                    )
                )
                continue
            items.append(
                HalftimeScanItem(
                    event=event,
                    analysis=analysis,
                    source="Aktueller HT-Detailabruf",
                )
            )

        return sorted(
            items,
            key=lambda item: (
                0 if item.rankable else 1,
                -float(item.analysis.strategy.p1_buffer or -1e9)
                if item.rankable and item.analysis
                else 0,
                -float(item.analysis.strategy.win_roi or -1e9)
                if item.rankable and item.analysis
                else 0,
                item.event.event_id,
            ),
        )
