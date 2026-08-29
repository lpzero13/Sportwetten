"""Defensive normalization of Tipico JSON payloads.

The UI and storage layers consume the models produced here.  Tipico IDs are
kept as strings so they can never be coerced to floating-point values.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from models.event import LiveEvent
from models.market import EventDetails, Market, Outcome


# Tipico can retain the last numeric quote while an outcome is temporarily
# stopped (for example immediately after a goal). A numeric value alone must
# never make such an outcome wagerable.
PAUSED_STATUSES = {"paused", "suspended", "stopped", "closed", "inactive"}
HALF_TIME_VALUES = {"HZ", "HT", "HALF_TIME", "HALFTIME", "CONFERENCE.TIMECOL.HZ"}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _string(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _id(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _score_pair(value: Any) -> tuple[int | None, int | None]:
    if isinstance(value, Mapping):
        return _int(value.get("count1")), _int(value.get("count2"))
    values = _list(value)
    if len(values) < 2:
        return None, None
    return _int(values[0]), _int(values[1])


def _epoch_ms_to_iso(value: Any) -> str | None:
    number = _float(value)
    if number is None:
        if isinstance(value, str) and value:
            return value
        return None
    try:
        return datetime.fromtimestamp(number / 1000, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _display_time(value: Any) -> str:
    text = _string(value, "—").strip()
    if text.upper() in HALF_TIME_VALUES:
        return "HZ"
    return text or "—"


def _derive_period(event: Mapping[str, Any], display_time: str) -> str:
    explicit = event.get("period") or event.get("phase") or event.get("eventPeriod")
    if explicit:
        return _string(explicit)
    if display_time.upper() == "HZ":
        return "HALF_TIME"
    if bool(event.get("penalties")):
        return "PENALTIES"
    if bool(event.get("extraTime")):
        return "EXTRA_TIME"
    status = _string(event.get("eventState") or event.get("status"), "UNKNOWN")
    return "LIVE" if status.lower() == "running" else status.upper()


def _competition_name_from_detail(event: Mapping[str, Any]) -> str:
    groups = _list(event.get("groups"))
    if groups and groups[0]:
        return _string(groups[0])
    event_info = _string(event.get("eventInfo"))
    if " / " in event_info:
        return event_info.split(" / ", 1)[0]
    return _string(event.get("competitionId"), "Unbekannter Wettbewerb")


def _competition_country_from_detail(event: Mapping[str, Any]) -> str | None:
    """Extract Tipico's country/region without confusing it with the league."""

    def clean(value: Any) -> str:
        # A few previously archived payloads contain the Unicode replacement
        # character in this country name. Tipico's current payload spells it
        # correctly; repairing this known value keeps historical rows usable.
        return _string(value).strip().replace("�sterreich", "Österreich")

    for key in ("countryName", "country", "regionName", "region", "parentName"):
        value = clean(event.get(key))
        if value and value.casefold() not in {"fußball", "fussball", "soccer"}:
            return value
    groups = _list(event.get("groups"))
    competition_name = _competition_name_from_detail(event).casefold()
    for value in groups[1:]:
        candidate = clean(value)
        if candidate and candidate.casefold() not in {
            competition_name,
            "fußball",
            "fussball",
            "soccer",
        }:
            return candidate
    event_info = _string(event.get("eventInfo"))
    if " / " in event_info:
        prefix = clean(event_info.split(" / ", 1)[0])
        if prefix and prefix.casefold() != competition_name:
            return prefix
    return None


