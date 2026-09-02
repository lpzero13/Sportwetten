"""Small repository facades used by the application services."""

from __future__ import annotations

from models.event import LiveEvent
from models.market import EventDetails
from .database import Database


class EventRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def save_observation(self, event: LiveEvent, observed_at: str) -> bool:
        return self.database.persist_event_observation(
            event,
            observed_at,
            record_history=False,
        )


class MarketRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def save_details(
        self,
        details: EventDetails,
        observed_at: str,
        *,
        store_odds_history: bool = True,
        snapshot_id: int | None = None,
    ) -> int:
        accepted = self.database.persist_event_observation(
            details.event,
            observed_at,
            record_history=True,
        )
        if not accepted:
            return 0
        changes = 0
        for market in details.markets:
            self.database.upsert_market(market, observed_at)
            for outcome in market.outcomes:
                self.database.upsert_outcome(outcome, details.event.event_id, observed_at)
                if store_odds_history and self.database.record_odds_change_if_needed(
                    outcome,
                    details.event.event_id,
                    observed_at,
                    snapshot_id=snapshot_id,
                ):
                    changes += 1
        return changes

    def save_current_details(self, details: EventDetails, observed_at: str) -> bool:
        """Update only operational event/market state; never append history."""

        accepted = self.database.persist_event_observation(
            details.event,
            observed_at,
            record_history=False,
        )
        if not accepted:
            return False
        for market in details.markets:
            self.database.upsert_market(market, observed_at)
            for outcome in market.outcomes:
                self.database.upsert_outcome(outcome, details.event.event_id, observed_at)
        return True
