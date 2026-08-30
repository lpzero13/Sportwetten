"""Defensive extraction of FotMob league, season and fixture index payloads."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any

from .history_models import FotMobMatchIndexRecord, FotMobSeasonRef


_MISSING = object()
_SEASON_RE = re.compile(r"^(?P<first>\d{4})\s*[/\-]\s*(?P<second>\d{2,4})$")


def _first(mapping: Mapping[str, Any] | None, *keys: str) -> Any:
    if not mapping:
        return _MISSING
    lowered = {str(key).casefold(): key for key in mapping}
    for key in keys:
        actual = lowered.get(key.casefold())
        if actual is not None:
            return mapping[actual]
    return _MISSING


def _text(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Mapping):
        for key in ("name", "shortName", "displayName", "label", "text", "value", "type"):
            found = _text(_first(value, key))
            if found:
                return found
        return None
    text = str(value).strip()
    return text or None


def _walk(value: Any) -> Iterable[tuple[str | None, Any]]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            yield str(key), child
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield None, child
            yield from _walk(child)


def _find_named(value: Any, names: set[str]) -> Any:
    wanted = {name.casefold() for name in names}
    for key, child in _walk(value):
        if key is not None and key.casefold() in wanted:
            return child
    return _MISSING


def _id(value: Any) -> str | None:
    if isinstance(value, Mapping):
        raw = _first(value, "id", "seasonId", "season_id", "matchId", "match_id", "eventId", "fixtureId")
    else:
        raw = value
    if raw is _MISSING or raw is None or isinstance(raw, bool):
        return None
    text = str(raw).strip()
    return text or None


def _team(value: Any) -> tuple[str | None, str | None]:
    if isinstance(value, Mapping):
        nested = _first(value, "team", "participant")
        if isinstance(nested, Mapping):
            value = nested
        identifier = _first(value, "id", "teamId", "team_id")
        name = _first(value, "name", "shortName", "displayName", "teamName")
        return (
            None if identifier is _MISSING or identifier is None else str(identifier),
            _text(None if name is _MISSING else name),
        )
    return None, _text(value)


def _score_pair(value: Any) -> tuple[int | None, int | None]:
    def integer(item: Any) -> int | None:
        if item is None or isinstance(item, bool):
            return None
        if isinstance(item, Mapping):
            nested = _first(item, "score", "value", "current", "total")
            if nested is not _MISSING and nested is not item:
                return integer(nested)
        try:
            return int(float(str(item).replace(",", ".")))
        except (TypeError, ValueError):
            return None

    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return integer(value[0]), integer(value[1])
    if isinstance(value, Mapping):
        home = _first(value, "home", "homeScore", "home_score", "currentHome")
        away = _first(value, "away", "awayScore", "away_score", "currentAway")
        if home is not _MISSING or away is not _MISSING:
            return integer(None if home is _MISSING else home), integer(None if away is _MISSING else away)
        for key in ("score", "currentScore", "fullTimeScore", "ftScore"):
            nested = _first(value, key)
            if nested is not _MISSING:
                result = _score_pair(nested)
                if result != (None, None):
                    return result
    return None, None


def _datetime(value: Any) -> str | None:
    if value is None or value is _MISSING:
        return None
    if isinstance(value, (int, float)):
        try:
            timestamp = float(value)
            if timestamp > 100_000_000_000:
                timestamp /= 1000
            return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
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


def normalize_season_label(value: Any) -> str:
    text = (_text(value) or "").replace("–", "-").replace("—", "-")
    match = _SEASON_RE.match(text)
    if not match:
        return text
    second = match.group("second")
    if len(second) == 4:
        return f"{match.group('first')}/{second}"
    return f"{match.group('first')}/{second}"


def _season_items(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, Mapping):
        for key in ("seasons", "items", "options", "data", "values"):
            nested = _first(value, key)
            if nested is not _MISSING:
                items = _season_items(nested)
                if items:
                    return items
        return list(value.values())
    return []


def extract_league_metadata(payload: Mapping[str, Any], league_id: str) -> dict[str, str | None]:
    containers: list[Mapping[str, Any]] = [payload]
    for key in ("league", "competition", "details", "general"):
        value = _find_named(payload, {key})
        if isinstance(value, Mapping):
            containers.append(value)
    name: str | None = None
    country: str | None = None
    resolved_id = str(league_id)
    for container in containers:
        if name is None:
            raw_name = _first(container, "leagueName", "competitionName", "name", "title")
            name = _text(None if raw_name is _MISSING else raw_name)
        if country is None:
            raw_country = _first(container, "country", "countryName", "leagueCountry", "region")
            country = _text(None if raw_country is _MISSING else raw_country)
        raw_id = _first(container, "leagueId", "competitionId")
        if raw_id is not _MISSING and raw_id is not None:
            resolved_id = str(raw_id)
    return {"league_id": resolved_id, "league_name": name, "country": country}


def extract_seasons(
    payload: Mapping[str, Any],
    *,
    league_id: str,
    discovered_at: str | None = None,
) -> list[FotMobSeasonRef]:
    metadata = extract_league_metadata(payload, league_id)
    seasons_node = _find_named(
        payload,
        {"seasons", "availableSeasons", "seasonList", "seasonSelector", "seasonSelection"},
    )
    if seasons_node is _MISSING:
        return []
    result: dict[str, FotMobSeasonRef] = {}
    for item in _season_items(seasons_node):
        if not isinstance(item, Mapping):
            continue
        raw_id = _first(item, "seasonId", "season_id", "id", "key", "valueId")
        raw_label = _first(item, "seasonLabel", "seasonName", "label", "name", "text", "displayName")
        nested_season = _first(item, "season")
        if isinstance(nested_season, Mapping):
            if raw_id is _MISSING:
                raw_id = _first(nested_season, "seasonId", "season_id", "id", "key", "valueId")
            if raw_label is _MISSING:
                raw_label = _first(nested_season, "seasonLabel", "seasonName", "label", "name", "text", "displayName")
        if raw_id is _MISSING or raw_id is None:
            continue
        season_id = _id(raw_id)
        label = normalize_season_label(None if raw_label is _MISSING else raw_label)
        if not season_id or not label:
            continue
        result[season_id] = FotMobSeasonRef(
            provider="FOTMOB",
            league_id=str(metadata["league_id"] or league_id),
            season_id=season_id,
            season_label=label,
            league_name=metadata["league_name"],
            country=metadata["country"],
            discovered_at=discovered_at,
        )
    return sorted(result.values(), key=lambda item: (item.season_label, item.season_id), reverse=True)


def _match_items(payload: Mapping[str, Any]) -> list[Any]:
    for key in ("matches", "fixtures", "results", "games", "events"):
        node = _find_named(payload, {key})
        if node is _MISSING:
            continue
        if isinstance(node, list):
            return node
        if isinstance(node, Mapping):
            for nested_key in ("matches", "fixtures", "results", "games", "events", "allMatches", "all"):
                nested = _first(node, nested_key)
                if isinstance(nested, list):
                    return nested
    return []


def _record_from_item(
    item: Mapping[str, Any],
    *,
    league_id: str,
    season: FotMobSeasonRef,
    league_name: str | None,
    country: str | None,
    first_seen_at: str,
) -> FotMobMatchIndexRecord | None:
    raw_id = _first(item, "matchId", "match_id", "eventId", "fixtureId", "id")
    provider_match_id = _id(raw_id if raw_id is not _MISSING else None)
    if not provider_match_id:
        return None
    home_node = _first(item, "homeTeam", "home_team", "home", "homeParticipant")
    away_node = _first(item, "awayTeam", "away_team", "away", "awayParticipant")
    home_id, home_name = _team(None if home_node is _MISSING else home_node)
    away_id, away_name = _team(None if away_node is _MISSING else away_node)
    if not home_name or not away_name:
        return None
    kickoff_value = _first(
        item,
        "matchTimeUTC", "startTime", "kickoff", "kickoffTime", "date", "timestamp", "utcTime",
    )
    status_value = _first(item, "matchStatus", "status", "state", "phase")
    status = _text(None if status_value is _MISSING else status_value)
    if isinstance(status_value, Mapping):
        if kickoff_value is _MISSING:
            kickoff_value = _first(status_value, "utcTime", "startTime", "kickoff", "kickoffTime")
        finished = _first(status_value, "finished", "isFinished")
        completed = _first(status_value, "completed")
        cancelled = _first(status_value, "cancelled", "isCancelled")
        if finished is True or completed is True:
            status = "finished"
        elif cancelled is True:
            status = "cancelled"
    round_value = _first(item, "matchRound", "round", "roundName", "matchweek", "gameweek")
    round_name = _text(None if round_value is _MISSING else round_value)
    score_home = score_away = None
    home_score = _first(item, "homeScore", "home_score")
    away_score = _first(item, "awayScore", "away_score")
    if home_score is not _MISSING or away_score is not _MISSING:
        score_home, score_away = _score_pair(
            {"home": None if home_score is _MISSING else home_score, "away": None if away_score is _MISSING else away_score}
        )
    else:
        score_node = _first(item, "score", "currentScore", "fullTimeScore", "ftScore")
        if score_node is not _MISSING:
            score_home, score_away = _score_pair(score_node)
    if status is None and score_home is not None and score_away is not None:
        status = "finished"
    return FotMobMatchIndexRecord(
        provider_match_id=provider_match_id,
        league_id=str(league_id),
        season_id=season.season_id,
        season_label=season.season_label,
        kickoff_at=_datetime(kickoff_value),
        home_team_id=home_id,
        home_team_name=home_name,
        away_team_id=away_id,
        away_team_name=away_name,
        round_name=round_name,
        match_status=status,
        league_name=league_name,
        country=country,
        first_seen_at=first_seen_at,
    )


def extract_match_index(
    payload: Mapping[str, Any],
    *,
    league_id: str,
    season: FotMobSeasonRef,
    first_seen_at: str | None = None,
) -> list[FotMobMatchIndexRecord]:
    metadata = extract_league_metadata(payload, league_id)
    seen_at = first_seen_at or datetime.now(timezone.utc).isoformat()
    result: dict[str, FotMobMatchIndexRecord] = {}
    for item in _match_items(payload):
        if not isinstance(item, Mapping):
            continue
        record = _record_from_item(
            item,
            league_id=str(metadata["league_id"] or league_id),
            season=season,
            league_name=metadata["league_name"] or season.league_name,
            country=metadata["country"] or season.country,
            first_seen_at=seen_at,
        )
        if record is not None:
            result.setdefault(record.provider_match_id, record)
    return sorted(result.values(), key=lambda item: (item.kickoff_at or "", item.provider_match_id))


def is_finished_index_record(record: FotMobMatchIndexRecord | Mapping[str, Any]) -> bool:
    status = record.match_status if isinstance(record, FotMobMatchIndexRecord) else record.get("match_status")
    normalized = str(status or "").casefold().replace("_", " ")
    return normalized in {
        "finished", "ended", "completed", "complete", "ft", "full time", "beendet", "final",
    }


def select_reproducible_sample(
    records: Iterable[FotMobMatchIndexRecord],
    count: int = 5,
) -> list[FotMobMatchIndexRecord]:
    """Select deterministic approximate 0/25/50/75/100-percent positions."""

    completed = sorted((item for item in records if is_finished_index_record(item)), key=lambda item: (item.kickoff_at or "", item.provider_match_id))
    if count <= 0 or not completed:
        return []
    if len(completed) <= count:
        return completed
    selected: list[FotMobMatchIndexRecord] = []
    for position in range(count):
        ratio = position / (count - 1) if count > 1 else 0.0
        index = int(ratio * (len(completed) - 1) + 0.5)
        candidate = completed[index]
        if candidate not in selected:
            selected.append(candidate)
    return selected


def season_matches_selector(season: FotMobSeasonRef | Mapping[str, Any], selector: str) -> bool:
    if isinstance(season, FotMobSeasonRef):
        season_id = str(season.season_id)
        label = str(season.season_label)
    else:
        # sqlite3.Row is mapping-like but intentionally does not implement
        # Mapping.get().  Keep the selector usable for catalog rows as well.
        try:
            season_id = str(season["season_id"])
            label = str(season["season_label"])
        except (KeyError, IndexError):
            season_id = ""
            label = ""
    wanted = selector.strip()
    return wanted in {season_id, label, normalize_season_label(wanted)}
