"""Volatile FotMob live data for the selected Tipico event.

This module deliberately does not know how to persist a FotMob observation.
It reads an already persisted Tipico-to-FotMob link, fetches one public
``matchDetails`` response for the selected match, normalizes the response in
memory and discards the provider payload afterwards.  The historical,
canonical and halftime services remain separate.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from .matching import AUTO_LINK_STATUSES, MatchIdentity, MatchMatchResult, MatchMatcher
from .models import FotMobFetchResult, FotMobMatch, FotMobStats
from .storage import internal_match_id_for_tipico


ACCEPTED_LINK_STATUSES = AUTO_LINK_STATUSES
TERMINAL_LIVE_STATUSES = frozenset({"NO_DATA", "FINISHED"})
LIVE_STAT_KEYS = (
    "xg",
    "xgot",
    "shots",
    "shots_on_target",
    "big_chances",
    "big_chances_missed",
    "shots_inside_box",
    "shots_outside_box",
    "touches_in_box",
    "corners",
    "possession",
    "yellow_cards",
    "red_cards",
    "fouls",
    "offsides",
    "goalkeeper_saves",
    "passes",
    "accurate_passes",
)

DETAILED_CORE_KEYS = (
    "shots",
    "shots_on_target",
    "big_chances",
    "corners",
    "possession",
)

STAT_LABELS = {
    "xg": "xG",
    "xgot": "xGOT",
    "shots": "Schüsse",
    "shots_on_target": "Schüsse aufs Tor",
    "big_chances": "Großchancen",
    "big_chances_missed": "Großchancen vergeben",
    "shots_inside_box": "Schüsse im Strafraum",
    "shots_outside_box": "Schüsse außerhalb des Strafraums",
    "touches_in_box": "Ballkontakte im Strafraum",
    "corners": "Ecken",
    "possession": "Ballbesitz",
    "yellow_cards": "Gelbe Karten",
    "red_cards": "Rote Karten",
    "fouls": "Fouls",
    "offsides": "Abseits",
    "goalkeeper_saves": "Torwartparaden",
    "passes": "Pässe",
    "accurate_passes": "Angekommene Pässe",
}

Pair = tuple[int | float | None, int | float | None]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_value(row: Any, name: str) -> Any:
    if row is None:
        return None
    if isinstance(row, Mapping):
        return row.get(name)
    keys = getattr(row, "keys", None)
    if callable(keys):
        try:
            if name in keys():
                return row[name]
        except (TypeError, KeyError):
            pass
    return getattr(row, name, None)


def _event_id(event: Any) -> str | None:
    if event is None:
        return None
    if isinstance(event, str):
        return event
    value = getattr(event, "event_id", None)
    return str(value) if value is not None else None


def _provider_match_id(value: Any) -> str:
    """Accept either a numeric ID or a copied FotMob match URL."""

    text = str(value or "").strip()
    if re.fullmatch(r"\d+", text):
        return text
    fragment = re.search(r"(?:#|[?&](?:matchId|match_id)=)(\d+)\s*$", text, re.IGNORECASE)
    return fragment.group(1) if fragment else ""


def _is_finished_value(value: Any) -> bool:
    normalized = _normalized_label(value)
    return normalized in {"finished", "ended", "completed", "ft", "beendet"}


def _text(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Mapping):
        for key in ("name", "shortName", "displayName", "text", "value", "type"):
            if key in value:
                found = _text(value[key])
                if found:
                    return found
        return None
    text = str(value).strip()
    return text or None


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool) or isinstance(value, Mapping):
        return None
    if isinstance(value, (list, tuple)):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"[-+]?\d+(?:[.,]\d+)?", str(value).replace("\u00a0", " "))
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", "."))
    except ValueError:
        return None


def _integer(value: Any) -> int | None:
    number = _number(value)
    return None if number is None else int(number)


def _pair(value: Any) -> Pair | None:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        home, away = _number(value[0]), _number(value[1])
        return (home, away) if home is not None or away is not None else None
    if not isinstance(value, Mapping):
        return None
    home = next(
        (value[key] for key in ("home", "homeValue", "home_value", "valueHome") if key in value),
        None,
    )
    away = next(
        (value[key] for key in ("away", "awayValue", "away_value", "valueAway") if key in value),
        None,
    )
    if home is not None or away is not None:
        parsed_home, parsed_away = _number(home), _number(away)
        return (parsed_home, parsed_away) if parsed_home is not None or parsed_away is not None else None
    for key in ("stats", "values", "value"):
        child = value.get(key)
        if isinstance(child, (list, tuple)) and len(child) >= 2:
            return _pair(child)
    return None


def _normalized_label(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (_text(value) or "").casefold()).strip()


def _metric_key(label: Any) -> str | None:
    normalized = _normalized_label(label)
    if "expected goals on target" in normalized or "xgot" in normalized or "xg on target" in normalized:
        return "xgot"
    if "big chance" in normalized and "missed" in normalized:
        return "big_chances_missed"
    if "expected goals" in normalized or normalized == "xg":
        return "xg"
    if "big chance" in normalized:
        return "big_chances"
    if "shots on target" in normalized or "shots on goal" in normalized:
        return "shots_on_target"
    if "inside box" in normalized and "shot" in normalized:
        return "shots_inside_box"
    if "outside box" in normalized and "shot" in normalized:
        return "shots_outside_box"
    if "touches in box" in normalized or "touches inside box" in normalized or "touches in opposition box" in normalized:
        return "touches_in_box"
    if normalized in {"shots", "total shots", "total shot"} or "total shots" in normalized:
        return "shots"
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
    return None


def _collect_raw_stat_pairs(value: Any, inherited_label: str | None = None) -> dict[str, Pair]:
    """Collect provider stat pairs without changing the shared parser."""

    result: dict[str, Pair] = {}

    def visit(node: Any, parent_label: str | None = None) -> None:
        if isinstance(node, Mapping):
            label_node = next(
                (node[key] for key in ("title", "name", "label", "key", "statName") if key in node),
                None,
            )
            label = _text(label_node) or parent_label
            kind = _metric_key(label)
            direct_pair = _pair(node)
            if kind in LIVE_STAT_KEYS and direct_pair is not None:
                result[kind] = direct_pair
            for key, child in node.items():
                key_text = str(key).casefold()
                if key_text in {"home", "away", "homevalue", "awayvalue", "valuehome", "valueaway"}:
                    continue
                visit(child, label)
        elif isinstance(node, (list, tuple)):
            for child in node:
                visit(child, parent_label)

    visit(value, inherited_label)
    return result


def _find_first_key(value: Any, wanted: set[str]) -> Any:
    wanted = {item.casefold() for item in wanted}
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).casefold() in wanted:
                return child
            found = _find_first_key(child, wanted)
            if found is not None:
                return found
    elif isinstance(value, (list, tuple)):
        for child in value:
            found = _find_first_key(child, wanted)
            if found is not None:
                return found
    return None


def _period_nodes(payload: Mapping[str, Any]) -> dict[str, Any]:
    periods = _find_first_key(payload, {"periods"})
    if not isinstance(periods, Mapping):
        return {}
    aliases = {
        "ALL": {"all", "full", "full time", "match", "0"},
        "FIRST_HALF": {"1", "1st", "1st half", "first half", "firsthalf", "first", "1h"},
        "SECOND_HALF": {"2", "2nd", "2nd half", "second half", "secondhalf", "second", "2h"},
    }
    result: dict[str, Any] = {}
    for key, child in periods.items():
        normalized = _normalized_label(key)
        for period, names in aliases.items():
            if normalized in names:
                result[period] = child
                break
    return result


def _normalize_pair(pair: Pair | None, key: str) -> Pair:
    if pair is None:
        return (None, None)
    home, away = pair
    if key == "possession":
        if home is not None and 0 <= home <= 1:
            home *= 100
        if away is not None and 0 <= away <= 1:
            away *= 100
    return (home, away)


def _stats_dict(stats: FotMobStats | None, raw_node: Any = None) -> dict[str, Pair]:
    raw_pairs = _collect_raw_stat_pairs(raw_node) if raw_node is not None else {}
    extra_pairs: dict[str, Pair] = {}
    if stats is not None:
        extra_stats = (
            stats.get("extra_stats", {})
            if isinstance(stats, Mapping)
            else getattr(stats, "extra_stats", {})
        )
        for label, value in extra_stats.items() if isinstance(extra_stats, Mapping) else ():
            pair = _pair(value)
            if pair is not None:
                kind = _metric_key(label)
                if kind:
                    extra_pairs[kind] = pair
    result: dict[str, Pair] = {}
    for key in LIVE_STAT_KEYS:
        model_pair: Pair | None = None
        if isinstance(stats, Mapping) and (
            f"{key}_home" in stats or f"{key}_away" in stats
        ):
            model_pair = (
                stats.get(f"{key}_home"),
                stats.get(f"{key}_away"),
            )
        elif stats is not None and hasattr(stats, f"{key}_home"):
            model_pair = (
                getattr(stats, f"{key}_home"),
                getattr(stats, f"{key}_away"),
            )
        pair = raw_pairs.get(key) or extra_pairs.get(key) or model_pair
        result[key] = _normalize_pair(pair, key)
    return result


def _has_detailed_stats(stats: Mapping[str, Pair]) -> bool:
    available = sum(
        1
        for key in DETAILED_CORE_KEYS
        if stats.get(key, (None, None))[0] is not None
        and stats.get(key, (None, None))[1] is not None
    )
    return available >= 3


def _parse_minute(value: Any) -> tuple[int | None, int | None]:
    text = str(value).strip() if value is not None else ""
    match = re.match(r"^(\d{1,3})(?:\s*\+\s*(\d{1,2}))?", text)
    if match:
        return int(match.group(1)), int(match.group(2)) if match.group(2) else None
    return _integer(value), None


def _shotmap(payload: Mapping[str, Any] | None) -> list[Mapping[str, Any]] | None:
    if not isinstance(payload, Mapping):
        return None
    value = _find_first_key(payload, {"shotmap"})
    if isinstance(value, Mapping):
        value = next((value[key] for key in ("shots", "items") if key in value), None)
    if not isinstance(value, list):
        return None
    return [item for item in value if isinstance(item, Mapping)]


def _shot_is_home(raw: Mapping[str, Any], match: FotMobMatch) -> bool | None:
    for key in ("isHome", "isHomeTeam", "home"):
        value = raw.get(key)
        if isinstance(value, bool):
            return value
    team_id = next((raw[key] for key in ("teamId", "team_id") if key in raw), None)
    if team_id is not None:
        if match.home_team_id is not None and str(team_id) == str(match.home_team_id):
            return True
        if match.away_team_id is not None and str(team_id) == str(match.away_team_id):
            return False
    team = _normalized_label(next((raw[key] for key in ("team", "side", "teamSide") if key in raw), None))
    if team in {"home", "heim"}:
        return True
    if team in {"away", "auswarts", "auswärts"}:
        return False
    return None


def _shot_on_target(raw: Mapping[str, Any]) -> bool:
    for key in ("isOnTarget", "onTarget", "on_target"):
        if isinstance(raw.get(key), bool):
            return bool(raw[key])
    text = " ".join(
        str(raw[key])
        for key in ("eventType", "outcome", "result", "shotType")
        if raw.get(key) is not None
    ).casefold()
    return any(token in text for token in ("goal", "saved", "save", "on target", "ontarget"))


def _last_15_stats(match: FotMobMatch, payload: Mapping[str, Any] | None) -> tuple[dict[str, Pair], bool, int]:
    shots = _shotmap(payload)
    if shots is None or match.minute is None:
        return {
            "xg": (None, None),
            "shots": (None, None),
            "shots_on_target": (None, None),
        }, shots is not None, len(shots or [])

    start_minute = max(0, int(match.minute) - 15)
    counts = {"shots": [0, 0], "shots_on_target": [0, 0]}
    xg_values: list[float | None] = [None, None]
    for raw in shots:
        minute_value = next((raw[key] for key in ("min", "minute", "time") if key in raw), None)
        minute, _ = _parse_minute(minute_value)
        if minute is None or minute < start_minute or minute > int(match.minute):
            continue
        side = _shot_is_home(raw, match)
        if side is None:
            continue
        index = 0 if side else 1
        counts["shots"][index] += 1
        if _shot_on_target(raw):
            counts["shots_on_target"][index] += 1
        xg = _number(next((raw[key] for key in ("expectedGoals", "xg", "expected_goals") if key in raw), None))
        if xg is not None:
            xg_values[index] = (xg if xg_values[index] is None else xg_values[index] + xg)
    return {
        "xg": (xg_values[0], xg_values[1]),
        "shots": (counts["shots"][0], counts["shots"][1]),
        "shots_on_target": (counts["shots_on_target"][0], counts["shots_on_target"][1]),
    }, True, len(shots)


def normalize_live_match(
    match: FotMobMatch,
    payload: Mapping[str, Any] | None = None,
    *,
    fetched_at: str | None = None,
) -> FotMobLiveData:
    """Normalize only display-safe fields and discard raw provider structure."""

    source = payload if isinstance(payload, Mapping) else match.raw_data
    period_nodes = _period_nodes(source if isinstance(source, Mapping) else {})
    all_stats = _stats_dict(match.stats, period_nodes.get("ALL"))
    first_stats = _stats_dict(match.ht_stats, period_nodes.get("FIRST_HALF")) if (
        match.ht_stats is not None or "FIRST_HALF" in period_nodes
    ) else None
    second_stats = _stats_dict(match.second_half_stats, period_nodes.get("SECOND_HALF")) if (
        match.second_half_stats is not None or "SECOND_HALF" in period_nodes
    ) else None
    last_15, shotmap_available, shotmap_total = _last_15_stats(match, source if isinstance(source, Mapping) else None)
    return FotMobLiveData(
        provider_match_id=str(match.provider_match_id),
        fetched_at=fetched_at or _now_iso(),
        match_status=match.status,
        minute=match.minute,
        added_time=match.added_time,
        period=match.period,
        home_score=match.score_home,
        away_score=match.score_away,
        home_team=match.home_team,
        away_team=match.away_team,
        competition_name=match.competition_name,
        competition_country=match.competition_country,
        stats=all_stats,
        periods={
            "ALL": all_stats,
            "FIRST_HALF": first_stats,
            "SECOND_HALF": second_stats,
        },
        last_15=last_15,
        shotmap_available=shotmap_available,
        shotmap_total=shotmap_total,
    )


@dataclass(slots=True)
class FotMobLiveData:
    """Small normalized in-memory representation; it contains no raw payload."""

    provider_match_id: str
    fetched_at: str
    match_status: str | None
    minute: int | None
    added_time: int | None
    period: str | None
    home_score: int | None
    away_score: int | None
    home_team: str
    away_team: str
    competition_name: str | None
    competition_country: str | None
    stats: dict[str, Pair]
    periods: dict[str, dict[str, Pair] | None]
    last_15: dict[str, Pair]
    shotmap_available: bool
    shotmap_total: int

    def to_dict(self) -> dict[str, Any]:
        def pairs(value: Mapping[str, Pair] | None) -> dict[str, dict[str, Any]] | None:
            if value is None:
                return None
            return {
                key: {"home": pair[0], "away": pair[1]}
                for key, pair in value.items()
            }

        result = asdict(self)
        result["stats"] = pairs(self.stats)
        result["periods"] = {key: pairs(value) for key, value in self.periods.items()}
        result["last_15"] = pairs(self.last_15)
        return result


@dataclass(slots=True)
class FotMobLiveResult:
    """Result of a volatile live lookup, including cache and availability state."""

    status: str
    provider_match_id: str | None = None
    data: FotMobLiveData | None = None
    availability_status: str | None = None
    fetched_at: str | None = None
    last_request_at: str | None = None
    cache_hit: bool = False
    request_made: bool = False
    error: str | None = None
    consecutive_no_data_count: int = 0
    successful_payloads: int = 0
    consecutive_errors: int = 0
    retry_delay_seconds: int = 0
    response_time_ms: int | None = None
    payload_size: int = 0
    parse_duration_ms: int | None = None

    @property
    def detailed_data_available(self) -> bool:
        return self.availability_status == "DETAILED_DATA_AVAILABLE"

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_LIVE_STATUSES

    @property
    def should_auto_refresh(self) -> bool:
        return self.status not in TERMINAL_LIVE_STATUSES and self.status != "DISABLED" and self.status != "NO_MATCH"


@dataclass(slots=True)
class FotMobManualBindingResult:
    """Result of an explicit, selected-match FotMob-ID check.

    The result deliberately contains no provider payload or ``FotMobMatch``.
    A successful check only keeps the accepted ID and normalized live data in
    the service's RAM cache for the current application session.
    """

    success: bool
    provider_match_id: str | None = None
    match_status: str | None = None
    confidence: float = 0.0
    reasons: list[str] = field(default_factory=list)
    live_result: FotMobLiveResult | None = None
    match_result: MatchMatchResult | None = None
    error: str | None = None


@dataclass(slots=True)
class _CacheEntry:
    provider_match_id: str
    data: FotMobLiveData | None = None
    status: str = "PENDING"
    availability_status: str | None = None
    fetched_at: str | None = None
    last_request_at: str | None = None
    last_request_monotonic: float | None = None
    next_allowed_monotonic: float = 0.0
    consecutive_no_data_count: int = 0
    successful_payloads: int = 0
    consecutive_errors: int = 0
    error: str | None = None
    response_time_ms: int | None = None
    payload_size: int = 0
    parse_duration_ms: int | None = None


class FotMobLiveService:
    """Read-only live boundary with a RAM-only cache and explicit backoff.

    A persisted accepted link is preferred.  For a selected match, the UI may
    also submit one explicit FotMob match ID.  That ID is validated against
    the selected Tipico event and kept only in RAM; it never becomes an
    automated all-events discovery path and never writes live values to
    SQLite/Parquet.
    """

    def __init__(
        self,
        fotmob_service: Any,
        *,
        client: Any | None = None,
        cache_ttl_seconds: float = 8.0,
        refresh_seconds: int = 10,
        pending_minute: int = 10,
        no_data_payload_threshold: int = 3,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.fotmob_service = fotmob_service
        self.client = client if client is not None else getattr(fotmob_service, "client", None)
        self.cache_ttl_seconds = max(0.0, float(cache_ttl_seconds))
        self.refresh_seconds = max(1, int(refresh_seconds))
        self.pending_minute = max(0, int(pending_minute))
        self.no_data_payload_threshold = max(1, int(no_data_payload_threshold))
        self._clock = clock or time.monotonic
        self._cache: dict[str, _CacheEntry] = {}
        # event_id -> (provider_match_id, status).  These are deliberate
        # session-level overrides for a selected event, not persistent links.
        self._manual_links: dict[str, tuple[str, str]] = {}
        self._lock = threading.RLock()
        self._metrics: dict[str, Any] = {
            "requests": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "terminal_skips": 0,
            "no_selected_match": 0,
            "errors": 0,
            "last_request_at": None,
            "last_provider_match_id": None,
        }

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.fotmob_service, "enabled", True))

    @property
    def manual_use_allowed(self) -> bool:
        return bool(getattr(self.fotmob_service, "manual_use_allowed", True))

    def _matcher(self) -> MatchMatcher:
        """Reuse the persistent service's aliases without performing a write."""

        factory = getattr(self.fotmob_service, "_matcher", None)
        if callable(factory):
            try:
                matcher = factory()
                if isinstance(matcher, MatchMatcher):
                    return matcher
            except (AttributeError, KeyError, TypeError, ValueError):
                # A small test adapter may not expose all FotMob store tables.
                # The fallback still applies the safe base matching rules.
                pass
        settings = getattr(self.fotmob_service, "settings", None)
        tolerance = getattr(settings, "fotmob_matching_tolerance_minutes", 15)
        return MatchMatcher(tolerance_minutes=int(tolerance))

    def _match_candidate(self, event: Any, match: FotMobMatch) -> MatchMatchResult:
        return self._matcher().match(
            MatchIdentity.from_tipico_event(event),
            [match],
        )

    def _link_for_event(self, event: Any) -> tuple[str, Any] | None:
        event_id = _event_id(event)
        if not event_id:
            return None
        with self._lock:
            manual = self._manual_links.get(event_id)
        if manual is not None:
            provider_id, status = manual
            return provider_id, {
                "provider_match_id": provider_id,
                "match_status": status,
                "match_confidence": 1.0,
                "manual_session_link": True,
            }
        store = getattr(self.fotmob_service, "store", None)
        if store is None:
            return None
        rich_link = getattr(self.fotmob_service, "provider_event_link_for_event", None)
        if callable(rich_link):
            try:
                row = rich_link(event)
            except (AttributeError, KeyError, TypeError, ValueError):
                row = None
            if row is not None:
                status = str(_row_value(row, "match_status") or "").upper()
                # ``FotMobService`` falls back to the V0.5.3 legacy relation
                # on an older database.  Accept its provider_match_id shape
                # here as well; a missing rich row must not disable a valid
                # migrated/live link.
                provider_id = _row_value(row, "fotmob_match_id") or _row_value(
                    row, "provider_match_id"
                )
                if status in ACCEPTED_LINK_STATUSES and provider_id not in (None, ""):
                    return str(provider_id), row
                # A rich row is authoritative, including an explicit
                # AMBIGUOUS/UNMATCHED/INVALIDATED decision.  Do not revive a
                # stale V0.5.3 legacy link behind it.
                return None
        try:
            row = store.link_for_internal(internal_match_id_for_tipico(event_id), "FOTMOB")
        except TypeError:
            # Small fakes and older adapters may expose the provider as a
            # fixed default instead of accepting the second argument.
            row = store.link_for_internal(internal_match_id_for_tipico(event_id))
        if row is None:
            return None
        status = str(_row_value(row, "match_status") or "").upper()
        provider_id = _row_value(row, "provider_match_id")
        if status not in ACCEPTED_LINK_STATUSES or provider_id in (None, ""):
            return None
        return str(provider_id), row

    def auto_link_for_event(self, event: Any) -> Any | None:
        """Resolve the selected event from the cached daily index on demand."""

        resolver = getattr(self.fotmob_service, "resolver", None)
        resolve = getattr(resolver, "resolve", None)
        if not callable(resolve):
            return None
        try:
            result = resolve(event)
        except Exception:  # provider/resolver boundary must not break the UI
            return None
        return result

    def provider_match_id_for_event(self, event: Any) -> str | None:
        linked = self._link_for_event(event)
        return linked[0] if linked else None

    def has_confirmed_link_for_tipico_event(self, event_id: str) -> bool:
        return self.provider_match_id_for_event(str(event_id)) is not None

    def has_accepted_link_for_tipico_event(self, event_id: str) -> bool:
        """Alias that makes the accepted-status rule explicit to UI callers."""

        return self.has_confirmed_link_for_tipico_event(event_id)

    def bind_manual_match_id(
        self,
        event: Any | None,
        provider_match_id: str,
    ) -> FotMobManualBindingResult:
        """Validate and bind one explicitly selected FotMob match in RAM.

        This is intentionally a user-triggered single-match operation.  It
        makes one detail request, checks the returned teams/kickoff/
        competition with the existing deterministic matcher and, on success,
        seeds the normal volatile live cache so the response is not fetched a
        second time in the same render cycle.
        """

        event_id = _event_id(event)
        normalized_id = _provider_match_id(provider_match_id)
        if not event_id:
            return FotMobManualBindingResult(False, error="Kein Tipico-Live-Spiel ausgewählt.")
        if not re.fullmatch(r"\d+", normalized_id):
            return FotMobManualBindingResult(
                False,
                error="Die FotMob Match-ID muss eine numerische ID sein, z. B. 6003655.",
            )
        if not self.enabled or not self.manual_use_allowed:
            return FotMobManualBindingResult(
                False,
                provider_match_id=normalized_id,
                error="FotMob-Einzelspielnutzung ist durch die aktuelle Konfiguration deaktiviert.",
            )
        if self.client is None:
            return FotMobManualBindingResult(
                False,
                provider_match_id=normalized_id,
                error="Kein FotMob-Live-Client konfiguriert.",
            )

        with self._lock:
            now = self._clock()
            request_iso = _now_iso()
            self._metrics["requests"] += 1
            self._metrics["last_provider_match_id"] = normalized_id
            self._metrics["last_request_at"] = request_iso
            try:
                fetched = self.client.fetch_match_details(normalized_id)
            except Exception as exc:  # provider boundary: keep the dashboard alive
                self._metrics["errors"] += 1
                return FotMobManualBindingResult(
                    False,
                    provider_match_id=normalized_id,
                    error=str(exc),
                )
            if not isinstance(fetched, FotMobFetchResult) and not hasattr(fetched, "success"):
                self._metrics["errors"] += 1
                return FotMobManualBindingResult(
                    False,
                    provider_match_id=normalized_id,
                    error="Ungültige FotMob-Client-Antwort.",
                )
            if not bool(getattr(fetched, "success", False)) or getattr(fetched, "match", None) is None:
                self._metrics["errors"] += 1
                return FotMobManualBindingResult(
                    False,
                    provider_match_id=normalized_id,
                    error=str(getattr(fetched, "error", None) or "FotMob-Match konnte nicht gelesen werden."),
                )

            match = fetched.match
            match_result = self._match_candidate(event, match)
            if not match_result.auto_linkable:
                return FotMobManualBindingResult(
                    False,
                    provider_match_id=normalized_id,
                    match_status=match_result.status,
                    confidence=match_result.confidence,
                    reasons=list(match_result.reasons),
                    match_result=match_result,
                    error=(
                        "FotMob-Match wurde gelesen, aber nicht sicher diesem Tipico-Spiel "
                        f"zugeordnet ({match_result.status})."
                    ),
                )

            persist_link = getattr(self.fotmob_service, "persist_manual_link", None)
            if callable(persist_link):
                try:
                    # The link/evidence is durable, while the fetched live
                    # payload remains exclusively in this service's RAM
                    # cache.  Older lightweight adapters simply do not expose
                    # this optional persistence hook.
                    persist_link(event, match, reason="manual_live_confirmation")
                except Exception:
                    # A temporary persistence problem must not discard a
                    # validated, user-requested live view.
                    pass
            self._manual_links[event_id] = (normalized_id, "MANUALLY_CONFIRMED")
            entry = self._cache.setdefault(
                normalized_id,
                _CacheEntry(provider_match_id=normalized_id),
            )
            live_result = self._ingest_fetched_response(
                event,
                normalized_id,
                entry,
                fetched,
                request_iso=request_iso,
                now=now,
            )
            return FotMobManualBindingResult(
                True,
                provider_match_id=normalized_id,
                match_status=match_result.status,
                confidence=match_result.confidence,
                reasons=list(match_result.reasons),
                live_result=live_result,
                match_result=match_result,
            )

    def _result(self, entry: _CacheEntry | None, *, status: str, provider_id: str | None, cache_hit: bool, request_made: bool) -> FotMobLiveResult:
        now = self._clock()
        retry_delay = 0
        if entry is not None:
            retry_delay = max(0, int(round(entry.next_allowed_monotonic - now)))
        return FotMobLiveResult(
            status=status,
            provider_match_id=provider_id,
            data=entry.data if entry else None,
            availability_status=entry.availability_status if entry else None,
            fetched_at=entry.fetched_at if entry else None,
            last_request_at=entry.last_request_at if entry else None,
            cache_hit=cache_hit,
            request_made=request_made,
            error=entry.error if entry else None,
            consecutive_no_data_count=entry.consecutive_no_data_count if entry else 0,
            successful_payloads=entry.successful_payloads if entry else 0,
            consecutive_errors=entry.consecutive_errors if entry else 0,
            retry_delay_seconds=retry_delay,
            response_time_ms=entry.response_time_ms if entry else None,
            payload_size=entry.payload_size if entry else 0,
            parse_duration_ms=entry.parse_duration_ms if entry else None,
        )

    def cached_for_event(self, event: Any) -> FotMobLiveResult | None:
        linked = self._link_for_event(event)
        if linked is None:
            return None
        provider_id, _ = linked
        with self._lock:
            entry = self._cache.get(provider_id)
            return self._result(entry, status=entry.status, provider_id=provider_id, cache_hit=True, request_made=False) if entry else None

    def _record_error(self, entry: _CacheEntry, error: str, now: float, request_started_at: str) -> FotMobLiveResult:
        entry.status = "ERROR"
        entry.error = error
        entry.last_request_at = request_started_at
        entry.last_request_monotonic = now
        entry.consecutive_errors += 1
        entry.next_allowed_monotonic = now + min(40, 10 * (2 ** (entry.consecutive_errors - 1)))
        self._metrics["errors"] += 1
        return self._result(entry, status="ERROR", provider_id=entry.provider_match_id, cache_hit=False, request_made=True)

    def _ingest_fetched_response(
        self,
        event: Any,
        provider_id: str,
        entry: _CacheEntry,
        fetched: Any,
        *,
        request_iso: str,
        now: float,
    ) -> FotMobLiveResult:
        """Normalize one successful provider response into the volatile cache."""

        response_ms = getattr(fetched, "response_time_ms", None)
        payload_size = int(getattr(fetched, "payload_size", 0) or 0)
        if not isinstance(fetched, FotMobFetchResult) and not hasattr(fetched, "success"):
            return self._record_error(entry, "Ungültige FotMob-Client-Antwort", now, request_iso)
        if not bool(getattr(fetched, "success", False)) or getattr(fetched, "match", None) is None:
            return self._record_error(
                entry,
                str(getattr(fetched, "error", None) or "FotMob-Live-Abruf fehlgeschlagen"),
                now,
                request_iso,
            )
        match = fetched.match
        normalize_started = time.perf_counter()
        try:
            data = normalize_live_match(
                match,
                getattr(fetched, "payload", None),
                fetched_at=request_iso,
            )
        except (TypeError, ValueError, KeyError, AttributeError) as exc:
            return self._record_error(entry, str(exc), now, request_iso)
        entry.data = data
        entry.fetched_at = data.fetched_at
        entry.last_request_at = request_iso
        entry.last_request_monotonic = now
        entry.response_time_ms = int(response_ms) if response_ms is not None else None
        entry.payload_size = payload_size
        entry.parse_duration_ms = int((time.perf_counter() - normalize_started) * 1000)
        entry.successful_payloads += 1
        entry.consecutive_errors = 0
        entry.error = None
        # The UI timer controls the normal ten-second cadence.  The live
        # service only needs the short cache TTL here; using the refresh
        # interval as another gate would make an eight-second TTL ineffective
        # for reruns that happen just after expiry.
        entry.next_allowed_monotonic = now
        detailed = _has_detailed_stats(data.stats)
        tipico_finished = _is_finished_value(getattr(event, "status", None)) or _is_finished_value(
            getattr(event, "period", None)
        )
        if detailed:
            entry.consecutive_no_data_count = 0
            entry.availability_status = "DETAILED_DATA_AVAILABLE"
            entry.status = "FINISHED" if match.is_finished or tipico_finished else "AVAILABLE"
        else:
            entry.consecutive_no_data_count += 1
            entry.availability_status = "NO_DETAILED_DATA"
            if match.is_finished or tipico_finished:
                entry.status = "FINISHED"
            elif (
                entry.consecutive_no_data_count >= self.no_data_payload_threshold
                and (match.minute is None or match.minute >= self.pending_minute)
            ):
                entry.status = "NO_DATA"
            else:
                entry.status = "PENDING"
            if entry.status == "PENDING":
                entry.availability_status = "DETAILED_DATA_PENDING"
        return self._result(entry, status=entry.status, provider_id=provider_id, cache_hit=False, request_made=True)

    def fetch_for_event(
        self,
        event: Any | None,
        *,
        force: bool = False,
        allow_network: bool = True,
    ) -> FotMobLiveResult:
        """Fetch only the confirmed provider match for ``event``.

        ``force`` bypasses the short TTL and an error backoff, but never
        bypasses terminal ``NO_DATA`` or ``FINISHED`` states.  This prevents a
        manual button from reintroducing a request loop after the provider has
        explicitly shown that detailed data is unavailable.
        """

        if event is None:
            with self._lock:
                self._metrics["no_selected_match"] += 1
            return self._result(None, status="NO_MATCH", provider_id=None, cache_hit=False, request_made=False)
        if not self.enabled or not self.manual_use_allowed:
            return self._result(None, status="DISABLED", provider_id=None, cache_hit=False, request_made=False)
        linked = self._link_for_event(event)
        if linked is None:
            return self._result(None, status="NO_MATCH", provider_id=None, cache_hit=False, request_made=False)
        provider_id, _ = linked
        with self._lock:
            entry = self._cache.setdefault(provider_id, _CacheEntry(provider_match_id=provider_id))
            now = self._clock()
            if entry.status in TERMINAL_LIVE_STATUSES:
                self._metrics["terminal_skips"] += 1
                return self._result(entry, status=entry.status, provider_id=provider_id, cache_hit=True, request_made=False)
            if not allow_network and not force:
                return self._result(entry, status=entry.status, provider_id=provider_id, cache_hit=True, request_made=False)
            if not force and entry.last_request_monotonic is not None:
                if now - entry.last_request_monotonic < self.cache_ttl_seconds:
                    self._metrics["cache_hits"] += 1
                    return self._result(entry, status=entry.status, provider_id=provider_id, cache_hit=True, request_made=False)
                if now < entry.next_allowed_monotonic:
                    self._metrics["cache_hits"] += 1
                    return self._result(entry, status=entry.status, provider_id=provider_id, cache_hit=True, request_made=False)
            self._metrics["cache_misses"] += 1
            self._metrics["requests"] += 1
            self._metrics["last_provider_match_id"] = provider_id
            request_iso = _now_iso()
            self._metrics["last_request_at"] = request_iso
            try:
                if self.client is None:
                    raise RuntimeError("Kein FotMob-Live-Client konfiguriert")
                fetched = self.client.fetch_match_details(provider_id)
            except Exception as exc:  # provider boundary: keep the dashboard alive
                return self._record_error(entry, str(exc), now, request_iso)
            return self._ingest_fetched_response(
                event,
                provider_id,
                entry,
                fetched,
                request_iso=request_iso,
                now=now,
            )

    def metrics(self) -> dict[str, Any]:
        with self._lock:
            return {
                **self._metrics,
                "cache_entries": len(self._cache),
                "cache_ttl_seconds": self.cache_ttl_seconds,
                "refresh_seconds": self.refresh_seconds,
                "pending_minute": self.pending_minute,
                "no_data_payload_threshold": self.no_data_payload_threshold,
            }

    def get_for_event(
        self,
        event: Any | None,
        *,
        force: bool = False,
        allow_network: bool = True,
    ) -> FotMobLiveResult:
        """Readable alias for callers that treat the panel as a data source."""

        return self.fetch_for_event(event, force=force, allow_network=allow_network)

    def refresh_for_event(
        self,
        event: Any | None,
        *,
        force: bool = False,
        allow_network: bool = True,
    ) -> FotMobLiveResult:
        """Compatibility alias; this method still never persists anything."""

        return self.fetch_for_event(event, force=force, allow_network=allow_network)

    def debug_for_event(self, event: Any) -> dict[str, Any]:
        linked = self._link_for_event(event)
        link_row = linked[1] if linked else None
        if link_row is None:
            store = getattr(self.fotmob_service, "store", None)
            lookup = getattr(store, "provider_event_link_for_tipico_event", None)
            if callable(lookup):
                try:
                    link_row = lookup(_event_id(event) or "")
                except (AttributeError, KeyError, TypeError, ValueError):
                    link_row = None
        result = self.cached_for_event(event)
        if result is None:
            link_status = _row_value(link_row, "match_status")
            return {
                "tipico_event_id": _event_id(event),
                "fotmob_match_id": linked[0] if linked else (
                    _row_value(link_row, "fotmob_match_id")
                    or _row_value(link_row, "provider_match_id")
                ),
                "status": link_status or ("LINKED_NOT_FETCHED" if linked else "NO_MATCH"),
                "link_status": link_status,
                "match_method": _row_value(link_row, "match_method"),
                "match_confidence": _row_value(link_row, "match_confidence"),
                "tipico_home_team": _row_value(link_row, "tipico_home_team"),
                "tipico_away_team": _row_value(link_row, "tipico_away_team"),
                "fotmob_home_team": _row_value(link_row, "fotmob_home_team"),
                "fotmob_away_team": _row_value(link_row, "fotmob_away_team"),
                "tipico_kickoff": _row_value(link_row, "tipico_kickoff"),
                "fotmob_kickoff": _row_value(link_row, "fotmob_kickoff"),
                "fotmob_league_id": _row_value(link_row, "fotmob_league_id"),
            }
        return {
            "tipico_event_id": _event_id(event),
            "fotmob_match_id": result.provider_match_id,
            "status": result.status,
            "link_status": _row_value(link_row, "match_status"),
            "match_method": _row_value(link_row, "match_method"),
            "match_confidence": _row_value(link_row, "match_confidence"),
            "tipico_home_team": _row_value(link_row, "tipico_home_team"),
            "tipico_away_team": _row_value(link_row, "tipico_away_team"),
            "fotmob_home_team": _row_value(link_row, "fotmob_home_team"),
            "fotmob_away_team": _row_value(link_row, "fotmob_away_team"),
            "tipico_kickoff": _row_value(link_row, "tipico_kickoff"),
            "fotmob_kickoff": _row_value(link_row, "fotmob_kickoff"),
            "fotmob_league_id": _row_value(link_row, "fotmob_league_id"),
            "availability_status": result.availability_status,
            "consecutive_no_data_count": result.consecutive_no_data_count,
            "successful_payloads": result.successful_payloads,
            "last_request_at": result.last_request_at,
            "fetched_at": result.fetched_at,
            "cache_hit": result.cache_hit,
            "response_time_ms": result.response_time_ms,
            "payload_size": result.payload_size,
            "parse_duration_ms": result.parse_duration_ms,
            "error": result.error,
        }


__all__ = [
    "ACCEPTED_LINK_STATUSES",
    "DETAILED_CORE_KEYS",
    "FotMobLiveData",
    "FotMobManualBindingResult",
    "FotMobLiveResult",
    "FotMobLiveService",
    "LIVE_STAT_KEYS",
    "STAT_LABELS",
    "normalize_live_match",
]
