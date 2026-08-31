"""Canonical V0.5.4 FotMob Parquet datasets.

SQLite owns the small daily/index/queue catalog.  This module owns the
provider-shaped historical payload: one match-core row plus long period
statistics, shotmap rows, timeline rows, and live half-time snapshots.
Files use deterministic match names, so rerunning a date range replaces the
same observation instead of appending duplicate training rows.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .history_models import FotMobMatchIndexRecord, score_target
from .models import FotMobMatch, FotMobStats

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
except ImportError:  # pragma: no cover - dependency is declared in requirements
    pa = None
    pq = None


CANONICAL_MATCH_CORE_SCHEMA_VERSION = "fotmob_match_core_v2"
CANONICAL_PERIOD_STATS_SCHEMA_VERSION = "fotmob_period_stats_v1"
CANONICAL_SHOTS_SCHEMA_VERSION = "fotmob_shots_v1"
CANONICAL_EVENTS_SCHEMA_VERSION = "fotmob_events_v1"
CANONICAL_HT_SNAPSHOT_SCHEMA_VERSION = "fotmob_ht_snapshots_v1"
CANONICAL_PARSER_VERSION = "fotmob_canonical_parser_v1"

STAT_NAMES = (
    "xg",
    "shots",
    "shots_on_target",
    "big_chances",
    "corners",
    "possession",
    "yellow_cards",
    "red_cards",
    "fouls",
    "offsides",
    "goalkeeper_saves",
    "passes",
    "accurate_passes",
    "shots_inside_box",
    "shots_outside_box",
    "touches_in_box",
    "expected_threat",
)

PROVIDER_METRIC_NAMES = {
    "xg": "Expected goals (xG)",
    "shots": "Total shots",
    "shots_on_target": "Shots on target",
    "big_chances": "Big chances",
    "corners": "Corners",
    "possession": "Possession",
    "yellow_cards": "Yellow cards",
    "red_cards": "Red cards",
    "fouls": "Fouls",
    "offsides": "Offsides",
    "goalkeeper_saves": "Goalkeeper saves",
    "passes": "Passes",
    "accurate_passes": "Accurate passes",
    "shots_inside_box": "Shots inside box",
    "shots_outside_box": "Shots outside box",
    "touches_in_box": "Touches in box",
    "expected_threat": "Expected threat",
}

COUNTRY_CODES = {
    "de": "GER",
    "deu": "GER",
    "ger": "GER",
    "deutschland": "GER",
    "germany": "GER",
    "at": "AUT",
    "aut": "AUT",
    "österreich": "AUT",
    "osterreich": "AUT",
    "austria": "AUT",
    "gb": "ENG",
    "gbr": "ENG",
    "eng": "ENG",
    "england": "ENG",
    "es": "ESP",
    "esp": "ESP",
    "spanien": "ESP",
    "spain": "ESP",
    "fr": "FRA",
    "fra": "FRA",
    "frankreich": "FRA",
    "france": "FRA",
    "it": "ITA",
    "ita": "ITA",
    "italien": "ITA",
    "italy": "ITA",
    "nl": "NED",
    "nld": "NED",
    "ned": "NED",
    "niederlande": "NED",
    "netherlands": "NED",
}
COUNTRY_NAMES = {
    "GER": "Deutschland",
    "DEU": "Deutschland",
    "AUT": "Österreich",
    "ENG": "England",
    "ESP": "Spanien",
    "FRA": "Frankreich",
    "ITA": "Italien",
    "NED": "Niederlande",
}


def _safe(value: Any, default: str = "unknown") -> str:
    text = str(value or default).strip().replace("/", "-").replace("\\", "-")
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text)
    return text or default


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))


def _hash(value: Any) -> str | None:
    if value is None:
        return None
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _first(mapping: Mapping[str, Any] | None, *keys: str) -> Any:
    if not mapping:
        return None
    lowered = {str(key).casefold(): key for key in mapping}
    for key in keys:
        actual = lowered.get(key.casefold())
        if actual is not None:
            return mapping[actual]
    return None


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool) or isinstance(value, (Mapping, list, tuple)):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", ".")
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _integer(value: Any) -> int | None:
    number = _number(value)
    return None if number is None else int(number)


def _text(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Mapping):
        return _text(_first(value, "name", "shortName", "displayName", "text", "value", "type"))
    text = str(value).strip()
    return text or None


def _iso_date(value: Any) -> str | None:
    text = str(value or "").strip()
    return text[:10] if len(text) >= 10 else None


def _country(value: Any) -> tuple[str | None, str | None]:
    text = _text(value)
    if not text:
        return None, None
    code = COUNTRY_CODES.get(text.casefold())
    if code is None and len(text) in {2, 3}:
        code = text.upper()
    if code is None:
        code = text.upper()
    return code, COUNTRY_NAMES.get(code, text)


def _content(payload: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        return {}
    candidates = (
        ("props", "pageProps", "content"),
        ("pageProps", "content"),
        ("content",),
    )
    for path in candidates:
        current: Any = payload
        for key in path:
            if not isinstance(current, Mapping):
                break
            current = _first(current, key)
        if isinstance(current, Mapping):
            return current
    return {}


def _period_label(minute: int | None) -> str:
    if minute is None:
        return "UNKNOWN"
    return "FIRST_HALF" if minute <= 45 else "SECOND_HALF"


def _stat_value(stats: FotMobStats | None, name: str, side: str) -> float | None:
    if stats is None:
        return None
    value = getattr(stats, f"{name}_{side}", None)
    return float(value) if value is not None else None


def _identity(index: FotMobMatchIndexRecord, match: FotMobMatch) -> dict[str, Any]:
    country_code = getattr(index, "country_code", None)
    country_name = getattr(index, "country_name", None)
    if not country_code or not country_name:
        fallback_code, fallback_name = _country(index.country or match.competition_country)
        country_code = country_code or fallback_code
        country_name = country_name or fallback_name
    return {
        "provider": str(index.provider or "FOTMOB").upper(),
        "fotmob_match_id": str(index.provider_match_id),
        "internal_match_id": None,
        "league_id": str(index.league_id),
        "league_name": index.league_name or match.competition_name,
        "country_code": country_code,
        "country_name": country_name,
        "season_id": str(index.season_id),
        "season_label": index.season_label,
        "kickoff_at_utc": match.kickoff_at or index.kickoff_at,
        "home_team_id": match.home_team_id or index.home_team_id,
        "home_team_name": match.home_team or index.home_team_name,
        "away_team_id": match.away_team_id or index.away_team_id,
        "away_team_name": match.away_team or index.away_team_name,
    }


def _quality(match: FotMobMatch) -> tuple[str, bool, int | None, str | None]:
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
    stats_available = bool(
        match.stats.has_any_value()
        or (match.ht_stats is not None and match.ht_stats.has_any_value())
    )
    if score_error:
        quality = "INVALID"
    elif not score_available:
        quality = "PARTIAL"
    elif not stats_available:
        quality = "SCORE_ONLY"
    elif match.ht_stats is None or not match.ht_stats.has_any_value():
        quality = "PARTIAL"
    else:
        quality = "COMPLETE"
    return quality, bool(score_available and quality != "INVALID"), second_half_goals, goal_class


def match_core_row(
    index: FotMobMatchIndexRecord,
    match: FotMobMatch,
    *,
    fetched_at: str,
    payload_hash: str | None = None,
    source_type: str = "FRESH_FETCH",
    source_context: str | None = "DAILY_DETAIL",
    stats_period: str | None = "FULL_MATCH",
    captured_live: bool = False,
    internal_match_id: str | None = None,
    field_provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the fixed-column ``fotmob_match_core_v2`` row."""

    identity = _identity(index, match)
    identity["internal_match_id"] = internal_match_id
    quality, ml_eligible, second_half_goals, goal_class = _quality(match)
    row: dict[str, Any] = {
        "schema_version": CANONICAL_MATCH_CORE_SCHEMA_VERSION,
        "parser_version": CANONICAL_PARSER_VERSION,
        **identity,
        "ht_score_home": match.ht_score_home,
        "ht_score_away": match.ht_score_away,
        "ft_score_home": match.score_home,
        "ft_score_away": match.score_away,
        "second_half_goals": second_half_goals,
        "second_half_goal_class": goal_class,
        "data_quality": quality,
        "ml_eligible": ml_eligible,
        "ht_score_source": match.ht_score_source,
        "ft_score_source": match.ft_score_source,
        "source_type": str(source_type or "FRESH_FETCH").upper(),
        "source_context": source_context,
        "stats_period": stats_period,
        "captured_live": bool(captured_live),
        "fetched_at": fetched_at,
        "payload_hash": payload_hash,
        "ht_extra_stats_json": _json(match.ht_stats.extra_stats if match.ht_stats else {}),
        "ft_extra_stats_json": _json(match.stats.extra_stats),
        "field_provenance_json": _json(field_provenance or {}),
        "m60_score_home": None,
        "m60_score_away": None,
        "m60_xg_home": None,
        "m60_xg_away": None,
        "m60_shots_home": None,
        "m60_shots_away": None,
        "m60_shots_on_target_home": None,
        "m60_shots_on_target_away": None,
        "m60_corners_home": None,
        "m60_corners_away": None,
        "m60_source": "NOT_AVAILABLE",
    }
    for prefix, stats in (("ht", match.ht_stats), ("ft", match.stats)):
        for name in STAT_NAMES:
            for side in ("home", "away"):
                row[f"{prefix}_{name}_{side}"] = _stat_value(stats, name, side)
    return row