def _competition_lookup(live: Mapping[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    competition_map = _mapping(live.get("sportCompetitionMap"))
    for entries in competition_map.values():
        if isinstance(entries, Mapping):
            entries = list(entries.values())
        for item in _list(entries):
            if not isinstance(item, Mapping):
                continue
            competition_id = _id(item.get("groupIdString") or item.get("groupId") or item.get("id"))
            name = item.get("name") or item.get("groupInfo") or item.get("parentName")
            if competition_id and name:
                result[competition_id] = _string(name)
    return result


def _competition_country_lookup(live: Mapping[str, Any]) -> dict[str, str]:
    """Return Tipico's parentName/country for every competition ID."""

    result: dict[str, str] = {}
    competition_map = _mapping(live.get("sportCompetitionMap"))
    for entries in competition_map.values():
        if isinstance(entries, Mapping):
            entries = list(entries.values())
        for item in _list(entries):
            if not isinstance(item, Mapping):
                continue
            competition_id = _id(
                item.get("groupIdString") or item.get("groupId") or item.get("id")
            )
            country = _competition_country_from_detail(item)
            if competition_id and country:
                result[competition_id] = country
    return result


def _red_cards(event: Mapping[str, Any]) -> tuple[int | None, int | None]:
    values = event.get("redCards")
    if values is None:
        values = event.get("redCardsEventDetails")
    return _score_pair(values)


def _build_event(
    event: Mapping[str, Any],
    *,
    event_id: str | None = None,
    scores: Mapping[str, Any] | None = None,
    competition_name: str | None = None,
    competition_country: str | None = None,
    sport: str = "soccer",
) -> LiveEvent:
    resolved_id = _id(event.get("id")) or event_id or ""
    score_data = _mapping(scores)
    if not score_data:
        score_data = _mapping(event.get("eventScores"))
    score_home, score_away = _score_pair(score_data.get("currentScore"))
    ht_home, ht_away = _score_pair(score_data.get("htScore"))
    if score_home is None and score_away is None:
        score_home, score_away = _score_pair(event.get("pointScore"))
    if ht_home is None and ht_away is None:
        ht_home, ht_away = _score_pair(event.get("halftimeScore"))

    display_time = _display_time(event.get("date") or event.get("displayTime"))
    red_home, red_away = _red_cards(event)
    clock_data = _mapping(event.get("clockData"))
    return LiveEvent(
        event_id=resolved_id,
        competition_id=_id(event.get("competitionId") or event.get("groupId")),
        competition_name=competition_name or _competition_name_from_detail(event),
        sport=_string(event.get("sport"), sport),
        home_team=_string(event.get("team1") or event.get("homeTeam"), "Unbekannt"),
        away_team=_string(event.get("team2") or event.get("awayTeam"), "Unbekannt"),
        home_team_id=_id(event.get("team1Id") or event.get("homeTeamId")),
        away_team_id=_id(event.get("team2Id") or event.get("awayTeamId")),
        kickoff_time=_epoch_ms_to_iso(event.get("eventStartTime")),
        status=_string(event.get("eventState") or event.get("status"), "unknown"),
        period=_derive_period(event, display_time),
        display_minute=display_time,
        score_home=score_home,
        score_away=score_away,
        ht_score_home=ht_home,
        ht_score_away=ht_away,
        bet_markets_count=_int(event.get("betMarketsCount") or event.get("betMarketsCountEventDetails")),
        section_number=_int(clock_data.get("sectionNumber")),
        red_cards_home=red_home,
        red_cards_away=red_away,
        sport_radar_match_id=_id(event.get("sportRadarMatchId")),
        bet_genius_id=_id(event.get("betGeniusId")),
        extra_time=bool(event.get("extraTime")) if "extraTime" in event else None,
        penalties=bool(event.get("penalties")) if "penalties" in event else None,
        break_before=event.get("breakBefore"),
        clock_data=clock_data,
        raw_data=dict(event),
        competition_country=competition_country or _competition_country_from_detail(event),
    )


def parse_live_feed(
    payload: Mapping[str, Any],
    *,
    logger: logging.Logger | None = None,
) -> list[LiveEvent]:
    """Parse all soccer events from a Tipico live-feed response."""

    live = _mapping(payload.get("LIVE") if isinstance(payload, Mapping) else None)
    if not live:
        live = _mapping(payload)
    events = _mapping(live.get("events"))
    scores = _mapping(live.get("scores"))
    soccer_event_ids = {
        resolved
        for item in _list(_mapping(live.get("eventsBySport")).get("soccer"))
        if (resolved := _id(item))
    }
    competition_names = _competition_lookup(live)
    competition_countries = _competition_country_lookup(live)
    normalized: list[LiveEvent] = []

    for key, raw_event in events.items():
        if not isinstance(raw_event, Mapping):
            if logger:
                logger.warning("Skipping non-object event payload: %s", key)
            continue
        event_id = _id(raw_event.get("id")) or _id(key)
        if not event_id:
            if logger:
                logger.warning("Skipping event without ID")
            continue
        if soccer_event_ids:
            if event_id not in soccer_event_ids:
                continue
        elif _string(raw_event.get("sport")).lower() != "soccer":
            if logger:
                logger.warning(
                    "Tipico live response did not expose eventsBySport.soccer; "
                    "skipping event %s without explicit soccer sport",
                    event_id,
                )
            continue
        competition_id = _id(raw_event.get("competitionId") or raw_event.get("groupId"))
        normalized.append(
            _build_event(
                raw_event,
                event_id=event_id,
                scores=scores.get(event_id) or scores.get(key),
                competition_name=competition_names.get(
                    competition_id or "",
                    _competition_name_from_detail(raw_event),
                ),
                competition_country=competition_countries.get(
                    competition_id or "",
                    _competition_country_from_detail(raw_event),
                ),
                sport="soccer",
            )
        )
    return normalized


def parse_upcoming_feed(
    payload: Mapping[str, Any],
    *,
    logger: logging.Logger | None = None,
) -> list[LiveEvent]:
    """Parse upcoming soccer events from Tipico's hour-events response."""

    container: dict[str, Any] = {}
    for key in ("UPCOMING", "TOMORROW"):
        candidate = _mapping(payload.get(key) if isinstance(payload, Mapping) else None)
        if candidate:
            container = candidate
            break
    if not container:
        container = _mapping(payload)

    events = _mapping(container.get("events"))
    scores = _mapping(container.get("scores"))
    soccer_event_ids = {
        resolved
        for item in _list(_mapping(container.get("eventsBySport")).get("soccer"))
        if (resolved := _id(item))
    }
    competition_names = _competition_lookup(container)
    competition_regions = _competition_country_lookup(container)

    normalized: list[LiveEvent] = []
    for key, raw_event in events.items():
        if not isinstance(raw_event, Mapping):
            if logger:
                logger.warning("Skipping non-object upcoming event payload: %s", key)
            continue
        event_id = _id(raw_event.get("id")) or _id(key)
        if not event_id:
            continue
        if soccer_event_ids and event_id not in soccer_event_ids:
            continue
        competition_id = _id(raw_event.get("competitionId") or raw_event.get("groupId"))
        competition_name = competition_names.get(
            competition_id or "",
            _competition_name_from_detail(raw_event),
        )
        event = _build_event(
            raw_event,
            event_id=event_id,
            scores=scores.get(event_id) or scores.get(key),
            competition_name=competition_name,
            competition_country=competition_regions.get(
                competition_id or "",
                _competition_country_from_detail(raw_event),
            ),
            sport="soccer",
        )
        region = competition_regions.get(competition_id or "")
        if region:
            event.raw_data["groups"] = [competition_name, region, "Fußball"]
        normalized.append(event)
    return normalized


def _category_names(payload: Mapping[str, Any]) -> dict[str, str]:
    names: dict[str, str] = {}
    for item in _list(payload.get("categories")):
        if isinstance(item, Mapping):
            category_id = _id(item.get("id"))
            if category_id:
                names[category_id] = _string(item.get("name"), category_id)
    return names


def _category_references(
    payload: Mapping[str, Any],
    category_names: Mapping[str, str],
) -> dict[str, list[tuple[str, str]]]:
    references: dict[str, list[tuple[str, str]]] = defaultdict(list)
    sectioned = _mapping(payload.get("categoryOddGroupMapSectioned"))
    for category_key, entries in sectioned.items():
        category_id = _id(category_key) or _string(category_key)
        category_name = category_names.get(category_id, category_id)
        for entry in _list(entries):
            if not isinstance(entry, Mapping):
                continue
            for market_id in _list(entry.get("oddGroupIds")):
                resolved_market_id = _id(market_id)
                if not resolved_market_id:
                    continue
                reference = (category_id, category_name)
                if reference not in references[resolved_market_id]:
                    references[resolved_market_id].append(reference)

    # Some responses may expose only the non-sectioned category map.  Keep
    # this fallback deliberately permissive so unknown shapes remain visible.
    if not references:
        category_map = _mapping(payload.get("categoryOddGroupMap"))
        for category_key, values in category_map.items():
            category_id = _id(category_key) or _string(category_key)
            category_name = category_names.get(category_id, category_id)
            for value in _list(values):
                if isinstance(value, Mapping):
                    ids = value.get("oddGroupIds") or value.get("marketIds") or value.get("ids")
                else:
                    ids = value
                for market_id in _list(ids):
                    resolved_market_id = _id(market_id)
                    if resolved_market_id:
                        reference = (category_id, category_name)
                        if reference not in references[resolved_market_id]:
                            references[resolved_market_id].append(reference)
    return references


def _map_value(mapping: Mapping[str, Any], key: str) -> Any:
    if key in mapping:
        return mapping[key]
    for candidate, value in mapping.items():
        if _id(candidate) == key:
            return value
    return None


def _parse_outcome(
    raw: Mapping[str, Any],
    *,
    outcome_id: str,
    market_id: str,
) -> Outcome:
    status_value = raw.get("status")
    status = _string(status_value).strip().lower() or None
    quote_present = raw.get("quote") is not None and _string(raw.get("quote")).strip() != ""
    quote_float = _float(raw.get("quoteFloatValue"))
    if quote_float is None and quote_present:
        quote_float = _float(raw.get("quote"))
    is_available = quote_present and status not in PAUSED_STATUSES
    return Outcome(
        outcome_id=outcome_id,
        market_id=market_id,
        caption=_string(raw.get("caption"), outcome_id),
        choice_param=_string(raw.get("choiceParam")) or None,
        odds=quote_float if is_available else None,
        status=status,
        is_available=is_available,
        quote_raw=_string(raw.get("quote")) if quote_present else None,
        quote_float_value=quote_float,
        raw_data=dict(raw),
    )


def parse_event_details(
    payload: Mapping[str, Any],
    *,
    event_id: str | None = None,
    logger: logging.Logger | None = None,
) -> EventDetails:
    """Resolve the ID-linked market and outcome graph from an event payload."""

    raw_event = _mapping(payload.get("event"))
    if not raw_event:
        raise ValueError("Tipico event detail did not contain an event object")

    normalized_event = _build_event(raw_event, event_id=event_id, sport="soccer")
    category_names = _category_names(payload)
    references = _category_references(payload, category_names)
    odd_groups = _mapping(payload.get("oddGroups"))
    result_map = _mapping(payload.get("oddGroupResultsMap"))
    results = _mapping(payload.get("results"))

    market_ids: list[str] = []
    for candidate in list(odd_groups) + list(result_map) + list(references):
        resolved = _id(candidate)
        if resolved and resolved not in market_ids:
            market_ids.append(resolved)

    markets: list[Market] = []
    for market_id in market_ids:
        raw_group = _mapping(_map_value(odd_groups, market_id))
        group_references = references.get(market_id, [])
        section_title = ""
        section_type = ""
        if group_references:
            # The definition in oddGroups remains authoritative.  Titles and
            # types from category sections are only fallbacks.
            sectioned = _mapping(payload.get("categoryOddGroupMapSectioned"))
            for entries in sectioned.values():
                for entry in _list(entries):
                    if not isinstance(entry, Mapping):
                        continue
                    if market_id in {_id(item) for item in _list(entry.get("oddGroupIds"))}:
                        section_title = _string(entry.get("oddGroupTitle"))
                        section_type = _string(entry.get("oddGroupType"))
                        break
                if section_title or section_type:
                    break

        raw_result_ids = _map_value(result_map, market_id)
        outcomes: list[Outcome] = []
        for raw_outcome_id in _list(raw_result_ids):
            outcome_id = _id(raw_outcome_id)
            if not outcome_id:
                continue
            raw_outcome = _mapping(_map_value(results, outcome_id))
            if not raw_outcome:
                if logger:
                    logger.warning(
                        "Outcome %s referenced by market %s was not returned",
                        outcome_id,
                        market_id,
                    )
                raw_outcome = {"id": outcome_id}
            outcomes.append(
                _parse_outcome(raw_outcome, outcome_id=outcome_id, market_id=market_id)
            )

        raw_status = _string(raw_group.get("status")).strip().lower()
        if raw_status:
            market_status = raw_status
        elif outcomes and all(not outcome.is_available for outcome in outcomes):
            market_status = "paused"
        else:
            market_status = "open"

        markets.append(
            Market(
                market_id=market_id,
                event_id=normalized_event.event_id,
                caption=_string(
                    raw_group.get("caption"),
                    section_title or f"Market {market_id}",
                ),
                short_caption=_string(raw_group.get("shortCaption")),
                type=_string(
                    raw_group.get("type"),
                    section_type or "unknown",
                ),
                fixed_param=_string(raw_group.get("fixedParam")),
                standard=bool(raw_group.get("standard", False)),
                status=market_status,
                category_ids=[category_id for category_id, _ in group_references],
                category_names=[name for _, name in group_references],
                outcomes=outcomes,
                raw_data=raw_group,
            )
        )

    return EventDetails(
        event=normalized_event,
        markets=markets,
        categories=[dict(item) for item in _list(payload.get("categories")) if isinstance(item, Mapping)],
        raw_data=dict(payload),
    )
