"""Normalized event-market and outcome models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class Outcome:
    outcome_id: str
    market_id: str
    caption: str
    choice_param: str | None
    odds: float | None
    status: str | None
    is_available: bool
    quote_raw: str | None
    quote_float_value: float | None
    first_seen_at: str | None = None
    last_seen_at: str | None = None
    raw_data: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(slots=True)
class Market:
    market_id: str
    event_id: str
    caption: str
    short_caption: str
    type: str
    fixed_param: str
    standard: bool
    status: str
    category_ids: list[str] = field(default_factory=list)
    category_names: list[str] = field(default_factory=list)
    first_seen_at: str | None = None
    last_seen_at: str | None = None
    outcomes: list[Outcome] = field(default_factory=list)
    raw_data: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(slots=True)
class EventDetails:
    event: Any
    markets: list[Market]
    categories: list[dict[str, Any]]
    raw_data: dict[str, Any]

    @property
    def outcome_count(self) -> int:
        return sum(len(market.outcomes) for market in self.markets)

    @property
    def market_count(self) -> int:
        return len(self.markets)

    @property
    def open_outcome_count(self) -> int:
        return sum(outcome.is_available for market in self.markets for outcome in market.outcomes)

    @property
    def paused_outcome_count(self) -> int:
        return self.outcome_count - self.open_outcome_count