def period_stat_rows(
    index: FotMobMatchIndexRecord,
    match: FotMobMatch,
    *,
    fetched_at: str,
    internal_match_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return all normalized known and unknown metrics in long format."""

    identity = _identity(index, match)
    identity["internal_match_id"] = internal_match_id
    result: list[dict[str, Any]] = []
    periods = (
        ("FIRST_HALF", match.ht_stats),
        ("SECOND_HALF", match.second_half_stats),
        ("ALL", match.stats),
    )
    for period, stats in periods:
        if stats is None:
            continue
        for name in STAT_NAMES:
            home = _stat_value(stats, name, "home")
            away = _stat_value(stats, name, "away")
            if home is None and away is None:
                continue
            unit = "percent" if name == "possession" else "goals" if name == "xg" else "count"
            result.append(
                {
                    "schema_version": CANONICAL_PERIOD_STATS_SCHEMA_VERSION,
                    "parser_version": CANONICAL_PARSER_VERSION,
                    **identity,
                    "period": period,
                    "metric_key": name,
                    "provider_metric_name": PROVIDER_METRIC_NAMES.get(name, name),
                    "home_value": home,
                    "away_value": away,
                    "unit": unit,
                    "fetched_at": fetched_at,
                    "extra_json": "{}",
                }
            )
        for metric_name, value in stats.extra_stats.items():
            if isinstance(value, (list, tuple)) and len(value) >= 2:
                home, away = _number(value[0]), _number(value[1])
            elif isinstance(value, Mapping):
                home, away = _number(_first(value, "home", "homeValue")), _number(
                    _first(value, "away", "awayValue")
                )
            else:
                home = away = _number(value)
            if home is None and away is None:
                continue
            key = re.sub(r"[^a-z0-9]+", "_", str(metric_name).casefold()).strip("_") or "unknown"
            result.append(
                {
                    "schema_version": CANONICAL_PERIOD_STATS_SCHEMA_VERSION,
                    "parser_version": CANONICAL_PARSER_VERSION,
                    **identity,
                    "period": period,
                    "metric_key": key,
                    "provider_metric_name": str(metric_name),
                    "home_value": home,
                    "away_value": away,
                    "unit": None,
                    "fetched_at": fetched_at,
                    "extra_json": _json({"raw_value": value}),
                }
            )
    return result


def shot_rows(
    index: FotMobMatchIndexRecord,
    match: FotMobMatch,
    payload: Mapping[str, Any] | None,
    *,
    fetched_at: str,
    internal_match_id: str | None = None,
) -> list[dict[str, Any]]:
    content = _content(payload or match.raw_data)
    shotmap = _first(content, "shotmap")
    if isinstance(shotmap, Mapping):
        raw_shots = _first(shotmap, "shots", "items")
    else:
        raw_shots = shotmap
    if not isinstance(raw_shots, list):
        return []
    identity = _identity(index, match)
    identity["internal_match_id"] = internal_match_id
    result: list[dict[str, Any]] = []
    for position, raw in enumerate(raw_shots):
        if not isinstance(raw, Mapping):
            continue
        team_id = _first(raw, "teamId", "team_id")
        team_id_text = str(team_id) if team_id is not None else None
        is_home_value = _first(raw, "isHome", "isHomeTeam", "home")
        is_home = bool(is_home_value) if isinstance(is_home_value, bool) else None
        if is_home is None and team_id_text is not None:
            if identity["home_team_id"] is not None and team_id_text == str(identity["home_team_id"]):
                is_home = True
            elif identity["away_team_id"] is not None and team_id_text == str(identity["away_team_id"]):
                is_home = False
        minute = _integer(_first(raw, "min", "minute", "time"))
        added = _integer(_first(raw, "addedTime", "added_time", "extraTime"))
        player = _first(raw, "player")
        player_id = _first(raw, "playerId", "player_id")
        player_name = _text(_first(raw, "playerName")) or _text(player)
        if isinstance(player, Mapping) and player_id is None:
            player_id = _first(player, "id", "playerId", "player_id")
        shot_id = _first(raw, "id", "shotId", "shot_id", "eventId")
        result.append(
            {
                "schema_version": CANONICAL_SHOTS_SCHEMA_VERSION,
                "parser_version": CANONICAL_PARSER_VERSION,
                **identity,
                "shot_id": str(shot_id) if shot_id is not None else f"{identity['fotmob_match_id']}-{position}",
                "team_id": team_id_text,
                "is_home": is_home,
                "period": _period_label(minute),
                "minute": minute,
                "added_time": added,
                "xg": _number(_first(raw, "expectedGoals", "xg", "expected_goals")),
                "xgot": _number(_first(raw, "expectedGoalsOnTarget", "xgot", "expectedGoalsOnTarget")),
                "outcome": _text(_first(raw, "eventType", "outcome", "result")),
                "shot_type": _text(_first(raw, "shotType", "type")),
                "situation": _text(_first(raw, "situation", "shotSituation")),
                "body_part": _text(_first(raw, "bodyPart", "bodypart", "bodyPartType")),
                "x": _number(_first(raw, "x", "X")),
                "y": _number(_first(raw, "y", "Y")),
                "player_id": str(player_id) if player_id is not None else None,
                "player_name": player_name,
                "fetched_at": fetched_at,
                "extra_json": _json(dict(raw)),
            }
        )
    return result


def _event_type(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", (_text(value) or "UNKNOWN").casefold()).strip("_")
    aliases = {
        "goal": "GOAL",
        "own_goal": "OWN_GOAL",
        "owngoal": "OWN_GOAL",
        "penalty_goal": "PENALTY_GOAL",
        "penaltygoal": "PENALTY_GOAL",
        "missed_penalty": "MISSED_PENALTY",
        "missedpenalty": "MISSED_PENALTY",
        "yellow_card": "YELLOW_CARD",
        "yellowcard": "YELLOW_CARD",
        "red_card": "RED_CARD",
        "redcard": "RED_CARD",
        "second_yellow": "SECOND_YELLOW",
        "secondyellow": "SECOND_YELLOW",
        "substitution": "SUBSTITUTION",
        "var": "VAR",
    }
    return aliases.get(text, text.upper() or "UNKNOWN")


def event_rows(
    index: FotMobMatchIndexRecord,
    match: FotMobMatch,
    *,
    fetched_at: str,
    internal_match_id: str | None = None,
) -> list[dict[str, Any]]:
    identity = _identity(index, match)
    identity["internal_match_id"] = internal_match_id
    result: list[dict[str, Any]] = []
    for position, event in enumerate(match.events):
        result.append(
            {
                "schema_version": CANONICAL_EVENTS_SCHEMA_VERSION,
                "parser_version": CANONICAL_PARSER_VERSION,
                **identity,
                "event_id": str(_first(event.raw, "id", "eventId") or f"{identity['fotmob_match_id']}-{position}"),
                "event_type": _event_type(event.event_type),
                "period": _period_label(event.minute),
                "minute": event.minute,
                "added_time": event.added_time,
                "team_id": event.team_id,
                "is_home": event.is_home,
                "player_id": event.player_id,
                "player_name": event.player,
                "score_home_after": event.score_home,
                "score_away_after": event.score_away,
                "fetched_at": fetched_at,
                "extra_json": _json(event.raw),
            }
        )
    return result


def ht_snapshot_row(
    index: FotMobMatchIndexRecord,
    match: FotMobMatch,
    *,
    captured_at: str,
    internal_match_id: str | None = None,
    tipico_event_id: str | None = None,
    matching_status: str | None = None,
    matching_confidence: float | None = None,
    source_context: str = "LIVE_HT",
) -> dict[str, Any]:
    identity = _identity(index, match)
    identity["internal_match_id"] = internal_match_id
    row: dict[str, Any] = {
        "schema_version": CANONICAL_HT_SNAPSHOT_SCHEMA_VERSION,
        "parser_version": CANONICAL_PARSER_VERSION,
        **identity,
        "tipico_event_id": tipico_event_id,
        "captured_at": captured_at,
        "snapshot_type": "HALFTIME",
        "stats_period": "FIRST_HALF",
        "source_context": source_context,
        "matching_status": matching_status,
        "matching_confidence": matching_confidence,
        "ht_score_home": match.ht_score_home,
        "ht_score_away": match.ht_score_away,
    }
    for name in STAT_NAMES:
        row[f"ht_{name}_home"] = _stat_value(match.ht_stats, name, "home")
        row[f"ht_{name}_away"] = _stat_value(match.ht_stats, name, "away")
    row["ht_extra_stats_json"] = _json(match.ht_stats.extra_stats if match.ht_stats else {})
    return row


def _schema(fields: list[tuple[str, Any]]) -> Any:
    return pa.schema([pa.field(name, type_, nullable=True) for name, type_ in fields]) if pa is not None else None


if pa is not None:
    _IDENTITY_FIELDS = [
        ("provider", pa.string()), ("fotmob_match_id", pa.string()),
        ("internal_match_id", pa.string()), ("league_id", pa.string()),
        ("league_name", pa.string()), ("country_code", pa.string()),
        ("country_name", pa.string()), ("season_id", pa.string()),
        ("season_label", pa.string()), ("kickoff_at_utc", pa.string()),
        ("home_team_id", pa.string()), ("home_team_name", pa.string()),
        ("away_team_id", pa.string()), ("away_team_name", pa.string()),
    ]
    _STAT_FIELDS = [
        (f"{prefix}_{name}_{side}", pa.float64())
        for prefix in ("ht", "ft")
        for name in STAT_NAMES
        for side in ("home", "away")
    ]
    CORE_SCHEMA = _schema(
        [("schema_version", pa.string()), ("parser_version", pa.string())]
        + _IDENTITY_FIELDS
        + [
            ("ht_score_home", pa.int64()), ("ht_score_away", pa.int64()),
            ("ft_score_home", pa.int64()), ("ft_score_away", pa.int64()),
            ("second_half_goals", pa.int64()), ("second_half_goal_class", pa.string()),
            ("data_quality", pa.string()), ("ml_eligible", pa.bool_()),
            ("ht_score_source", pa.string()), ("ft_score_source", pa.string()),
            ("source_type", pa.string()), ("source_context", pa.string()),
            ("stats_period", pa.string()), ("captured_live", pa.bool_()),
            ("fetched_at", pa.string()), ("payload_hash", pa.string()),
        ]
        + _STAT_FIELDS
        + [
            ("ht_extra_stats_json", pa.string()), ("ft_extra_stats_json", pa.string()),
            ("field_provenance_json", pa.string()),
            ("m60_score_home", pa.int64()), ("m60_score_away", pa.int64()),
            ("m60_xg_home", pa.float64()), ("m60_xg_away", pa.float64()),
            ("m60_shots_home", pa.float64()), ("m60_shots_away", pa.float64()),
            ("m60_shots_on_target_home", pa.float64()), ("m60_shots_on_target_away", pa.float64()),
            ("m60_corners_home", pa.float64()), ("m60_corners_away", pa.float64()),
            ("m60_source", pa.string()),
        ]
    )
    PERIOD_SCHEMA = _schema(
        [("schema_version", pa.string()), ("parser_version", pa.string())]
        + _IDENTITY_FIELDS
        + [
            ("period", pa.string()), ("metric_key", pa.string()),
            ("provider_metric_name", pa.string()), ("home_value", pa.float64()),
            ("away_value", pa.float64()), ("unit", pa.string()),
            ("fetched_at", pa.string()), ("extra_json", pa.string()),
        ]
    )
    SHOT_SCHEMA = _schema(
        [("schema_version", pa.string()), ("parser_version", pa.string())]
        + _IDENTITY_FIELDS
        + [
            ("shot_id", pa.string()), ("team_id", pa.string()), ("is_home", pa.bool_()),
            ("period", pa.string()), ("minute", pa.int64()), ("added_time", pa.int64()),
            ("xg", pa.float64()), ("xgot", pa.float64()), ("outcome", pa.string()),
            ("shot_type", pa.string()), ("situation", pa.string()), ("body_part", pa.string()),
            ("x", pa.float64()), ("y", pa.float64()), ("player_id", pa.string()),
            ("player_name", pa.string()), ("fetched_at", pa.string()), ("extra_json", pa.string()),
        ]
    )
    EVENT_SCHEMA = _schema(
        [("schema_version", pa.string()), ("parser_version", pa.string())]
        + _IDENTITY_FIELDS
        + [
            ("event_id", pa.string()), ("event_type", pa.string()), ("period", pa.string()),
            ("minute", pa.int64()), ("added_time", pa.int64()), ("team_id", pa.string()),
            ("is_home", pa.bool_()), ("player_id", pa.string()), ("player_name", pa.string()),
            ("score_home_after", pa.int64()), ("score_away_after", pa.int64()),
            ("fetched_at", pa.string()), ("extra_json", pa.string()),
        ]
    )
    HT_SNAPSHOT_SCHEMA = _schema(
        [("schema_version", pa.string()), ("parser_version", pa.string())]
        + _IDENTITY_FIELDS
        + [
            ("tipico_event_id", pa.string()), ("captured_at", pa.string()),
            ("snapshot_type", pa.string()), ("stats_period", pa.string()),
            ("source_context", pa.string()), ("matching_status", pa.string()),
            ("matching_confidence", pa.float64()), ("ht_score_home", pa.int64()),
            ("ht_score_away", pa.int64()),
        ]
        + [
            (f"ht_{name}_{side}", pa.float64())
            for name in STAT_NAMES for side in ("home", "away")
        ]
        + [("ht_extra_stats_json", pa.string())]
    )
else:  # pragma: no cover
    CORE_SCHEMA = PERIOD_SCHEMA = SHOT_SCHEMA = EVENT_SCHEMA = HT_SNAPSHOT_SCHEMA = None


class FotMobCanonicalArchive:
    """Crash-safe deterministic writer for the V0.5.4 FotMob archive."""

    def __init__(self, root: Path | str, compression: str = "zstd") -> None:
        self.root = Path(root)
        self.compression = str(compression or "zstd").lower()
        self._lock = threading.RLock()
        self.root.mkdir(parents=True, exist_ok=True)

    @property
    def total_size_bytes(self) -> int:
        total = 0
        if not self.root.exists():
            return 0
        for path in self.root.rglob("*.parquet"):
            try:
                total += path.stat().st_size
            except OSError:
                continue
        return total

    def read_match_core(self, archive_path: str | Path | None) -> dict[str, Any] | None:
        """Read one compact match-core row for UI/detail inspection."""

        if pq is None or not archive_path:
            return None
        path = Path(str(archive_path))
        if not path.is_absolute():
            path = self.root / path
        if not path.exists():
            return None
        try:
            rows = pq.read_table(path).to_pylist()
        except (OSError, ValueError, TypeError):
            return None
        return rows[0] if rows else None

    def _write_one(
        self,
        dataset: str,
        partition: tuple[str, ...],
        stem: str,
        row: Mapping[str, Any] | None = None,
        rows: list[Mapping[str, Any]] | None = None,
        schema: Any = None,
    ) -> str:
        if pa is None or pq is None:
            raise RuntimeError("pyarrow is required for the canonical FotMob archive")
        values = rows if rows is not None else [row or {}]
        partition_fields = {
            part.split("=", 1)[0]
            for part in partition
            if "=" in part
        }
        # Hive partition columns are supplied by the dataset reader.  Keeping
        # a second physical ``league_id`` column would make pyarrow try to
        # merge string data with its inferred integer partition value.
        physical_schema = pa.schema(
            [field for field in schema if field.name not in partition_fields]
        )
        fields = {field.name for field in physical_schema}
        normalized = [{key: value for key, value in item.items() if key in fields} for item in values]
        directory = self.root / dataset
        for part in partition:
            directory /= part
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / f"{_safe(stem)}.parquet"
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        if temporary.exists():
            temporary.unlink()
        table = pa.Table.from_pylist(normalized, schema=physical_schema)
        pq.write_table(
            table,
            temporary,
            compression="zstd" if self.compression in {"zstd", "zst"} else self.compression,
            use_dictionary=True,
        )
        with temporary.open("r+b") as handle:
            os.fsync(handle.fileno())
        temporary.replace(destination)
        return str(destination)

    def write_match(
        self,
        index: FotMobMatchIndexRecord,
        match: FotMobMatch,
        payload: Mapping[str, Any] | None = None,
        *,
        fetched_at: str,
        source_type: str = "FRESH_FETCH",
        source_context: str | None = "DAILY_DETAIL",
        stats_period: str | None = "FULL_MATCH",
        captured_live: bool = False,
        internal_match_id: str | None = None,
        field_provenance: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload_value = payload if isinstance(payload, Mapping) else match.raw_data
        payload_hash = _hash(payload_value)
        core = match_core_row(
            index,
            match,
            fetched_at=fetched_at,
            payload_hash=payload_hash,
            source_type=source_type,
            source_context=source_context,
            stats_period=stats_period,
            captured_live=captured_live,
            internal_match_id=internal_match_id,
            field_provenance=field_provenance,
        )
        periods = period_stat_rows(index, match, fetched_at=fetched_at, internal_match_id=internal_match_id)
        shots = shot_rows(index, match, payload_value, fetched_at=fetched_at, internal_match_id=internal_match_id)
        events = event_rows(index, match, fetched_at=fetched_at, internal_match_id=internal_match_id)
        season = _safe(index.season_label or index.season_id, "unknown-season")
        league = _safe(index.league_id)
        match_stem = f"match-{index.provider_match_id}"
        paths: list[str] = []
        with self._lock:
            paths.append(self._write_one("match_core", (f"league_id={league}", f"season={season}"), match_stem, row=core, schema=CORE_SCHEMA))
            if periods:
                paths.append(self._write_one("period_stats", (f"league_id={league}", f"season={season}"), match_stem, rows=periods, schema=PERIOD_SCHEMA))
            elif (self.root / "period_stats").exists():
                # A previous period file must not survive a re-fetch that now
                # explicitly contains no period stats.
                old = self.root / "period_stats" / f"league_id={league}" / f"season={season}" / f"{_safe(match_stem)}.parquet"
                old.unlink(missing_ok=True)
            if shots:
                paths.append(self._write_one("shots", (f"league_id={league}", f"season={season}"), match_stem, rows=shots, schema=SHOT_SCHEMA))
            else:
                old = self.root / "shots" / f"league_id={league}" / f"season={season}" / f"{_safe(match_stem)}.parquet"
                old.unlink(missing_ok=True)
            if events:
                paths.append(self._write_one("events", (f"league_id={league}", f"season={season}"), match_stem, rows=events, schema=EVENT_SCHEMA))
            else:
                old = self.root / "events" / f"league_id={league}" / f"season={season}" / f"{_safe(match_stem)}.parquet"
                old.unlink(missing_ok=True)
        return {
            "written": 1,
            "period_stats_rows": len(periods),
            "shot_rows": len(shots),
            "event_rows": len(events),
            "paths": paths,
            "payload_hash": payload_hash,
            "bytes": sum(Path(path).stat().st_size for path in paths if Path(path).exists()),
        }

    def write_ht_snapshot(self, row: Mapping[str, Any]) -> dict[str, Any]:
        if not row.get("fotmob_match_id"):
            raise ValueError("HT snapshot requires fotmob_match_id")
        captured_at = str(row.get("captured_at") or datetime.now(timezone.utc).isoformat())
        day = _iso_date(captured_at) or "unknown-date"
        league = _safe(row.get("league_id"))
        season = _safe(row.get("season_label") or row.get("season_id"), "unknown-season")
        match_stem = f"match-{row['fotmob_match_id']}-{str(row.get('snapshot_type', 'HALFTIME')).lower()}"
        with self._lock:
            path = self._write_one(
                "ht_snapshots",
                (f"date={day}", f"league_id={league}", f"season={season}"),
                match_stem,
                row=row,
                schema=HT_SNAPSHOT_SCHEMA,
            )
        return {"written": 1, "paths": [path], "bytes": Path(path).stat().st_size if Path(path).exists() else 0}
