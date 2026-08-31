"""Models and constants for the opt-in FotMob historical pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


FOTMOB_HISTORICAL_SCHEMA_VERSION = "fotmob_historical_v1"
FOTMOB_HISTORICAL_PARSER_VERSION = "fotmob_historical_parser_v1"
FOTMOB_DETAIL_STATUSES = ("NOT_FETCHED", "IN_PROGRESS", "FETCHED", "PARTIAL", "FAILED")
FOTMOB_DATA_QUALITY = ("COMPLETE", "PARTIAL", "SCORE_ONLY", "INVALID")
FOTMOB_SOURCE_TYPES = (
    "FRESH_INDEX",
    "FRESH_FETCH",
    "LEGACY_IMPORT",
    "LEGACY_VALIDATED",
    "LIVE_HT",
)
FOTMOB_SOURCE_PRIORITY = {
    "FRESH_INDEX": 0,
    "LEGACY_IMPORT": 10,
    "LEGACY_VALIDATED": 20,
    "FRESH_FETCH": 30,
    "LIVE_HT": 40,
}


@dataclass(frozen=True, slots=True)
class FotMobSeasonRef:
    provider: str
    league_id: str
    season_id: str
    season_label: str
    league_name: str | None = None
    country: str | None = None
    discovered_at: str | None = None


@dataclass(frozen=True, slots=True)
class FotMobMatchIndexRecord:
    provider_match_id: str
    league_id: str
    season_id: str
    season_label: str
    kickoff_at: str | None
    home_team_id: str | None
    home_team_name: str
    away_team_id: str | None
    away_team_name: str
    round_name: str | None = None
    match_status: str | None = None
    league_name: str | None = None
    country: str | None = None
    first_seen_at: str | None = None
    provider: str = "FOTMOB"
    source_type: str = "FRESH_INDEX"
    source_context: str | None = None
    stats_period: str | None = None
    captured_live: bool = False
    field_provenance: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class HistoricalDetailResult:
    provider_match_id: str
    status: str
    data_quality: str | None = None
    ml_eligible: bool = False
    second_half_goals: int | None = None
    second_half_goal_class: str | None = None
    error: str | None = None
    archive_path: str | None = None
    source_type: str = "FRESH_FETCH"


def score_target(
    ht_home: int | None,
    ht_away: int | None,
    ft_home: int | None,
    ft_away: int | None,
) -> tuple[int | None, str | None, str | None]:
    """Return (second-half goals, class, quality error) without correction."""

    if None in {ht_home, ht_away, ft_home, ft_away}:
        return None, None, None
    assert ht_home is not None and ht_away is not None
    assert ft_home is not None and ft_away is not None
    second_half_goals = (ft_home + ft_away) - (ht_home + ht_away)
    if second_half_goals < 0:
        return None, None, "INVALID_SCORE_TOTAL_LT_HALFTIME"
    if second_half_goals == 0:
        return 0, "0", None
    if second_half_goals == 1:
        return 1, "1", None
    return second_half_goals, "2_PLUS", None


def historical_row_from_match(
    index: FotMobMatchIndexRecord,
    match: Any,
    *,
    fetched_at: str,
    raw_payload_path: str | None = None,
    source_type: str = "FRESH_FETCH",
    source_context: str | None = "HISTORY_DETAIL",
    stats_period: str | None = "FULL_MATCH",
    captured_live: bool = False,
    field_provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Flatten a normalized :class:`FotMobMatch` into a historical row."""

    ht_stats = match.ht_stats
    ft_stats = match.stats
    second_half_goals, goal_class, score_error = score_target(
        match.ht_score_home,
        match.ht_score_away,
        match.score_home,
        match.score_away,
    )
    score_available = all(
        value is not None
        for value in (
            match.ht_score_home,
            match.ht_score_away,
            match.score_home,
            match.score_away,
        )
    )
    stats_available = bool(ft_stats.has_any_value() or (ht_stats and ht_stats.has_any_value()))
    if score_error:
        quality = "INVALID"
    elif not score_available:
        # Without both HT and FT scores there is no score-only row.  The
        # payload may still contain useful stats/timeline data, but it is an
        # incomplete historical observation.
        quality = "PARTIAL"
    elif not stats_available:
        quality = "SCORE_ONLY"
    elif ht_stats is None or not ht_stats.has_any_value():
        quality = "PARTIAL"
    else:
        quality = "COMPLETE"
    ml_eligible = score_available and quality != "INVALID"

    def stat(stats: Any, name: str) -> Any:
        return getattr(stats, name, None) if stats is not None else None

    row: dict[str, Any] = {
        "schema_version": FOTMOB_HISTORICAL_SCHEMA_VERSION,
        "parser_version": FOTMOB_HISTORICAL_PARSER_VERSION,
        "provider": index.provider,
        "fotmob_match_id": index.provider_match_id,
        "league_id": index.league_id,
        "league_name": index.league_name or match.competition_name,
        "country": index.country or match.competition_country,
        "season_id": index.season_id,
        "season_label": index.season_label,
        "kickoff_at": match.kickoff_at or index.kickoff_at,
        "home_team_id": match.home_team_id or index.home_team_id,
        "home_team_name": match.home_team or index.home_team_name,
        "away_team_id": match.away_team_id or index.away_team_id,
        "away_team_name": match.away_team or index.away_team_name,
        "round_name": match.round_name or index.round_name,
        "match_status": match.status or index.match_status,
        "ht_home": match.ht_score_home,
        "ht_away": match.ht_score_away,
        "ft_home": match.score_home,
        "ft_away": match.score_away,
        "ht_score_source": getattr(match, "ht_score_source", None),
        "ft_score_source": getattr(match, "ft_score_source", None),
        "second_half_goals": second_half_goals,
        "second_half_goal_class": goal_class,
        "ht_extra_stats_json": ht_stats.extra_stats if ht_stats is not None else {},
        "ft_extra_stats_json": ft_stats.extra_stats,
        "timeline_json": [event.to_dict() for event in match.events],
        "data_quality": quality,
        "ml_eligible": ml_eligible,
        "raw_payload_path": raw_payload_path,
        "fetched_at": fetched_at,
        "source_type": source_type,
        "source_context": source_context,
        "stats_period": stats_period,
        "captured_live": bool(captured_live),
        "field_provenance_json": field_provenance or {},
        # These columns intentionally remain separate from the first-half and
        # full-match statistics.  A legacy 60-minute observation must never be
        # mistaken for a FirstHalf value.
        "m60_score_home": None,
        "m60_score_away": None,
        "m60_xg_home": None,
        "m60_xg_away": None,
        "m60_shots_home": None,
        "m60_shots_away": None,
        "m60_shots_on_target_home": None,
        "m60_shots_on_target_away": None,
        "m60_big_chances_home": None,
        "m60_big_chances_away": None,
        "m60_corners_home": None,
        "m60_corners_away": None,
        "m60_yellow_cards_home": None,
        "m60_yellow_cards_away": None,
        "m60_red_cards_home": None,
        "m60_red_cards_away": None,
    }
    for prefix, stats in (("ht", ht_stats), ("ft", ft_stats)):
        for field_name in (
            "xg_home", "xg_away", "shots_home", "shots_away",
            "shots_on_target_home", "shots_on_target_away",
            "big_chances_home", "big_chances_away", "corners_home", "corners_away",
            "possession_home", "possession_away", "yellow_cards_home", "yellow_cards_away",
            "red_cards_home", "red_cards_away", "shots_inside_box_home", "shots_inside_box_away",
            "shots_outside_box_home", "shots_outside_box_away", "touches_in_box_home",
            "touches_in_box_away", "passes_home", "passes_away", "accurate_passes_home",
            "accurate_passes_away", "fouls_home", "fouls_away", "offsides_home", "offsides_away",
            "goalkeeper_saves_home", "goalkeeper_saves_away", "expected_threat_home",
            "expected_threat_away",
        ):
            row[f"{prefix}_{field_name}"] = stat(stats, field_name)
    return row
