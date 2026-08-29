"""Historized event-state model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class EventState:
    event_id: str
    observed_at: str
    status: str
    period: str
    display_time: str
    section_number: int | None
    score_home: int | None
    score_away: int | None
    ht_score_home: int | None
    ht_score_away: int | None
    red_cards_home: int | None
    red_cards_away: int | None
    raw_state: dict[str, Any] | None = None

    @property
    def relevant_key(self) -> tuple[Any, ...]:
        return (
            self.status,
            self.period,
            self.display_time,
            self.section_number,
            self.score_home,
            self.score_away,
            self.ht_score_home,
            self.ht_score_away,
            self.red_cards_home,
            self.red_cards_away,
        )
