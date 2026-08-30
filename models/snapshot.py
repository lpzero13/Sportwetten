"""Historical snapshot models used by the background collector."""

from __future__ import annotations

from dataclasses import dataclass


# These are the only historical slots created by the V0.4.2 collector.  The
# legacy names remain accepted by the model so old SQLite databases can still
# be inspected and exported without a destructive migration.
STANDARD_SNAPSHOT_TYPES = (
    "PRE_KICKOFF",
    "HALFTIME",
    "HT_STABLE",
    "MINUTE_60",
    "MINUTE_70",
    "MINUTE_80",
    "FIRST_H2_GOAL_REOPEN",
    "MINUTE_85",
    "MINUTE_90",
    "FINAL",
)
LEGACY_SNAPSHOT_TYPES = {
    "PREMATCH",
    "LIVE_PERIODIC",
    "EVENT_TRIGGERED",
    "MANUAL",
}
SNAPSHOT_TYPES = set(STANDARD_SNAPSHOT_TYPES) | LEGACY_SNAPSHOT_TYPES


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
    competition_id: str | None = None
    competition_name: str | None = None
    competition_country: str | None = None
    home_team: str | None = None
    away_team: str | None = None
    kickoff_time: str | None = None
    match_minute: int | None = None
    q_zero_best: float | None = None
    q_zero_source_type: str | None = None
    q_zero_market_id: str | None = None
    q_zero_outcome_id: str | None = None
    q_two_plus_best: float | None = None
    q_two_plus_source_type: str | None = None
    q_two_plus_market_id: str | None = None
    q_two_plus_outcome_id: str | None = None
    remaining_under_05: float | None = None
    remaining_over_05: float | None = None
    remaining_under_15: float | None = None
    remaining_over_15: float | None = None
    p0_market: float | None = None
    p1_market: float | None = None
    p2plus_market: float | None = None
    p1_break_even: float | None = None
    p1_buffer: float | None = None
    win_roi: float | None = None
    normalizer_version: str | None = None
    strategy_version: str | None = None
    relevant_markets_json: str | None = None
    goal_at: str | None = None
    reopen_at: str | None = None
    reopen_delay_seconds: float | None = None
    archive_path: str | None = None
    exported_at: str | None = None
    payload_hash: str | None = None

    def __post_init__(self) -> None:
        if self.snapshot_type not in SNAPSHOT_TYPES:
            raise ValueError(f"Unsupported snapshot type: {self.snapshot_type}")
