"""Provider-neutral data structures for the optional FotMob source."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping


FOTMOB_SCHEMA_VERSION = "fotmob_snapshot_v1"
FOTMOB_PROVIDERS = ("TIPICO", "FOTMOB")
FOTMOB_SNAPSHOT_TYPES = (
    "PRE_KICKOFF",
    "HALFTIME",
    "HT_STABLE",
    "MINUTE_60",
    "MINUTE_70",
    "MINUTE_80",
    "FINAL",
)


@dataclass(slots=True)
class FotMobStats:
    """Nullable match statistics.

    FotMob can omit individual fields or an entire period.  ``None`` therefore
    means unavailable and is never silently turned into zero.  Possession is
    stored as a percentage in the range 0..100, documented in the V0.5 report.
    """

    xg_home: float | None = None
    xg_away: float | None = None
    shots_home: float | None = None
    shots_away: float | None = None
    shots_on_target_home: float | None = None
    shots_on_target_away: float | None = None
    big_chances_home: float | None = None
    big_chances_away: float | None = None
    corners_home: float | None = None
    corners_away: float | None = None
    possession_home: float | None = None
    possession_away: float | None = None
    yellow_cards_home: float | None = None
    yellow_cards_away: float | None = None
    red_cards_home: float | None = None
    red_cards_away: float | None = None
    shots_inside_box_home: float | None = None
    shots_inside_box_away: float | None = None
    shots_outside_box_home: float | None = None
    shots_outside_box_away: float | None = None
    touches_in_box_home: float | None = None
    touches_in_box_away: float | None = None
    passes_home: float | None = None
    passes_away: float | None = None
    accurate_passes_home: float | None = None
    accurate_passes_away: float | None = None
    fouls_home: float | None = None
    fouls_away: float | None = None
    offsides_home: float | None = None
    offsides_away: float | None = None
    goalkeeper_saves_home: float | None = None
    goalkeeper_saves_away: float | None = None
    expected_threat_home: float | None = None
    expected_threat_away: float | None = None
    extra_stats: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def has_any_value(self) -> bool:
        return any(
            value is not None
            for key, value in self.to_dict().items()
            if key != "extra_stats"
        )


@dataclass(slots=True)
class FotMobEvent:
    """One normalized timeline incident."""

    event_type: str
    minute: int | None = None
    added_time: int | None = None
    team: str | None = None
    player: str | None = None
    detail: str | None = None
    score_home: int | None = None
    score_away: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class FotMobMatch:
    """Normalized FotMob match response with explicit nullable semantics."""

    provider_match_id: str
    kickoff_at: str | None
    competition_id: str | None
    competition_name: str | None
    competition_country: str | None
    home_team: str
    away_team: str
    home_team_id: str | None = None
    away_team_id: str | None = None
    season: str | None = None
    round_name: str | None = None
    status: str | None = None
    period: str | None = None
    minute: int | None = None
    added_time: int | None = None
    score_home: int | None = None
    score_away: int | None = None
    ht_score_home: int | None = None
    ht_score_away: int | None = None
    stats: FotMobStats = field(default_factory=FotMobStats)
    ht_stats: FotMobStats | None = None
    ht_stats_available: bool = False
    events: list[FotMobEvent] = field(default_factory=list)
    extra_data: dict[str, Any] = field(default_factory=dict)
    raw_data: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def is_finished(self) -> bool:
        status = (self.status or "").casefold()
        return status in {"finished", "ended", "completed", "ft", "beendet"}

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["stats"] = self.stats.to_dict()
        result["ht_stats"] = self.ht_stats.to_dict() if self.ht_stats else None
        result["events"] = [item.to_dict() for item in self.events]
        return result


@dataclass(slots=True)
class FotMobSnapshot:
    """Immutable historical slot written to the outbox exactly once."""

    internal_match_id: str
    match: FotMobMatch
    snapshot_type: str
    captured_at: str
    quality: str
    result_consistency: str | None = None
    ht_consistency: str | None = None
    raw_payload_path: str | None = None
    extra_stats: dict[str, Any] = field(default_factory=dict)
    schema_version: str = FOTMOB_SCHEMA_VERSION
    provider: str = "FOTMOB"
    stats_period: str | None = "MATCH"
    source_context: str | None = None
    captured_live: bool = False
    tipico_event_id: str | None = None


@dataclass(slots=True)
class FotMobFetchResult:
    """Client result used by the service and easy to replace in tests."""

    success: bool
    match: FotMobMatch | None = None
    payload: dict[str, Any] | None = None
    status_code: int | None = None
    response_time_ms: int | None = None
    payload_size: int = 0
    endpoint: str | None = None
    error: str | None = None
    attempts: int = 1


def stats_from_mapping(value: Mapping[str, Any] | None) -> FotMobStats:
    """Small helper for callers that already have normalized stat columns."""

    if not isinstance(value, Mapping):
        return FotMobStats()
    allowed = {field for field in FotMobStats.__dataclass_fields__ if field != "extra_stats"}
    values = {key: value.get(key) for key in allowed if key in value}
    extra = value.get("extra_stats")
    if isinstance(extra, Mapping):
        values["extra_stats"] = dict(extra)
    return FotMobStats(**values)
