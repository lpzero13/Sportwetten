"""Historical snapshot models used by the background collector."""

from __future__ import annotations

from dataclasses import dataclass


SNAPSHOT_TYPES = {
    "PREMATCH",
    "PRE_KICKOFF",
    "HALFTIME",
    "LIVE_PERIODIC",
    "EVENT_TRIGGERED",
    "FINAL",
    "MANUAL",
}


@dataclass(slots=True)
class Snapshot:
    """One point-in-time event detail observation."""

    event_id: str
    observed_at: str
    snapshot_type: str
    trigger_reason: str | None = None
    match_status: str | None = None
    display_time: str | None = None
    score_home: int | None = None
    score_away: int | None = None
    ht_score_home: int | None = None
    ht_score_away: int | None = None
    market_count: int = 0
    outcome_count: int = 0
    open_outcome_count: int = 0
    paused_outcome_count: int = 0
    snapshot_quality: str | None = None
    raw_payload_path: str | None = None
    second_half_goals: int | None = None
    second_half_goal_class: str | None = None
    snapshot_id: int | None = None

    def __post_init__(self) -> None:
        if self.snapshot_type not in SNAPSHOT_TYPES:
            raise ValueError(f"Unsupported snapshot type: {self.snapshot_type}")
