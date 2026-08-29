"""Small repository facades used by the application services."""

from __future__ import annotations

from models.event import LiveEvent
from models.market import EventDetails
from .database import Database, state_from_event


class EventRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def save_observation(self, event: LiveEvent, observed_at: str) -> bool:
        self.database.upsert_event(event, observed_at)
        return self.database.record_event_state_if_changed(
            state_from_event(event, observed_at)
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
        self.database.upsert_event(details.event, observed_at)
        self.database.record_event_state_if_changed(
            state_from_event(details.event, observed_at)
        )
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
