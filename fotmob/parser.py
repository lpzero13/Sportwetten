"""Defensive parser for the public FotMob match response.

FotMob's rendered pages and JSON payloads have changed shape over time.  The
parser therefore uses a small set of known paths plus recursive fallbacks.  It
never treats a missing field as zero and keeps unrecognised statistic pairs in
``extra_stats`` for later schema work.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any

from .models import FotMobEvent, FotMobMatch, FotMobStats


_MISSING = object()
_NUMBER_RE = re.compile(r"[-+]?\d+(?:[.,]\d+)?")
_MINUTE_RE = re.compile(r"^\s*(\d{1,3})(?:\s*\+\s*(\d{1,2}))?\s*['’]?")


def _mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _text(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Mapping):
        for key in ("name", "shortName", "displayName", "text", "value", "type"):
            found = _text(value.get(key))
            if found:
                return found
        return None
    value = str(value).strip()
    return value or None


def _first(mapping: Mapping[str, Any] | None, *keys: str) -> Any:
    if not mapping:
        return _MISSING
    lowered = {str(key).casefold(): key for key in mapping}
    for key in keys:
        actual = lowered.get(key.casefold())
        if actual is not None:
            return mapping[actual]
    return _MISSING


def _walk(value: Any) -> Iterable[tuple[str | None, Any]]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            yield str(key), child
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield None, child
            yield from _walk(child)


def _find_first(value: Any, keys: set[str]) -> Any:
    wanted = {item.casefold() for item in keys}
    for key, child in _walk(value):
        if key is not None and key.casefold() in wanted:
            return child
    return _MISSING


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("\u00a0", " ")
    if not text:
        return None
    # FotMob sometimes sends a localized decimal or a percentage string.
    match = _NUMBER_RE.search(text.replace(" ", ""))
    if not match:
        return None
    candidate = match.group(0).replace(",", ".")
    try:
        return float(candidate)
    except ValueError:
        return None


def _integer(value: Any) -> int | None:
    number = _number(value)
    return None if number is None else int(number)


def _iso_datetime(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _team(value: Any) -> tuple[str | None, str | None]:
    if isinstance(value, Mapping):
        identifier = _first(value, "id", "teamId", "team_id")
        name = _first(value, "name", "shortName", "displayName")
        return (
            None if identifier is _MISSING or identifier is None else str(identifier),
            _text(None if name is _MISSING else name),
        )
    return None, _text(value)


def _score_pair(value: Any) -> tuple[int | None, int | None]:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return _integer(value[0]), _integer(value[1])
    if isinstance(value, Mapping):
        home = _first(value, "home", "homeScore", "home_score", "currentHome")
        away = _first(value, "away", "awayScore", "away_score", "currentAway")
        if home is not _MISSING or away is not _MISSING:
            return (
                _integer(None if home is _MISSING else home),
                _integer(None if away is _MISSING else away),
            )
        for key in ("currentScore", "score", "fullTimeScore", "ftScore"):
            nested = _first(value, key)
            if nested is not _MISSING:
                result = _score_pair(nested)
                if result != (None, None):
                    return result
    return None, None


def _normal_label(value: Any) -> str:
    text = _text(value) or ""
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


def _stat_kind(label: str) -> str | None:
    normalized = _normal_label(label)
    if "expected goals" in normalized or normalized in {"xg", "expected_goals"}:
        return "xg"
    if "big chance" in normalized:
        return "big_chances"
    if "shots on target" in normalized or "shots on goal" in normalized:
        return "shots_on_target"
    if normalized in {"shots", "total shots", "total shot"} or "total shots" in normalized:
        return "shots"
    if "inside box" in normalized and "shot" in normalized:
        return "shots_inside_box"
    if "outside box" in normalized and "shot" in normalized:
        return "shots_outside_box"
    if "touches in box" in normalized or "touches inside box" in normalized:
        return "touches_in_box"
    if "corner" in normalized:
        return "corners"
    if "possession" in normalized:
        return "possession"
    if "yellow" in normalized and "card" in normalized:
        return "yellow_cards"
    if "red" in normalized and "card" in normalized:
        return "red_cards"
    if "accurate pass" in normalized:
        return "accurate_passes"
    if normalized == "passes" or "total passes" in normalized:
        return "passes"
    if "foul" in normalized:
        return "fouls"
    if "offside" in normalized:
        return "offsides"
    if "save" in normalized and ("goalkeeper" in normalized or "keeper" in normalized):
        return "goalkeeper_saves"
    if "expected threat" in normalized or normalized == "xt":
        return "expected_threat"
    return None


def _pair(value: Any) -> tuple[float | None, float | None] | None:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return _number(value[0]), _number(value[1])
    if isinstance(value, Mapping):
        home = _first(value, "home", "homeValue", "home_value", "valueHome")
        away = _first(value, "away", "awayValue", "away_value", "valueAway")
        if home is not _MISSING or away is not _MISSING:
            return (
                _number(None if home is _MISSING else home),
                _number(None if away is _MISSING else away),
            )
        stats_value = _first(value, "stats")
        if isinstance(stats_value, (list, tuple)) and len(stats_value) >= 2:
            return _number(stats_value[0]), _number(stats_value[1])
        values = _first(value, "values", "value")
        if values is not _MISSING and isinstance(values, (list, tuple)) and len(values) >= 2:
            return _number(values[0]), _number(values[1])
    return None


def _collect_stat_pairs(value: Any, parent_label: str | None = None) -> dict[str, tuple[float | None, float | None]]:
    result: dict[str, tuple[float | None, float | None]] = {}

    def visit(node: Any, inherited_label: str | None = None) -> None:
        if isinstance(node, Mapping):
            label_value = _first(node, "title", "name", "label", "key", "statName")
            label = _text(inherited_label if label_value is _MISSING else label_value)
            direct_pair = _pair(node)
            kind = _stat_kind(label or "")
            if direct_pair is not None and kind:
                result[kind] = direct_pair
            for key, child in node.items():
                child_label = label
                if str(key).casefold() not in {"stats", "values", "home", "away"}:
                    child_label = _text(key) or child_label
                if str(key).casefold() == "stats" and isinstance(child, (list, tuple)):
                    scalar_pair = _pair(child)
                    if scalar_pair is not None and kind:
                        result[kind] = scalar_pair
                    else:
                        for item in child:
                            visit(item, label)
                elif str(key).casefold() not in {"home", "away", "homevalue", "awayvalue"}:
                    visit(child, child_label)
        elif isinstance(node, list):
            for item in node:
                visit(item, inherited_label)

    visit(value, parent_label)
    return result


def _stats_from_pairs(
    pairs: Mapping[str, tuple[float | None, float | None]],
    raw_node: Any = None,
) -> FotMobStats:
    kwargs: dict[str, Any] = {}
    for kind, values in pairs.items():
        home, away = values
        if kind == "possession":
            # Explicit storage convention: percentages, not fractions.
            if home is not None and 0 <= home <= 1:
                home *= 100
            if away is not None and 0 <= away <= 1:
                away *= 100
        kwargs[f"{kind}_home"] = home
        kwargs[f"{kind}_away"] = away
    known = set(pairs)
    extras: dict[str, Any] = {}
    if isinstance(raw_node, Mapping):
        for label, values in _collect_unknown_pairs(raw_node):
            if label not in known:
                extras[label] = list(values)
    kwargs["extra_stats"] = extras
    return FotMobStats(**kwargs)


def _collect_unknown_pairs(value: Any) -> Iterable[tuple[str, tuple[float | None, float | None]]]:
    if isinstance(value, Mapping):
        label_value = _first(value, "title", "name", "label", "key", "statName")
        label = _normal_label(None if label_value is _MISSING else label_value)
        pair = _pair(value)
        if label and pair is not None and _stat_kind(label) is None:
            yield label, pair
        for child in value.values():
            yield from _collect_unknown_pairs(child)
    elif isinstance(value, list):
        for child in value:
            yield from _collect_unknown_pairs(child)


def _period_nodes(payload: Mapping[str, Any]) -> tuple[Any, Any, Any]:
    """Return (all-period node, first-half node, source stats object)."""

    stats_root = _find_first(payload, {"stats"})
    if stats_root is _MISSING:
        stats_root = _find_first(payload, {"statistics"})
    if stats_root is _MISSING:
        return _MISSING, _MISSING, _MISSING

    periods = _find_first(stats_root, {"periods"})
    if not isinstance(periods, Mapping):
        return stats_root, _MISSING, stats_root

    all_node: Any = _MISSING
    ht_node: Any = _MISSING
    for key, value in periods.items():
        normalized = _normal_label(key)
        if normalized in {"all", "full time", "full", "match"} or str(key) == "0":
            all_node = value
        elif normalized in {
            "1", "1st", "1st half", "first half", "first", "1h", "ht",
        }:
            ht_node = value
    if all_node is _MISSING and periods:
        # A response with only one explicit period is still safe as the all
        # period, but it must never be promoted to HT implicitly.
        all_node = next(iter(periods.values()))
    return all_node, ht_node, stats_root


def _period_stats(node: Any) -> FotMobStats:
    if node is _MISSING:
        return FotMobStats()
    pairs = _collect_stat_pairs(node)
    return _stats_from_pairs(pairs, node)


def _parse_minute(value: Any) -> tuple[int | None, int | None]:
    if value is None:
        return None, None
    text = str(value).strip()
    match = _MINUTE_RE.search(text)
    if not match:
        return _integer(value), None
    return int(match.group(1)), int(match.group(2)) if match.group(2) else None


def _incidents(payload: Mapping[str, Any]) -> list[Any]:
    for key in ("incidents", "events", "timeline"):
        value = _find_first(payload, {key})
        if isinstance(value, list) and value and all(isinstance(item, Mapping) for item in value):
            return value
    return []


def _parse_events(payload: Mapping[str, Any]) -> list[FotMobEvent]:
    result: list[FotMobEvent] = []
    for raw in _incidents(payload):
        kind = _first(raw, "incidentType", "type", "eventType", "kind", "action")
        event_type = (_text(None if kind is _MISSING else kind) or "unknown").casefold()
        minute_value = _first(raw, "time", "minute", "min", "matchMinute")
        minute, added = _parse_minute(None if minute_value is _MISSING else minute_value)
        added_value = _first(raw, "addedTime", "added_time", "extraTime")
        if added_value is not _MISSING:
            added = _integer(added_value) or added
        home_flag = _first(raw, "isHome", "home", "teamIsHome")
        team = None
        if isinstance(home_flag, bool):
            team = "home" if home_flag else "away"
        if team is None:
            team_value = _first(raw, "team", "side", "teamSide")
            team = _text(None if team_value is _MISSING else team_value)
        player_value = _first(raw, "player", "playerName", "name")
        detail_value = _first(raw, "incidentClass", "detail", "reason", "description")
        score_home, score_away = _score_pair(raw)
        result.append(
            FotMobEvent(
                event_type=event_type,
                minute=minute,
                added_time=added,
                team=team,
                player=_text(None if player_value is _MISSING else player_value),
                detail=_text(None if detail_value is _MISSING else detail_value),
                score_home=score_home,
                score_away=score_away,
                raw=dict(raw),
            )
        )
    return result


def _header_teams(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    header = _find_first(payload, {"header"})
    if isinstance(header, Mapping):
        teams = _first(header, "teams")
        if isinstance(teams, list):
            return [item for item in teams if isinstance(item, Mapping)]
    general = _find_first(payload, {"general"})
    if isinstance(general, Mapping):
        items = []
        for key in ("homeTeam", "awayTeam"):
            value = _first(general, key)
            if isinstance(value, Mapping):
                items.append(value)
        return items
    return []


def parse_fotmob_payload(
    payload: Mapping[str, Any],
    *,
    provider_match_id: str | None = None,
) -> FotMobMatch:
    """Parse a FotMob response without requiring a single fixed schema."""

    if not isinstance(payload, Mapping):
        raise TypeError("FotMob payload must be a mapping")
    general = _find_first(payload, {"general"})
    general = general if isinstance(general, Mapping) else {}
    header = _find_first(payload, {"header"})
    header = header if isinstance(header, Mapping) else {}

    identifier = _first(general, "matchId", "id")
    if identifier is _MISSING:
        identifier = _first(header, "matchId", "id")
    if identifier is _MISSING:
        identifier = _first(payload, "matchId", "id")
    resolved_id = provider_match_id or (None if identifier is _MISSING else str(identifier))
    if not resolved_id:
        raise ValueError("FotMob payload has no match id")

    teams = _header_teams(payload)
    home_id: str | None = None
    away_id: str | None = None
    home_name: str | None = None
    away_name: str | None = None
    score_home: int | None = None
    score_away: int | None = None
    ht_home: int | None = None
    ht_away: int | None = None
    if len(teams) >= 2:
        home_node = _first(teams[0], "team")
        away_node = _first(teams[1], "team")
        home_id, home_name = _team(teams[0] if home_node is _MISSING else home_node)
        away_id, away_name = _team(teams[1] if away_node is _MISSING else away_node)
        for team_node, side in ((teams[0], "home"), (teams[1], "away")):
            score_value = _first(team_node, "score", "currentScore", "fullTimeScore")
            score = _integer(score_value if score_value is not _MISSING else None)
            ht_value = _first(
                team_node,
                "halfTimeScore",
                "halftimeScore",
                "htScore",
                "firstHalfScore",
            )
            ht = _integer(ht_value if ht_value is not _MISSING else None)
            if side == "home":
                score_home, ht_home = score, ht
            else:
                score_away, ht_away = score, ht

    for side, key in (("home", "homeTeam"), ("away", "awayTeam")):
        node = _first(general, key)
        if node is _MISSING:
            continue
        team_id, team_name = _team(node)
        if side == "home":
            home_id, home_name = home_id or team_id, home_name or team_name
        else:
            away_id, away_name = away_id or team_id, away_name or team_name
    if not home_name or not away_name:
        raise ValueError("FotMob payload has no home/away team names")

    score_node = _first(header, "score", "currentScore", "fullTimeScore")
    if score_node is not _MISSING:
        parsed_home, parsed_away = _score_pair(score_node)
        score_home = score_home if score_home is not None else parsed_home
        score_away = score_away if score_away is not None else parsed_away
    ht_node = _first(header, "halfTimeScore", "halftimeScore", "htScore")
    if ht_node is not _MISSING:
        parsed_home, parsed_away = _score_pair(ht_node)
        ht_home = ht_home if ht_home is not None else parsed_home
        ht_away = ht_away if ht_away is not None else parsed_away

    competition = _first(general, "league", "competition")
    competition = competition if isinstance(competition, Mapping) else {}
    competition_id = _first(general, "leagueId", "competitionId")
    if competition_id is _MISSING:
        competition_id = _first(competition, "id", "leagueId")
    competition_name = _first(general, "leagueName", "competitionName", "parentLeagueName")
    if competition_name is _MISSING:
        competition_name = _first(competition, "name", "title")
    country = _first(general, "country", "countryName", "leagueCountry")
    if country is _MISSING:
        country = _first(competition, "country", "countryName")
    season = _first(general, "season")
    round_name = _first(general, "matchRound", "round", "roundName")
    status_node = _first(header, "status")
    status = _text(status_node if status_node is not _MISSING else _first(general, "matchStatus", "status"))
    period_value = _first(header, "period", "matchPeriod")
    if period_value is _MISSING:
        period_value = _first(general, "period", "matchPeriod")
    minute_value = _first(header, "minute", "matchMinute", "liveTime")
    if minute_value is _MISSING:
        minute_value = _first(general, "minute", "matchMinute", "liveTime")
    minute, added_time = _parse_minute(None if minute_value is _MISSING else minute_value)

    kickoff = _first(general, "matchTimeUTC", "startTime", "kickoff", "kickoffTime")
    if kickoff is _MISSING:
        kickoff = _first(header, "startTime", "kickoff", "kickoffTime")
    all_node, first_half_node, stats_source = _period_nodes(payload)
    stats = _period_stats(all_node)
    ht_stats = None if first_half_node is _MISSING else _period_stats(first_half_node)
    events = _parse_events(payload)
    if ht_home is None or ht_away is None:
        # Only derive the score from explicit goal events.  There is no
        # inference from full-time statistics or final score.
        goals = [
            event for event in events
            if "goal" in event.event_type and event.minute is not None and event.minute <= 45
        ]
        if goals and all(event.score_home is not None and event.score_away is not None for event in goals):
            last = goals[-1]
            ht_home, ht_away = last.score_home, last.score_away

    country_text = _text(country if country is not _MISSING else None)
    if isinstance(country, Mapping):
        country_text = _text(country)
    return FotMobMatch(
        provider_match_id=str(resolved_id),
        kickoff_at=_iso_datetime(None if kickoff is _MISSING else kickoff),
        competition_id=None if competition_id is _MISSING else str(competition_id),
        competition_name=_text(None if competition_name is _MISSING else competition_name),
        competition_country=country_text,
        home_team=home_name,
        away_team=away_name,
        home_team_id=home_id,
        away_team_id=away_id,
        season=_text(None if season is _MISSING else season),
        round_name=_text(None if round_name is _MISSING else round_name),
        status=status,
        period=_text(None if period_value is _MISSING else period_value),
        minute=minute,
        added_time=added_time,
        score_home=score_home,
        score_away=score_away,
        ht_score_home=ht_home,
        ht_score_away=ht_away,
        stats=stats,
        ht_stats=ht_stats,
        ht_stats_available=first_half_node is not _MISSING,
        events=events,
        extra_data={
            "stats_source_present": stats_source is not _MISSING,
            "raw_country": country if country is not _MISSING else None,
        },
        raw_data=dict(payload),
    )
