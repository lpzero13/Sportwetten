"""Reconcile missing Tipico final results with verified FotMob evidence.

The worker is intentionally narrower than the regular FotMob enrichment path:
it does not collect statistics, does not alter Tipico odds/snapshots and does
not overwrite a complete result.  It uses the already persisted all-league
FotMob daily index to find candidates, fetches one detail response per
candidate and promotes only a structurally valid regular-time result.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Mapping, Sequence
from urllib.parse import quote

from config import Settings
from fotmob.client import FotMobClient, FotMobFetchResult
from fotmob.history_discovery import extract_daily_match_index
from fotmob.history_models import FotMobMatchIndexRecord
from fotmob.history_pipeline import manual_history_allowed, worker_history_allowed
from fotmob.matching import (
    AUTO_LINK_STATUSES,
    MatchIdentity,
    MatchMatchResult,
    MatchMatcher,
    _team_core,
    normalize_team_name,
    normalize_country,
    team_names_equivalent,
)
from fotmob.models import FotMobMatch


NETWORK_MODES = frozenset({"worker", "manual", "cached"})
TERMINAL_QUEUE_STATUSES = frozenset(
    {"APPLIED", "CONFIRMED", "CONFLICT", "EXCLUDED_NON_REGULATION", "CANCELLED"}
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_time(value: Any) -> datetime | None:
    if value is None or not str(value).strip():
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _payload_hash(value: Any) -> str | None:
    if not isinstance(value, Mapping):
        return None
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def _text(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Mapping):
        for key in ("name", "short", "shortKey", "long", "longKey", "type", "value"):
            result = _text(value.get(key))
            if result:
                return result
        return None
    result = str(value).strip()
    return result or None


def _first(value: Any, *keys: str) -> Any:
    if not isinstance(value, Mapping):
        return None
    lowered = {str(key).casefold(): key for key in value}
    for key in keys:
        actual = lowered.get(key.casefold())
        if actual is not None:
            return value[actual]
    return None


def _walk(value: Any):
    if isinstance(value, Mapping):
        for key, child in value.items():
            yield str(key), child
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield None, child
            yield from _walk(child)


def _bool_value(value: Any) -> bool | None:
    if value is True:
        return True
    if value is False:
        return False
    if isinstance(value, (int, float)) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"true", "yes", "ja", "1"}:
            return True
        if normalized in {"false", "no", "nein", "0"}:
            return False
    return None


def _provider_status_node(match: FotMobMatch) -> Mapping[str, Any]:
    raw = match.raw_data if isinstance(match.raw_data, Mapping) else {}
    header = _first(raw, "header")
    if isinstance(header, Mapping):
        status = _first(header, "status")
        if isinstance(status, Mapping):
            return status
    page_props = _first(_first(raw, "props"), "pageProps")
    if isinstance(page_props, Mapping):
        header = _first(page_props, "header")
        if isinstance(header, Mapping):
            status = _first(header, "status")
            if isinstance(status, Mapping):
                return status
    return {}


def _scope_status(match: FotMobMatch) -> tuple[str, list[str]]:
    """Return regulation/extra-time/penalties/unknown without guessing.

    The current FotMob payload exposes an explicit ``halfs`` object.  Empty
    extra-half timestamps plus an explicit FT status are sufficient evidence
    for a regular-time result.  Older payloads without that information stay
    ``UNKNOWN`` and are not promoted by default.
    """

    status = _provider_status_node(match)
    flags: list[str] = []
    status_text = " ".join(
        str(item or "")
        for item in (
            _first(status, "reason"),
            _first(status, "type"),
            _first(status, "period"),
        )
    ).casefold()
    if any(token in status_text for token in ("penalty", "shootout", "fulltime_ap")):
        flags.append("penalty_indicator")
    if any(token in status_text for token in ("extra", "overtime", "aet")):
        flags.append("extra_time_indicator")

    for key, child in _walk(status):
        if key is None:
            continue
        normalized = key.casefold().replace("_", "")
        if normalized in {"extratime", "isextratime", "wentoextratime", "overtime"}:
            if _bool_value(child) is True:
                flags.append("extra_time_flag")
        if normalized in {"penaltyshootout", "wenttopenalties", "penalties"}:
            if _bool_value(child) is True:
                flags.append("penalties_flag")

    halves = _first(status, "halfs", "halves")
    if isinstance(halves, Mapping):
        first_extra = _first(halves, "firstExtraHalfStarted", "first_extra_half_started")
        second_extra = _first(halves, "secondExtraHalfStarted", "second_extra_half_started")
        if str(first_extra or "").strip() or str(second_extra or "").strip():
            flags.append("extra_half_started")

    penalty_loser = _first(status, "whoLostOnPenalties", "lostOnPenalties")
    if penalty_loser not in (None, "", False):
        flags.append("penalty_loser_present")

    if any("penalt" in flag for flag in flags):
        return "PENALTIES", sorted(set(flags))
    if any("extra" in flag or "overtime" in flag or flag == "aet" for flag in flags):
        return "EXTRA_TIME", sorted(set(flags))

    finished_explicit = _bool_value(_first(status, "finished", "completed")) is True
    reason = _first(status, "reason")
    reason_text = " ".join(
        str(_first(reason, key) or "") for key in ("short", "shortKey", "long", "longKey")
    ).casefold()
    explicit_ft = finished_explicit or "fulltime" in reason_text or reason_text.strip() in {"ft", "finished"}
    explicit_no_extra = isinstance(halves, Mapping) and (
        "firstExtraHalfStarted" in halves or "first_extra_half_started" in halves
    ) and (
        "secondExtraHalfStarted" in halves or "second_extra_half_started" in halves
    )
    penalty_key_explicitly_empty = (
        _first(status, "whoLostOnPenalties", "lostOnPenalties") in (None, "", False)
        and any(
            key.casefold() in {"wholostonpenalties", "lostonpenalties"}
            for key in status
        )
    )
    if explicit_ft and (explicit_no_extra or penalty_key_explicitly_empty):
        return "REGULATION", sorted(set(flags))
    return "UNKNOWN", sorted(set(flags or ["scope_not_explicit"]))


def _is_finished(match: FotMobMatch) -> bool:
    if match.is_finished:
        return True
    status = _provider_status_node(match)
    if _bool_value(_first(status, "finished", "completed")) is True:
        return True
    reason = _first(status, "reason")
    reason_text = " ".join(
        str(_first(reason, key) or "") for key in ("short", "shortKey", "long", "longKey")
    ).casefold()
    return reason_text.strip() in {"ft", "finished", "fulltime", "full time"}


def _match_from_index(row: Mapping[str, Any]) -> FotMobMatch:
    raw = json.loads(row["raw_fixture_json"] or "{}") if "raw_fixture_json" in row.keys() else {}
    return FotMobMatch(
        provider_match_id=str(row["fotmob_match_id"]),
        kickoff_at=row["kickoff_at_utc"],
        competition_id=str(row["league_id"]) if row["league_id"] is not None else None,
        competition_name=row["league_name"],
        competition_country=row["country_name"] or row["country_code"],
        home_team=str(row["home_team_name"] or ""),
        away_team=str(row["away_team_name"] or ""),
        home_team_id=row["home_team_id"],
        away_team_id=row["away_team_id"],
        status=row["match_status"],
        extra_data={"identity_aliases": _fixture_aliases(raw)},
    )


def _match_from_record(record: FotMobMatchIndexRecord) -> FotMobMatch:
    return FotMobMatch(
        provider_match_id=record.provider_match_id,
        kickoff_at=record.kickoff_at,
        competition_id=record.league_id,
        competition_name=record.league_name,
        competition_country=record.country_name or record.country,
        home_team=record.home_team_name,
        away_team=record.away_team_name,
        home_team_id=record.home_team_id,
        away_team_id=record.away_team_id,
        status=record.match_status,
        extra_data={"identity_aliases": _fixture_aliases(record.raw_fixture or {})},
    )


def _fixture_aliases(raw: Mapping[str, Any]) -> dict[str, list[str]]:
    aliases: dict[str, list[str]] = {}
    for side in ("home", "away"):
        team = raw.get(side) or raw.get(side + "Team") or {}
        aliases[side] = list(dict.fromkeys(
            team[key].strip() for key in ("longName", "name", "displayName", "teamName")
            if isinstance(team, Mapping) and isinstance(team.get(key), str) and team[key].strip()
        ))
    return aliases


@dataclass(frozen=True, slots=True)
class BackfillEvent:
    event_id: str
    competition_id: str | None
    competition_name: str
    competition_country: str | None
    sport: str
    home_team: str
    away_team: str
    home_team_id: str | None
    away_team_id: str | None
    kickoff_time: str | None
    status: str | None
    period: str | None
    score_home: int | None
    score_away: int | None
    ht_score_home: int | None
    ht_score_away: int | None
    backfill_attempt_count: int = 0


@dataclass(frozen=True, slots=True)
class BackfillPlan:
    event: BackfillEvent
    provider_match_id: str | None
    identity_status: str
    confidence: float
    reasons: tuple[str, ...]
    source_context: str
    candidate_count: int


@dataclass(frozen=True, slots=True)
class Validation:
    status: str
    scope_status: str
    flags: tuple[str, ...]
    second_half_goals: int | None = None
    second_half_goal_class: str | None = None


@dataclass(slots=True)
class _DailyCatalog:
    by_pair: dict[tuple[str, str], list[FotMobMatch]]
    by_core: dict[tuple[str, str], list[FotMobMatch]]
    by_home: dict[str, set[str]]
    by_away: dict[str, set[str]]
    by_date: dict[str, list[FotMobMatch]]
    by_id: dict[str, FotMobMatch]


def _event_from_row(row: Mapping[str, Any]) -> BackfillEvent:
    def optional_int(name: str) -> int | None:
        value = row[name]
        return None if value is None else int(value)

    return BackfillEvent(
        event_id=str(row["event_id"]),
        competition_id=(str(row["competition_id"]) if row["competition_id"] is not None else None),
        competition_name=str(row["competition_name"] or ""),
        competition_country=row["competition_country"],
        sport=str(row["sport"] or ""),
        home_team=str(row["home_team"] or ""),
        away_team=str(row["away_team"] or ""),
        home_team_id=(str(row["home_team_id"]) if row["home_team_id"] is not None else None),
        away_team_id=(str(row["away_team_id"]) if row["away_team_id"] is not None else None),
        kickoff_time=row["kickoff_time"],
        status=row["status"],
        period=row["period"],
        score_home=optional_int("score_home"),
        score_away=optional_int("score_away"),
        ht_score_home=optional_int("ht_score_home"),
        ht_score_away=optional_int("ht_score_away"),
        backfill_attempt_count=int(row["backfill_attempt_count"] or 0),
    )


class ResultBackfillRunner:
    """Run a dry-run or an additive FotMob result reconciliation batch."""

    def __init__(
        self,
        settings: Settings,
        database: Any,
        *,
        client: FotMobClient | Any | None = None,
        logger: logging.Logger | None = None,
        read_only: bool = False,
    ) -> None:
        self.settings = settings
        self.database = database
        self.connection: sqlite3.Connection = (
            database.connection
            if hasattr(database, "connection")
            else database
        )
        self._lock = getattr(database, "_lock", RLock())
        self.read_only = bool(read_only)
        self.logger = logger or logging.getLogger("tipico.result_backfill")
        self.matcher = MatchMatcher(
            tolerance_minutes=int(getattr(settings, "fotmob_matching_tolerance_minutes", 15))
        )
        self._provider_days: list[dict[str, Any]] = []
        self.client = client or FotMobClient(
            base_url=settings.fotmob_base_url,
            api_base_url=settings.fotmob_api_base_url,
            match_details_path=settings.fotmob_match_details_path,
            timeout_seconds=settings.fotmob_timeout_seconds,
            max_retries=settings.fotmob_max_retries,
            min_request_interval_seconds=(
                settings.fotmob_min_request_interval_seconds
                if str(getattr(settings, "fotmob_rate_mode", "ADAPTIVE")).upper() == "FIXED"
                else None
            ),
            rate_mode=getattr(settings, "fotmob_rate_mode", "ADAPTIVE"),
            initial_rps=getattr(settings, "fotmob_initial_rps", 5.0),
            rps_step=getattr(settings, "fotmob_rps_step", 5.0),
            min_rps=getattr(settings, "fotmob_min_rps", 0.5),
            max_rps=getattr(settings, "fotmob_max_rps", 30.0),
            rate_window_requests=getattr(settings, "fotmob_rate_window_requests", 20),
            rate_cooldown_seconds=getattr(settings, "fotmob_rate_cooldown_seconds", 5.0),
            max_error_rate=getattr(settings, "fotmob_max_error_rate", 0.10),
            max_5xx_rate=getattr(settings, "fotmob_max_5xx_rate", 0.05),
            max_timeout_rate=getattr(settings, "fotmob_max_timeout_rate", 0.05),
            max_connection_error_rate=getattr(settings, "fotmob_max_connection_error_rate", 0.05),
            max_p95_latency_ms=getattr(settings, "fotmob_max_p95_latency_ms", 3000.0),
            connection_pool_size=getattr(settings, "fotmob_connection_pool_size", 40),
            logger=self.logger,
        )

    def _table_exists(self, table: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone()
        return row is not None

    def _candidate_rows(
        self,
        *,
        current: datetime,
        limit: int,
        event_id: str | None = None,
        event_ids: Sequence[str] | None = None,
    ) -> list[sqlite3.Row]:
        before = _iso(
            current
            - timedelta(hours=float(getattr(self.settings, "result_backfill_grace_hours", 3.0)))
        )
        if self._table_exists("result_backfill_queue"):
            queue_select = """
                COALESCE(q.attempt_count, 0) AS backfill_attempt_count,
                q.status AS backfill_status, q.next_attempt_at,
                q.last_provider_match_id, q.identity_status AS backfill_identity_status,
                q.validation_status AS backfill_validation_status,
                q.last_error AS backfill_last_error
            """
            queue_join = "LEFT JOIN result_backfill_queue q ON q.event_id = e.event_id"
            queue_where = """
                AND (
                    q.event_id IS NULL
                    OR (
                        q.status NOT IN ('APPLIED', 'CONFIRMED')
                        AND q.next_attempt_at IS NOT NULL
                        AND q.next_attempt_at <= ?
                    )
                )
            """
            params: tuple[Any, ...] = (before, _iso(current))
        else:
            queue_select = "0 AS backfill_attempt_count, NULL AS backfill_status, NULL AS next_attempt_at, NULL AS last_provider_match_id, NULL AS backfill_identity_status, NULL AS backfill_validation_status, NULL AS backfill_last_error"
            queue_join = ""
            queue_where = ""
            params = (before,)
        result_columns = {
            str(row["name"])
            for row in self.connection.execute("PRAGMA table_info(match_results)").fetchall()
        }
        if "result_use_h2" in result_columns:
            result_need = """
                r.event_id IS NULL
                OR r.ft_home IS NULL OR r.ft_away IS NULL
                OR r.ht_home IS NULL OR r.ht_away IS NULL
                OR COALESCE(r.result_status, 'PENDING') != 'VERIFIED'
                OR COALESCE(r.result_use_ft, 0) = 0
                OR COALESCE(r.result_use_h2, 0) = 0
            """
            # A local FT-only repair is deliberately kept queue-eligible for
            # a later FotMob HT enrichment even though its queue row is
            # already APPLIED.
            queue_where = """
                AND (
                    q.event_id IS NULL
                    OR (
                        q.status NOT IN ('APPLIED', 'CONFIRMED')
                        AND q.next_attempt_at IS NOT NULL
                        AND q.next_attempt_at <= ?
                    )
                    OR (
                        COALESCE(r.result_use_h2, 0) = 0
                        AND q.status IN ('APPLIED', 'CONFIRMED')
                    )
                )
            """ if self._table_exists("result_backfill_queue") else queue_where
        else:
            result_need = "r.event_id IS NULL OR r.ft_home IS NULL OR r.ft_away IS NULL"
        if event_id is not None:
            event_filter = "AND e.event_id = ?"
            event_params: list[Any] = [str(event_id)]
        elif event_ids is not None:
            ids = tuple(dict.fromkeys(str(value) for value in event_ids if str(value)))
            if not ids:
                return []
            event_filter = f"AND e.event_id IN ({', '.join('?' for _ in ids)})"
            event_params = list(ids)
        else:
            event_filter = ""
            event_params = []
        # An explicit inventory/recheck is authoritative.  It must be able
        # to revalidate a prior conflict or an old terminal queue row; the
        # normal daily path keeps those retry guards.
        if event_id is not None or event_ids is not None:
            queue_where = ""
        query = f"""
            SELECT e.*, {queue_select}
            FROM events e
            LEFT JOIN match_results r ON r.event_id = e.event_id
            {queue_join}
            WHERE lower(e.sport) = 'soccer'
              AND e.kickoff_time IS NOT NULL
              AND e.kickoff_time <= ?
              AND ({result_need})
              {queue_where}
              {event_filter}
            ORDER BY e.kickoff_time ASC, e.event_id ASC
            LIMIT ?
        """
        base_params = [before] if not queue_where else list(params)
        base_params.extend(event_params)
        base_params.append(max(1, int(limit)))
        with self._lock:
            return list(self.connection.execute(query, tuple(base_params)).fetchall())

    def _existing_link(self, event_id: str) -> tuple[str, float, str] | None:
        accepted = tuple(sorted(AUTO_LINK_STATUSES))
        placeholders = ", ".join("?" for _ in accepted)
        with self._lock:
            if self._table_exists("provider_event_links"):
                row = self.connection.execute(
                    f"""
                    SELECT fotmob_match_id, match_confidence, match_status
                    FROM provider_event_links
                    WHERE provider = 'FOTMOB' AND tipico_event_id = ?
                      AND match_status IN ({placeholders})
                      AND fotmob_match_id IS NOT NULL
                    """,
                    (event_id, *accepted),
                ).fetchone()
                if row is not None:
                    return str(row["fotmob_match_id"]), float(row["match_confidence"] or 0.0), str(row["match_status"])
            if self._table_exists("match_provider_links"):
                row = self.connection.execute(
                    f"""
                    SELECT l.provider_match_id, l.match_confidence, l.match_status
                    FROM match_provider_links l
                    JOIN matches m ON m.internal_match_id = l.internal_match_id
                    WHERE l.provider = 'FOTMOB' AND m.tipico_event_id = ?
                      AND l.match_status IN ({placeholders})
                      AND l.provider_match_id IS NOT NULL
                    """,
                    (event_id, *accepted),
                ).fetchone()
                if row is not None:
                    return str(row["provider_match_id"]), float(row["match_confidence"] or 0.0), str(row["match_status"])
        return None

    def _daily_rows(self, events: list[BackfillEvent]) -> list[sqlite3.Row]:
        if not self._table_exists("fotmob_daily_index") or not events:
            return []
        times = [_parse_time(event.kickoff_time) for event in events]
        times = [value for value in times if value is not None]
        if not times:
            return []
        start = min(value.date() for value in times) - timedelta(days=1)
        end = max(value.date() for value in times) + timedelta(days=1)
        with self._lock:
            return list(
                self.connection.execute(
                    """
                    SELECT * FROM fotmob_daily_index
                    WHERE provider = 'FOTMOB'
                      AND observation_date BETWEEN ? AND ?
                    ORDER BY kickoff_at_utc, fotmob_match_id
                    """,
                    (start.isoformat(), end.isoformat()),
                ).fetchall()
            )

    def _daily_endpoint(self, day: date) -> str:
        template = getattr(
            self.settings,
            "fotmob_daily_matches_path",
            "/data/matches?date={date}&timezone={timezone}&ccode3={ccode3}&includeNextDayLateNight=true",
        )
        return str(template).format(
            date=day.strftime("%Y%m%d"),
            timezone=quote(str(getattr(self.settings, "fotmob_daily_timezone", "Europe/Berlin")), safe=""),
            ccode3=quote(str(getattr(self.settings, "fotmob_daily_ccode3", "DEU")), safe=""),
        )

    def _refresh_missing_days(
        self,
        events: list[BackfillEvent],
        existing_rows: list[sqlite3.Row],
        *,
        allow_network: bool,
        persist: bool,
        force: bool = False,
        run_id: str | None = None,
    ) -> list[FotMobMatch]:
        times = [_parse_time(event.kickoff_time) for event in events]
        times = [value for value in times if value is not None]
        if not times or not allow_network:
            return []
        start = min(value.date() for value in times) - timedelta(days=1)
        end = max(value.date() for value in times) + timedelta(days=1)
        existing_counts: dict[str, int] = defaultdict(int)
        for row in existing_rows:
            existing_counts[str(row["observation_date"])] += 1
        days = []
        cursor = start
        while cursor <= end:
            if force or cursor.isoformat() not in existing_counts:
                days.append(cursor)
            cursor += timedelta(days=1)
        if not days:
            return []

        persisted_store = None
        if persist and not self.read_only:
            from fotmob.history_storage import FotMobHistoryStore

            persisted_store = FotMobHistoryStore(
                self.database,
                self.settings.archive_path,
            )
        records: list[FotMobMatch] = []
        for day in days:
            endpoint = self._daily_endpoint(day)
            fetched = self.client.fetch_json(endpoint)
            fetched_at = _iso(_now())
            status = "COMPLETE"
            parser_status = "OK"
            error = fetched.error
            extracted: list[FotMobMatchIndexRecord] = []
            if not fetched.success:
                status = "HTTP_ERROR" if fetched.status_code is not None else "FETCH_ERROR"
                parser_status = "NOT_RUN"
                self.logger.warning("FotMob daily index failed for %s: %s", day, fetched.error)
            elif self._is_no_data(fetched):
                status = "EMPTY"
                parser_status = "NO_DATA"
                error = None
            elif not isinstance(fetched.payload, Mapping) or not isinstance(fetched.payload.get("leagues"), list):
                status = "PARSER_ERROR"
                parser_status = "INVALID_PAYLOAD"
                error = "daily response lacks a valid leagues list"
            else:
                try:
                    extracted = extract_daily_match_index(
                        fetched.payload,
                        observation_date=day,
                        first_seen_at=fetched_at,
                    )
                    status = "COMPLETE" if extracted else "EMPTY"
                except Exception as exc:  # pragma: no cover - defensive parser boundary
                    status = "PARSER_ERROR"
                    parser_status = "ERROR"
                    error = f"{type(exc).__name__}: {exc}"
                    self.logger.warning("FotMob daily index parser failed for %s: %s", day, exc)
            records.extend(_match_from_record(record) for record in extracted)
            if persisted_store is not None:
                persisted_store.upsert_daily_index(
                    extracted,
                    observation_date=day.isoformat(),
                    source_endpoint=endpoint,
                    payload_hash=_payload_hash(fetched.payload),
                    fetched_at=fetched_at,
                    replace_day=bool(force and status in {"COMPLETE", "EMPTY"}),
                )
            day_result = {
                "observation_date": day.isoformat(),
                "status": status,
                "source_endpoint": endpoint,
                "fetched_at": fetched_at,
                "response_status": fetched.status_code,
                "fixture_count": len(extracted),
                "payload_hash": _payload_hash(fetched.payload),
                "parser_status": parser_status,
                "error": error,
                "existing_cache_rows": existing_counts.get(day.isoformat(), 0),
                "refreshed": bool(force),
            }
            self._provider_days.append(day_result)
            if run_id and persist and hasattr(self.database, "record_result_finalization_provider_day"):
                self.database.record_result_finalization_provider_day(run_id, **day_result)
                # Keep a persistent lease heartbeat while a full inventory is
                # downloading daily provider catalogs.  This makes a long
                # network phase distinguishable from a crashed worker and
                # lets resume/status report the last durable progress point.
                if hasattr(self.database, "update_result_finalization_run"):
                    self.database.update_result_finalization_run(
                        run_id,
                        heartbeat_at=fetched_at,
                    )
        return records

    def _catalog(
        self,
        events: list[BackfillEvent],
        *,
        allow_network: bool,
        persist: bool,
        force_refresh: bool = False,
        run_id: str | None = None,
    ) -> _DailyCatalog:
        rows = self._daily_rows(events)
        fresh = self._refresh_missing_days(
                events,
                rows,
                allow_network=allow_network,
                persist=persist,
                force=force_refresh,
                run_id=run_id,
            )
        refreshed_days = {
            day["observation_date"] for day in self._provider_days
            if day["status"] in {"COMPLETE", "EMPTY"}
        }
        # The in-memory catalog must reflect replacements too, including
        # successful empty responses. Otherwise deleted/stale names survive
        # for the entire run even though SQLite already contains fresh data.
        matches = [_match_from_index(row) for row in rows if row["observation_date"] not in refreshed_days]
        matches.extend(fresh)
        by_id: dict[str, FotMobMatch] = {}
        for match in matches:
            by_id[match.provider_match_id] = match
        by_pair: dict[tuple[str, str], list[FotMobMatch]] = defaultdict(list)
        by_core: dict[tuple[str, str], list[FotMobMatch]] = defaultdict(list)
        by_home: dict[str, set[str]] = defaultdict(set)
        by_away: dict[str, set[str]] = defaultdict(set)
        by_date: dict[str, list[FotMobMatch]] = defaultdict(list)
        for match in by_id.values():
            home = normalize_team_name(match.home_team)
            away = normalize_team_name(match.away_team)
            core_home = _team_core(match.home_team)
            core_away = _team_core(match.away_team)
            by_pair[(home, away)].append(match)
            by_core[(core_home, core_away)].append(match)
            for key in {home, core_home}:
                if key:
                    by_home[key].add(match.provider_match_id)
            for key in {away, core_away}:
                if key:
                    by_away[key].add(match.provider_match_id)
            if match.kickoff_at:
                by_date[match.kickoff_at[:10]].append(match)
        return _DailyCatalog(by_pair, by_core, by_home, by_away, by_date, by_id)

    def _candidate_matches(self, event: BackfillEvent, catalog: _DailyCatalog) -> list[FotMobMatch]:
        home = normalize_team_name(event.home_team)
        away = normalize_team_name(event.away_team)
        core_home = _team_core(event.home_team)
        core_away = _team_core(event.away_team)
        ids: set[str] = set()
        for key in ((home, away), (core_home, core_away)):
            ids.update(item.provider_match_id for item in catalog.by_pair.get(key, ()))
            ids.update(item.provider_match_id for item in catalog.by_core.get(key, ()))
        if not ids:
            home_ids = catalog.by_home.get(home, set()) | catalog.by_home.get(core_home, set())
            away_ids = catalog.by_away.get(away, set()) | catalog.by_away.get(core_away, set())
            ids.update(home_ids & away_ids)
        kickoff = _parse_time(event.kickoff_time)
        if kickoff is None:
            return [catalog.by_id[item] for item in sorted(ids) if item in catalog.by_id]
        country = normalize_country(event.competition_country)
        for offset in (-1, 0, 1):
            day = (kickoff + timedelta(days=offset)).date().isoformat()
            for match in catalog.by_date.get(day, ()):
                provider_time = _parse_time(match.kickoff_at)
                if provider_time is None or abs((provider_time - kickoff).total_seconds()) > self.matcher.tolerance_minutes * 60:
                    continue
                other_country = normalize_country(match.competition_country)
                if country and other_country and country != other_country:
                    continue
                # Candidate discovery is deliberately broader than acceptance.
                # Let the shared matcher enforce names, category, league and
                # uniqueness; an exact-name prefilter defeats its fuzzy rules.
                ids.add(match.provider_match_id)
        return [catalog.by_id[item] for item in sorted(ids) if item in catalog.by_id]

    def _plans(self, events: list[BackfillEvent], catalog: _DailyCatalog) -> list[BackfillPlan]:
        plans: list[BackfillPlan] = []
        for event in events:
            link = self._existing_link(event.event_id)
            if link is not None:
                plans.append(
                    BackfillPlan(
                        event=event,
                        provider_match_id=link[0],
                        identity_status="LINKED",
                        confidence=link[1],
                        reasons=("confirmed_provider_link",),
                        source_context="CONFIRMED_PROVIDER_LINK",
                        candidate_count=1,
                    )
                )
                continue
            candidates = self._candidate_matches(event, catalog)
            result = self.matcher.match(MatchIdentity.from_tipico_event(event), candidates)
            plans.append(
                BackfillPlan(
                    event=event,
                    provider_match_id=result.provider_match_id if result.auto_linkable else None,
                    identity_status=result.status,
                    confidence=result.confidence,
                    reasons=tuple(result.reasons),
                    source_context="DAILY_INDEX_MATCH",
                    candidate_count=len(candidates),
                )
            )
        return plans

    def _network_allowed(self, mode: str) -> bool:
        normalized = str(mode or "").strip().casefold()
        if normalized == "cached":
            return False
        if normalized == "manual":
            return manual_history_allowed(self.settings)
        return worker_history_allowed(self.settings)

    @staticmethod
    def _is_no_data(fetched: FotMobFetchResult) -> bool:
        payload = fetched.payload
        if not isinstance(payload, Mapping) or not payload.get("error"):
            return False
        message = str(payload.get("message") or "").strip().casefold()
        return message in {"data not found", "match data not found"}

    def _validate(self, event: BackfillEvent, match: FotMobMatch, catalog_match: FotMobMatch | None = None) -> Validation:
        # The same provider may use different short/full names in its daily
        # list and details. Transfer source aliases only when match, league
        # and BOTH team IDs agree; scores/status still come from the details.
        identity_match = match
        if (catalog_match is not None
                and match.provider_match_id == catalog_match.provider_match_id
                and match.competition_id and match.competition_id == catalog_match.competition_id
                and match.home_team_id and match.home_team_id == catalog_match.home_team_id
                and match.away_team_id and match.away_team_id == catalog_match.away_team_id):
            aliases = catalog_match.extra_data.get("identity_aliases", {})
            identity_match = replace(match, extra_data={**match.extra_data, "identity_aliases": aliases})
            # INT is a generic provider placeholder, never a contradictory
            # domestic country. Require agreement of the two league labels
            # before substituting the explicit country of the same league ID.
            if (str(match.competition_country or "").upper() == "INT"
                    and catalog_match.competition_country
                    and self.matcher._competition_equal(match.competition_name, catalog_match.competition_name,
                                                       normalize_country(catalog_match.competition_country))):
                identity_match = replace(identity_match, competition_country=catalog_match.competition_country)
        identity = self.matcher.match(MatchIdentity.from_tipico_event(event), [identity_match])
        if not identity.auto_linkable:
            return Validation(
                status="IDENTITY_CONFLICT",
                scope_status="UNKNOWN",
                flags=tuple(identity.candidates[0].reasons if identity.candidates else identity.reasons or ["detail_identity_not_confirmed"]),
            )
        if not _is_finished(match):
            return Validation("NOT_FINISHED", "UNKNOWN", ("provider_not_finished",))
        values = (
            match.ht_score_home,
            match.ht_score_away,
            match.score_home,
            match.score_away,
        )
        ft_values = (match.score_home, match.score_away)
        if any(value is None for value in ft_values):
            return Validation("INCOMPLETE_SCORE", "UNKNOWN", ("ft_score_missing",))
        if any(int(value) < 0 for value in values if value is not None):
            return Validation("INVALID_SCORE", "UNKNOWN", ("negative_score",))
        scope, scope_flags = _scope_status(match)
        if scope == "EXTRA_TIME" or scope == "PENALTIES":
            return Validation("EXCLUDED_NON_REGULATION", scope, tuple(scope_flags))
        if scope == "UNKNOWN" and not bool(getattr(self.settings, "result_backfill_allow_unknown_scope", False)):
            return Validation("SCOPE_UNKNOWN", scope, tuple(scope_flags))
        flags = list(scope_flags)
        if match.ht_score_home is None or match.ht_score_away is None:
            return Validation("VALID_FT_ONLY", scope, tuple(sorted(set(flags))))
        if match.score_home < match.ht_score_home or match.score_away < match.ht_score_away:
            return Validation("INVALID_SCORE", scope, tuple(sorted(set(flags + ["ft_below_ht_score"]))))
        second_half_goals = int(match.score_home + match.score_away - match.ht_score_home - match.ht_score_away)
        if second_half_goals < 0:
            return Validation("INVALID_SCORE", scope, tuple(sorted(set(flags + ["ft_total_below_ht_total"]))))
        if event.ht_score_home is not None and event.ht_score_away is not None:
            if (event.ht_score_home, event.ht_score_away) != (match.ht_score_home, match.ht_score_away):
                flags.append("tipico_event_halftime_conflict")
                return Validation("TIPICO_HT_CONFLICT", scope, tuple(sorted(set(flags))))
        return Validation(
            "VALID_SCOPE_ASSUMED" if scope == "UNKNOWN" else "VALID_REGULATION",
            scope,
            tuple(sorted(set(flags))),
            second_half_goals,
            "0" if second_half_goals == 0 else "1" if second_half_goals == 1 else "2_PLUS",
        )

    @staticmethod
    def _retry_at(status: str, attempt_count: int, moment: datetime) -> str | None:
        if status in TERMINAL_QUEUE_STATUSES:
            return None
        if status == "NOT_FINISHED":
            delay_hours = 5
        elif status in {"AMBIGUOUS", "SCOPE_UNKNOWN", "IDENTITY_CONFLICT", "INCOMPLETE_SCORE", "INVALID_SCORE", "TIPICO_HT_CONFLICT"}:
            delay_hours = 168
        elif status in {"NO_CANDIDATE", "UNMATCHED", "NO_DATA"}:
            delay_hours = 24 if attempt_count <= 1 else 72 if attempt_count <= 3 else 168
        else:
            delay_hours = 24
        return _iso(moment + timedelta(hours=delay_hours))

    def _evidence_id(self, plan: BackfillPlan, fetched: FotMobFetchResult | None, validation_status: str) -> str:
        provider_id = plan.provider_match_id or ""
        payload_hash = _payload_hash(fetched.payload) if fetched is not None else None
        seed = "|".join((plan.event.event_id, provider_id, payload_hash or "", validation_status))
        return f"fotmob-result-{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:32]}"

    def _persist(
        self,
        plan: BackfillPlan,
        *,
        fetched: FotMobFetchResult | None,
        match: FotMobMatch | None,
        validation: Validation,
        queue_status: str,
        error: str | None,
        moment: datetime,
        apply: bool,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        event = plan.event
        attempt = event.backfill_attempt_count + 1
        provider_id = plan.provider_match_id or (match.provider_match_id if match else None)
        payload_hash = _payload_hash(fetched.payload) if fetched is not None else None
        evidence_id = self._evidence_id(plan, fetched, validation.status)
        evidence = {
            "evidence_id": evidence_id,
            "event_id": event.event_id,
            "provider": "FOTMOB",
            "provider_match_id": provider_id,
            "fetched_at": _iso(moment),
            "response_status": fetched.status_code if fetched is not None else None,
            "provider_endpoint": fetched.endpoint if fetched is not None else None,
            "payload_hash": payload_hash,
            "match_confidence": plan.confidence,
            "identity_status": plan.identity_status,
            "validation_status": validation.status,
            "validation_flags_json": list(validation.flags),
            "provider_status": match.status if match is not None else None,
            "scope_status": validation.scope_status,
            "ht_home": match.ht_score_home if match is not None else None,
            "ht_away": match.ht_score_away if match is not None else None,
            "ft_home": match.score_home if match is not None else None,
            "ft_away": match.score_away if match is not None else None,
            "second_half_goals": validation.second_half_goals,
            "second_half_goal_class": validation.second_half_goal_class,
            "competition_id": match.competition_id if match is not None else None,
            "competition_name": match.competition_name if match is not None else None,
            "competition_country": match.competition_country if match is not None else None,
            "home_team": match.home_team if match is not None else None,
            "away_team": match.away_team if match is not None else None,
            "kickoff_at": match.kickoff_at if match is not None else event.kickoff_time,
            "source_context": plan.source_context,
            "raw_payload_path": None,
            "source_record_type": "FOTMOB_MATCH_DETAIL" if match is not None else "FOTMOB_LOOKUP",
            "source_record_id": provider_id,
            "observed_at": _iso(moment),
            "raw_status": match.status if match is not None else None,
            "raw_period": match.period if match is not None else None,
            "result_status": (
                "VERIFIED"
                if validation.status in {"VALID_REGULATION", "VALID_SCOPE_ASSUMED", "VALID_FT_ONLY"}
                else "PENDING"
            ),
            "result_use_ft": int(
                validation.status in {"VALID_REGULATION", "VALID_SCOPE_ASSUMED", "VALID_FT_ONLY"}
            ),
            "result_use_h2": int(
                validation.status in {"VALID_REGULATION", "VALID_SCOPE_ASSUMED"}
            ),
            "rule_version": "v0.6.5",
            "created_at": _iso(moment),
        }
        result_values = None
        if validation.status in {"VALID_REGULATION", "VALID_SCOPE_ASSUMED", "VALID_FT_ONLY"} and match is not None:
            result_values = {
                "event_id": event.event_id,
                "competition_id": event.competition_id or match.competition_id,
                "competition_name": event.competition_name or match.competition_name,
                "competition_country": event.competition_country or match.competition_country,
                "home_team": event.home_team,
                "away_team": event.away_team,
                "kickoff_at": event.kickoff_time or match.kickoff_at,
                "ht_home": match.ht_score_home,
                "ht_away": match.ht_score_away,
                "ft_home": match.score_home,
                "ft_away": match.score_away,
                "first_half_goals": (
                    int(match.ht_score_home + match.ht_score_away)
                    if match.ht_score_home is not None and match.ht_score_away is not None
                    else None
                ),
                "second_half_goals": validation.second_half_goals,
                "second_half_goal_class": validation.second_half_goal_class,
                "final_status": "finished",
                "finished_at": _iso(moment),
                "extra_time": 0 if validation.scope_status == "REGULATION" else None,
                "penalties": 0 if validation.scope_status == "REGULATION" else None,
                "result_source": "FOTMOB_BACKFILL",
                "result_evidence_id": evidence_id,
                "result_confidence": plan.confidence,
                "result_scope_status": validation.scope_status,
                "result_resolved_at": _iso(moment),
                "result_revision": 0,
                "result_last_checked_at": _iso(moment),
                "result_status": "VERIFIED",
                "result_use_ft": 1,
                "result_use_h2": int(validation.status != "VALID_FT_ONLY"),
                "result_reason": "HT_MISSING" if validation.status == "VALID_FT_ONLY" else "FOTMOB_BACKFILL_VERIFIED",
                "result_raw_status": match.status,
                "result_ht_source": "FOTMOB_BACKFILL" if validation.status != "VALID_FT_ONLY" else None,
                "result_ft_source": "FOTMOB_BACKFILL",
                # FotMob gives us a terminal provider observation, not a
                # trustworthy real-world final-whistle timestamp.  Keep the
                # check time separately and leave ended_at unknown.
                "ended_at": None,
                "end_observed_at": _iso(moment),
                "result_rule_version": "v0.6.5",
            }
        next_attempt = self._retry_at(queue_status, attempt, moment)
        queue = {
            "event_id": event.event_id,
            "status": queue_status,
            "attempt_count": attempt,
            "next_attempt_at": next_attempt,
            "last_attempt_at": _iso(moment),
            "last_provider_match_id": provider_id,
            "identity_status": plan.identity_status,
            "validation_status": validation.status,
            "last_error": error,
            "resolved_at": _iso(moment) if queue_status in TERMINAL_QUEUE_STATUSES else None,
            "updated_at": _iso(moment),
        }
        result = {
            "event_id": event.event_id,
            "home_team": event.home_team,
            "away_team": event.away_team,
            "competition": event.competition_name,
            "country": event.competition_country,
            "provider_match_id": provider_id,
            "identity_status": plan.identity_status,
            "validation_status": validation.status,
            "queue_status": queue_status,
            "confidence": plan.confidence,
            "ht": [match.ht_score_home, match.ht_score_away] if match is not None else None,
            "ft": [match.score_home, match.score_away] if match is not None else None,
            "second_half_goals": validation.second_half_goals,
            "flags": list(validation.flags),
            "error": error,
            "evidence_id": evidence_id,
            "would_apply": result_values is not None and queue_status == "APPLIED",
        }
        if apply:
            persisted = self.database.persist_result_backfill_outcome(
                evidence,
                queue,
                result_values=result_values,
                run_id=run_id,
            )
            result.update(persisted)
            if persisted.get("result_action") == "RESULT_CONFLICT":
                result["queue_status"] = "CONFLICT"
        return result

    def run(
        self,
        *,
        apply: bool = False,
        limit: int | None = None,
        workers: int | None = None,
        mode: str | None = None,
        refresh_index: bool = False,
        allow_unknown_scope: bool | None = None,
        now: datetime | None = None,
        event_id: str | None = None,
        event_ids: Sequence[str] | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        started = now or _now()
        if allow_unknown_scope is not None:
            self.settings.result_backfill_allow_unknown_scope = bool(allow_unknown_scope)  # type: ignore[misc]
        if not bool(getattr(self.settings, "result_backfill_enabled", True)):
            return {
                "status": "DISABLED",
                "started_at": _iso(started),
                "finished_at": _iso(_now()),
                "dry_run": not apply,
            }
        batch_limit = max(1, int(limit or getattr(self.settings, "result_backfill_limit", 500)))
        worker_count = max(1, int(workers or getattr(self.settings, "result_backfill_workers", 10)))
        worker_count = min(worker_count, int(getattr(self.settings, "fotmob_max_workers", 40)))
        execution_mode = str(mode or getattr(self.settings, "fotmob_network_mode", "worker")).strip().casefold()
        if execution_mode not in NETWORK_MODES:
            execution_mode = "worker"
        network_allowed = self._network_allowed(execution_mode)
        candidate_rows = self._candidate_rows(
            current=started,
            limit=batch_limit,
            event_id=event_id,
            event_ids=event_ids,
        )
        events = [_event_from_row(row) for row in candidate_rows]
        self._provider_days = []
        catalog = self._catalog(
            events,
            allow_network=network_allowed and refresh_index,
            persist=apply,
            force_refresh=bool(refresh_index),
            run_id=run_id,
        )
        plans = self._plans(events, catalog)
        fetch_ids = sorted({plan.provider_match_id for plan in plans if plan.provider_match_id})
        fetched_by_id: dict[str, FotMobFetchResult] = {}
        fetch_started = _now()
        if fetch_ids and network_allowed:
            with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="result-backfill") as executor:
                futures = {
                    executor.submit(self.client.fetch_match_details, provider_id): provider_id
                    for provider_id in fetch_ids
                }
                for future in as_completed(futures):
                    provider_id = futures[future]
                    try:
                        fetched_by_id[provider_id] = future.result()
                    except Exception as exc:  # pragma: no cover - defensive worker boundary
                        fetched_by_id[provider_id] = FotMobFetchResult(
                            success=False,
                            error=f"{type(exc).__name__}: {exc}",
                        )

        outcomes: list[dict[str, Any]] = []
        for plan in plans:
            if plan.provider_match_id is None:
                if plan.identity_status == "AMBIGUOUS":
                    queue_status = "AMBIGUOUS"
                    validation = Validation("AMBIGUOUS", "UNKNOWN", plan.reasons or ("ambiguous_match",))
                elif plan.candidate_count == 0:
                    queue_status = "NO_CANDIDATE"
                    validation = Validation("NO_CANDIDATE", "UNKNOWN", plan.reasons or ("no_daily_index_candidate",))
                else:
                    queue_status = "UNMATCHED"
                    validation = Validation("UNMATCHED", "UNKNOWN", plan.reasons or ("candidate_not_accepted",))
                outcomes.append(
                    self._persist(
                        plan,
                        fetched=None,
                        match=None,
                        validation=validation,
                        queue_status=queue_status,
                        error=None,
                        moment=_now(),
                        apply=apply,
                        run_id=run_id,
                    )
                )
                continue
            if not network_allowed:
                continue
            fetched = fetched_by_id.get(plan.provider_match_id)
            if fetched is None:
                continue
            if not fetched.success or fetched.match is None:
                status = "NO_DATA" if self._is_no_data(fetched) else "ERROR"
                validation = Validation(status, "UNKNOWN", ("detail_fetch_failed",))
                outcomes.append(
                    self._persist(
                        plan,
                        fetched=fetched,
                        match=None,
                        validation=validation,
                        queue_status=status,
                        error=fetched.error,
                        moment=_now(),
                        apply=apply,
                        run_id=run_id,
                    )
                )
                continue
            validation = self._validate(plan.event, fetched.match, catalog.by_id.get(plan.provider_match_id))
            queue_status = {
                "VALID_REGULATION": "APPLIED",
                "VALID_SCOPE_ASSUMED": "APPLIED",
                "VALID_FT_ONLY": "APPLIED",
                "EXCLUDED_NON_REGULATION": "EXCLUDED_NON_REGULATION",
                "NOT_FINISHED": "NOT_FINISHED",
            }.get(validation.status, validation.status)
            outcomes.append(
                self._persist(
                    plan,
                    fetched=fetched,
                    match=fetched.match,
                    validation=validation,
                    queue_status=queue_status,
                    error=None,
                    moment=_now(),
                    apply=apply,
                    run_id=run_id,
                )
            )

        counts: dict[str, int] = defaultdict(int)
        for outcome in outcomes:
            counts[str(outcome.get("queue_status") or outcome.get("validation_status") or "UNKNOWN")] += 1
        applied = sum(1 for outcome in outcomes if outcome.get("applied") or outcome.get("result_action") in {"INSERTED", "FILLED_PARTIAL", "CONFIRMED_EXISTING"})
        finish = _now()
        status = "PASS"
        if execution_mode == "cached" and fetch_ids:
            status = "CACHED_ONLY"
        elif not network_allowed and fetch_ids:
            status = "BLOCKED_BY_POLICY"
        elif counts.get("ERROR") or counts.get("CONFLICT"):
            status = "PARTIAL"
        elif counts.get("NO_CANDIDATE") or counts.get("AMBIGUOUS") or counts.get("NOT_FINISHED"):
            status = "PARTIAL"
        return {
            "status": status,
            "started_at": _iso(started),
            "finished_at": _iso(finish),
            "duration_seconds": round((finish - started).total_seconds(), 3),
            "dry_run": not apply,
            "database_path": str(getattr(self.database, "path", "")),
            "execution_mode": execution_mode,
            "network_allowed": network_allowed,
            "refresh_index": bool(refresh_index),
            "provider_days": list(self._provider_days),
            "workers": worker_count,
            "candidate_limit": batch_limit,
            "event_id": event_id,
            "event_ids_requested": len(event_ids) if event_ids is not None else None,
            "events_selected": len(events),
            "plans": len(plans),
            "provider_ids": len(fetch_ids),
            "details_fetched": len(fetched_by_id),
            "fetch_duration_seconds": round((_now() - fetch_started).total_seconds(), 3),
            "daily_index_matches": len(catalog.by_id),
            "applied": applied,
            "counts": dict(sorted(counts.items())),
            # The complete fixed-list runner consumes this field.  ``samples``
            # remains intentionally capped for the lightweight daily CLI/UI.
            "outcomes": outcomes,
            "samples": outcomes[:50],
            "client_metrics": self.client.metrics_snapshot() if hasattr(self.client, "metrics_snapshot") else {},
        }


def write_backfill_report(result: Mapping[str, Any], path: Path | str) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(dict(result), ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return target
