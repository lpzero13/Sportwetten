"""Normalized live-event model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class LiveEvent:
    event_id: str
    competition_id: str | None
    competition_name: str
    sport: str
    home_team: str
    away_team: str
    home_team_id: str | None
    away_team_id: str | None
    kickoff_time: str | None
    status: str
    period: str
    display_minute: str
    score_home: int | None
    score_away: int | None
    ht_score_home: int | None
    ht_score_away: int | None
    bet_markets_count: int | None
    first_seen_at: str | None = None
    last_seen_at: str | None = None
    last_updated_at: str | None = None
    section_number: int | None = None
    red_cards_home: int | None = None
    red_cards_away: int | None = None
    sport_radar_match_id: str | None = None
    bet_genius_id: str | None = None
    extra_time: bool | None = None
    penalties: bool | None = None
    break_before: Any = None
    clock_data: dict[str, Any] = field(default_factory=dict)
    raw_data: dict[str, Any] = field(default_factory=dict, repr=False)
    # Tipico exposes the country/region separately from the competition name
    # in the sportCompetitionMap (for example "Deutschland" or "Österreich").
    # Keep it optional for backwards-compatible construction of fixture events.
    competition_country: str | None = None

    @property
    def score_label(self) -> str:
        home = "-" if self.score_home is None else str(self.score_home)
        away = "-" if self.score_away is None else str(self.score_away)
        return f"{home}:{away}"

    @property
    def state_key(self) -> tuple[Any, ...]:
        return (
            self.status,
            self.period,
            self.display_minute,
            self.section_number,
            self.score_home,
            self.score_away,
            self.ht_score_home,
            self.ht_score_away,
            self.red_cards_home,
            self.red_cards_away,
        )
