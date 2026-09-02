"""SQLite catalog and Parquet archive for FotMob historical discovery."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .history_models import (
    FOTMOB_HISTORICAL_PARSER_VERSION,
    FOTMOB_HISTORICAL_SCHEMA_VERSION,
    FOTMOB_SOURCE_PRIORITY,
    FotMobMatchIndexRecord,
    FotMobSeasonRef,
)
from .matching import country_name_for_code


HISTORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS fotmob_seasons (
    provider TEXT NOT NULL DEFAULT 'FOTMOB',
    league_id TEXT NOT NULL,
    season_id TEXT NOT NULL,
    season_label TEXT NOT NULL,
    league_name TEXT,
    country TEXT,
    discovered_at TEXT NOT NULL,
    last_checked_at TEXT,
    PRIMARY KEY (provider, league_id, season_id)
);

CREATE INDEX IF NOT EXISTS idx_fotmob_seasons_league
    ON fotmob_seasons(provider, league_id, season_label);

CREATE TABLE IF NOT EXISTS fotmob_match_index (
    fotmob_match_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL DEFAULT 'FOTMOB',
    league_id TEXT NOT NULL,
    season_id TEXT NOT NULL,
    season_label TEXT NOT NULL,
    league_name TEXT,
    country TEXT,
    country_code TEXT,
    country_name TEXT,
    kickoff_at TEXT,
    home_team_id TEXT,
    home_team_name TEXT NOT NULL,
    away_team_id TEXT,
    away_team_name TEXT NOT NULL,
    round TEXT,
    match_status TEXT,
    detail_status TEXT NOT NULL DEFAULT 'NOT_FETCHED',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    last_checked_at TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TEXT,
    last_error TEXT,
    worker_id TEXT,
    data_quality TEXT,
    ml_eligible INTEGER,
    parser_version TEXT,
    schema_version TEXT,
    raw_payload_path TEXT,
    payload_hash TEXT,
    second_half_goals INTEGER,
    second_half_goal_class TEXT,
    source_type TEXT NOT NULL DEFAULT 'FRESH_INDEX',
    source_context TEXT,
    stats_period TEXT,
    captured_live INTEGER NOT NULL DEFAULT 0,
    field_provenance_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_fotmob_match_index_season
    ON fotmob_match_index(provider, league_id, season_id, kickoff_at, fotmob_match_id);

CREATE INDEX IF NOT EXISTS idx_fotmob_match_index_queue
    ON fotmob_match_index(detail_status, league_id, season_id, kickoff_at);

CREATE TABLE IF NOT EXISTS fotmob_history_samples (
    provider TEXT NOT NULL DEFAULT 'FOTMOB',
    league_id TEXT NOT NULL,
    season_id TEXT NOT NULL,
    sample_rank INTEGER NOT NULL,
    fotmob_match_id TEXT NOT NULL,
    selected_at TEXT NOT NULL,
    PRIMARY KEY (provider, league_id, season_id, sample_rank),
    UNIQUE (provider, league_id, season_id, fotmob_match_id)
);

CREATE TABLE IF NOT EXISTS fotmob_historical_archive_index (
    provider TEXT NOT NULL DEFAULT 'FOTMOB',
    fotmob_match_id TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    archive_path TEXT NOT NULL,
    payload_hash TEXT,
    written_at TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT 'FRESH_FETCH',
    source_priority INTEGER NOT NULL DEFAULT 30,
    source_context TEXT,
    stats_period TEXT,
    captured_live INTEGER NOT NULL DEFAULT 0,
    field_provenance_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (provider, fotmob_match_id, schema_version)
);

CREATE TABLE IF NOT EXISTS fotmob_fixture_index_runs (
    provider TEXT NOT NULL DEFAULT 'FOTMOB',
    run_date TEXT NOT NULL,
    league_id TEXT NOT NULL,
    season_id TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    fixture_count INTEGER NOT NULL DEFAULT 0,
    payload_hash TEXT,
    source_context TEXT NOT NULL DEFAULT 'DAILY_INDEX',
    PRIMARY KEY (provider, run_date, league_id, season_id)
);

CREATE INDEX IF NOT EXISTS idx_fotmob_fixture_index_runs_lookup
    ON fotmob_fixture_index_runs(provider, league_id, fetched_at DESC);

-- One durable row per requested UTC day and provider fixture.  This is the
-- small, query-friendly daily catalog; match statistics remain in Parquet.
CREATE TABLE IF NOT EXISTS fotmob_daily_index (
    provider TEXT NOT NULL DEFAULT 'FOTMOB',
    observation_date TEXT NOT NULL,
    fotmob_match_id TEXT NOT NULL,
    league_id TEXT NOT NULL,
    league_name TEXT,
    country_code TEXT,
    country_name TEXT,
    season_id TEXT,
    season_label TEXT,
    kickoff_at_utc TEXT,
    home_team_id TEXT,
    home_team_name TEXT NOT NULL,
    away_team_id TEXT,
    away_team_name TEXT NOT NULL,
    round TEXT,
    match_status TEXT,
    is_next_day INTEGER NOT NULL DEFAULT 0,
    source_endpoint TEXT,
    payload_hash TEXT,
    fetched_at TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    PRIMARY KEY (provider, observation_date, fotmob_match_id)
);

CREATE INDEX IF NOT EXISTS idx_fotmob_daily_index_date
    ON fotmob_daily_index(provider, observation_date, league_id, kickoff_at_utc);

CREATE INDEX IF NOT EXISTS idx_fotmob_daily_index_match
    ON fotmob_daily_index(provider, fotmob_match_id, observation_date);

CREATE INDEX IF NOT EXISTS idx_fotmob_daily_index_country
    ON fotmob_daily_index(provider, country_code, observation_date, kickoff_at_utc);

CREATE INDEX IF NOT EXISTS idx_fotmob_daily_index_league
    ON fotmob_daily_index(provider, league_id, observation_date, kickoff_at_utc);

CREATE INDEX IF NOT EXISTS idx_fotmob_daily_index_season
    ON fotmob_daily_index(provider, season_label, observation_date, kickoff_at_utc);

CREATE TABLE IF NOT EXISTS fotmob_daily_load_runs (
    provider TEXT NOT NULL DEFAULT 'FOTMOB',
    observation_date TEXT NOT NULL,
    league_id TEXT NOT NULL,
    season_id TEXT,
    status TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    fixture_count INTEGER NOT NULL DEFAULT 0,
    selected_count INTEGER NOT NULL DEFAULT 0,
    detail_count INTEGER NOT NULL DEFAULT 0,
    skipped_no_halftime_count INTEGER NOT NULL DEFAULT 0,
    feed_group_count INTEGER NOT NULL DEFAULT 0,
    feed_entry_count INTEGER NOT NULL DEFAULT 0,
    feed_unique_count INTEGER NOT NULL DEFAULT 0,
    next_day_count INTEGER NOT NULL DEFAULT 0,
    duplicates_removed_count INTEGER NOT NULL DEFAULT 0,
    payload_hash TEXT,
    source_endpoint TEXT,
    error TEXT,
    PRIMARY KEY (provider, observation_date, league_id, season_id)
);

CREATE INDEX IF NOT EXISTS idx_fotmob_daily_load_runs_date
    ON fotmob_daily_load_runs(provider, observation_date, league_id, status);

CREATE TABLE IF NOT EXISTS fotmob_performance_profile (
    profile_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    tested_at TEXT NOT NULL,
    from_date TEXT,
    to_date TEXT,
    phase TEXT NOT NULL DEFAULT 'RPS',
    rps REAL NOT NULL,
    workers INTEGER NOT NULL,
    requests INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    successful INTEGER NOT NULL DEFAULT 0,
    http_429 INTEGER NOT NULL DEFAULT 0,
    http_403 INTEGER NOT NULL DEFAULT 0,
    http_5xx INTEGER NOT NULL DEFAULT 0,
    timeouts INTEGER NOT NULL DEFAULT 0,
    connection_errors INTEGER NOT NULL DEFAULT 0,
    retries INTEGER NOT NULL DEFAULT 0,
    parse_failures INTEGER NOT NULL DEFAULT 0,
    success_rate REAL NOT NULL DEFAULT 0,
    rate_limit_rate REAL NOT NULL DEFAULT 0,
    error_rate REAL NOT NULL DEFAULT 0,
    median_latency_ms REAL NOT NULL DEFAULT 0,
    p95_latency_ms REAL NOT NULL DEFAULT 0,
    effective_rps REAL NOT NULL DEFAULT 0,
    matches_per_minute REAL NOT NULL DEFAULT 0,
    megabytes_per_minute REAL NOT NULL DEFAULT 0,
    connection_pool_size INTEGER,
    cpu_time_seconds REAL,
    cpu_utilization_percent REAL,
    rss_peak_bytes INTEGER,
    rss_delta_bytes INTEGER,
    rate_wait_ms REAL,
    rate_wait_ratio REAL,
    controller_rps REAL,
    rate_slot_rps REAL,
    rate_slot_span_seconds REAL,
    rate_slot_interval_median_ms REAL,
    request_start_rps REAL,
    request_start_span_seconds REAL,
    request_start_interval_median_ms REAL,
    detail_call_median_ms REAL,
    detail_call_p95_ms REAL,
    parse_median_ms REAL,
    parse_p95_ms REAL,
    status TEXT NOT NULL,
    bottleneck TEXT,
    notes TEXT,
    UNIQUE(run_id, phase, rps, workers)
);

CREATE INDEX IF NOT EXISTS idx_fotmob_performance_profile_lookup
    ON fotmob_performance_profile(phase, status, rps, workers, tested_at);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _payload_hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _safe_path_part(value: Any, default: str = "unknown") -> str:
    text = str(value or default).strip()
    text = text.replace("/", "-").replace("\\", "-")
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text)
    return text or default


class FotMobHistoryStore:
    """Persistent catalog for season discovery, index state and archive keys."""

    def __init__(self, database: Any, archive_root: Path | str, raw_root: Path | str | None = None) -> None:
        self.database = database
        self.archive_root = Path(archive_root)
        self.raw_root = Path(raw_root) if raw_root is not None else None
        self.archive_root.mkdir(parents=True, exist_ok=True)
        if self.raw_root is not None:
            self.raw_root.mkdir(parents=True, exist_ok=True)
        self._lock = getattr(database, "_lock", threading.RLock())
        with self._lock, database.connection:
            database.connection.executescript(HISTORY_SCHEMA)
            # V0.5.2 databases already exist in the field.  Keep the migration
            # additive and do not rebuild or delete the historical catalog.
            for table, columns in {
                "fotmob_match_index": {
                    "country_code": "TEXT",
                    "country_name": "TEXT",
                    "source_type": "TEXT NOT NULL DEFAULT 'FRESH_INDEX'",
                    "source_context": "TEXT",
                    "stats_period": "TEXT",
                    "captured_live": "INTEGER NOT NULL DEFAULT 0",
                    "field_provenance_json": "TEXT NOT NULL DEFAULT '{}'",
                },
                "fotmob_historical_archive_index": {
                    "source_type": "TEXT NOT NULL DEFAULT 'FRESH_FETCH'",
                    "source_priority": "INTEGER NOT NULL DEFAULT 30",
                    "source_context": "TEXT",
                    "stats_period": "TEXT",
                    "captured_live": "INTEGER NOT NULL DEFAULT 0",
                    "field_provenance_json": "TEXT NOT NULL DEFAULT '{}'",
                },
                "fotmob_daily_index": {
                    "is_next_day": "INTEGER NOT NULL DEFAULT 0",
                },
                "fotmob_daily_load_runs": {
                    "skipped_no_halftime_count": "INTEGER NOT NULL DEFAULT 0",
                    "feed_group_count": "INTEGER NOT NULL DEFAULT 0",
                    "feed_entry_count": "INTEGER NOT NULL DEFAULT 0",
                    "feed_unique_count": "INTEGER NOT NULL DEFAULT 0",
                    "next_day_count": "INTEGER NOT NULL DEFAULT 0",
                    "duplicates_removed_count": "INTEGER NOT NULL DEFAULT 0",
                },
                "fotmob_performance_profile": {
                    "rate_limit_rate": "REAL NOT NULL DEFAULT 0",
                    "connection_pool_size": "INTEGER",
                    "cpu_time_seconds": "REAL",
                    "cpu_utilization_percent": "REAL",
                    "rss_peak_bytes": "INTEGER",
                    "rss_delta_bytes": "INTEGER",
                    "rate_wait_ms": "REAL",
                    "rate_wait_ratio": "REAL",
                    "controller_rps": "REAL",
                    "rate_slot_rps": "REAL",
                    "rate_slot_span_seconds": "REAL",
                    "rate_slot_interval_median_ms": "REAL",
                    "request_start_rps": "REAL",
                    "request_start_span_seconds": "REAL",
                    "request_start_interval_median_ms": "REAL",
                    "detail_call_median_ms": "REAL",
                    "detail_call_p95_ms": "REAL",
                    "parse_median_ms": "REAL",
                    "parse_p95_ms": "REAL",
                },
            }.items():
                existing_columns = {
                    str(row["name"])
                    for row in database.connection.execute(f"PRAGMA table_info({table})").fetchall()
                }
                for column, definition in columns.items():
                    if column not in existing_columns:
                        database.connection.execute(
                            f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
                        )

    @property
    def connection(self) -> sqlite3.Connection:
        return self.database.connection

    def upsert_seasons(self, seasons: Iterable[FotMobSeasonRef]) -> dict[str, int]:
        season_list = list(seasons)
        if not season_list:
            return {"discovered": 0, "inserted": 0, "updated": 0}
        inserted = 0
        updated = 0
        with self._lock, self.connection:
            for season in season_list:
                existing = self.connection.execute(
                    """
                    SELECT 1 FROM fotmob_seasons
                    WHERE provider = ? AND league_id = ? AND season_id = ?
                    """,
                    (season.provider.upper(), season.league_id, season.season_id),
                ).fetchone()
                now = season.discovered_at or _now()
                self.connection.execute(
                    """
                    INSERT INTO fotmob_seasons (
                        provider, league_id, season_id, season_label, league_name,
                        country, discovered_at, last_checked_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(provider, league_id, season_id) DO UPDATE SET
                        season_label = excluded.season_label,
                        league_name = COALESCE(excluded.league_name, fotmob_seasons.league_name),
                        country = COALESCE(excluded.country, fotmob_seasons.country),
                        last_checked_at = excluded.last_checked_at
                    """,
                    (
                        season.provider.upper(), season.league_id, season.season_id,
                        season.season_label, season.league_name, season.country, now, now,
                    ),
                )
                if existing is None:
                    inserted += 1
                else:
                    updated += 1
        return {"discovered": len(season_list), "inserted": inserted, "updated": updated}

    def seasons(self, league_id: str, provider: str = "FOTMOB") -> list[sqlite3.Row]:
        with self._lock:
            return list(self.connection.execute(
                """
                SELECT * FROM fotmob_seasons
                WHERE provider = ? AND league_id = ?
                ORDER BY season_label DESC, season_id DESC
                """,
                (provider.upper(), str(league_id)),
            ).fetchall())

    def season(self, league_id: str, season_id: str, provider: str = "FOTMOB") -> sqlite3.Row | None:
        with self._lock:
            return self.connection.execute(
                """
                SELECT * FROM fotmob_seasons
                WHERE provider = ? AND league_id = ? AND season_id = ?
                """,
                (provider.upper(), str(league_id), str(season_id)),
            ).fetchone()

    def upsert_match_index(self, records: Iterable[FotMobMatchIndexRecord]) -> dict[str, int]:
        record_list = list(records)
        if not record_list:
            return {"discovered": 0, "existing": 0, "inserted": 0, "updated": 0}
        inserted = 0
        updated = 0
        with self._lock, self.connection:
            for record in record_list:
                existing = self.connection.execute(
                    "SELECT 1 FROM fotmob_match_index WHERE fotmob_match_id = ?",
                    (record.provider_match_id,),
                ).fetchone()
                seen_at = record.first_seen_at or _now()
                source_type = str(record.source_type or "FRESH_INDEX").upper()
                if source_type not in FOTMOB_SOURCE_PRIORITY:
                    source_type = "FRESH_INDEX"
                provenance_json = _json(record.field_provenance or {})
                self.connection.execute(
                    """
                    INSERT INTO fotmob_match_index (
                        fotmob_match_id, provider, league_id, season_id, season_label,
                        league_name, country, country_code, country_name, kickoff_at,
                        home_team_id, home_team_name,
                        away_team_id, away_team_name, round, match_status,
                        first_seen_at, last_seen_at, source_type, source_context,
                        stats_period, captured_live, field_provenance_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(fotmob_match_id) DO UPDATE SET
                        provider = excluded.provider,
                        league_id = excluded.league_id,
                        season_id = excluded.season_id,
                        season_label = excluded.season_label,
                        league_name = COALESCE(excluded.league_name, fotmob_match_index.league_name),
                        country = COALESCE(excluded.country, fotmob_match_index.country),
                        country_code = COALESCE(excluded.country_code, fotmob_match_index.country_code),
                        country_name = COALESCE(excluded.country_name, fotmob_match_index.country_name),
                        kickoff_at = COALESCE(excluded.kickoff_at, fotmob_match_index.kickoff_at),
                        home_team_id = COALESCE(excluded.home_team_id, fotmob_match_index.home_team_id),
                        home_team_name = excluded.home_team_name,
                        away_team_id = COALESCE(excluded.away_team_id, fotmob_match_index.away_team_id),
                        away_team_name = excluded.away_team_name,
                        round = COALESCE(excluded.round, fotmob_match_index.round),
                        match_status = COALESCE(excluded.match_status, fotmob_match_index.match_status),
                        last_seen_at = excluded.last_seen_at,
                        source_type = CASE
                            WHEN excluded.source_type != 'FRESH_INDEX' THEN excluded.source_type
                            ELSE fotmob_match_index.source_type
                        END,
                        source_context = COALESCE(excluded.source_context, fotmob_match_index.source_context),
                        stats_period = COALESCE(excluded.stats_period, fotmob_match_index.stats_period),
                        captured_live = MAX(fotmob_match_index.captured_live, excluded.captured_live),
                        field_provenance_json = CASE
                            WHEN excluded.field_provenance_json != '{}' THEN excluded.field_provenance_json
                            ELSE fotmob_match_index.field_provenance_json
                        END
                    """,
                    (
                        record.provider_match_id,
                        record.provider.upper(),
                        record.league_id,
                        record.season_id,
                        record.season_label,
                        record.league_name,
                        record.country,
                        record.country_code or self._country_code(record.country),
                        record.country_name or self._country_name(record.country),
                        record.kickoff_at,
                        record.home_team_id,
                        record.home_team_name,
                        record.away_team_id,
                        record.away_team_name,
                        record.round_name,
                        record.match_status,
                        seen_at,
                        seen_at,
                        source_type,
                        record.source_context,
                        record.stats_period,
                        int(bool(record.captured_live)),
                        provenance_json,
                    ),
                )
                if existing is None:
                    inserted += 1
                else:
                    updated += 1
        return {
            "discovered": len(record_list),
            "existing": updated,
            "inserted": inserted,
            "updated": updated,
        }

    def record_fixture_index_run(
        self,
        league_id: str,
        season_id: str,
        *,
        fixture_count: int,
        payload_hash: str | None = None,
        run_date: str | None = None,
        fetched_at: str | None = None,
        source_context: str = "DAILY_INDEX",
        provider: str = "FOTMOB",
    ) -> None:
        fetched = fetched_at or _now()
        day = str(run_date or fetched[:10])
        with self._lock, self.connection:
            self.connection.execute(
                """
                INSERT INTO fotmob_fixture_index_runs (
                    provider, run_date, league_id, season_id, fetched_at,
                    fixture_count, payload_hash, source_context
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider, run_date, league_id, season_id) DO UPDATE SET
                    fetched_at = excluded.fetched_at,
                    fixture_count = excluded.fixture_count,
                    payload_hash = excluded.payload_hash,
                    source_context = excluded.source_context
                """,
                (
                    provider.upper(), day, str(league_id), str(season_id), fetched,
                    max(0, int(fixture_count)), payload_hash, source_context,
                ),
            )

    @staticmethod
    def _country_code(value: Any) -> str | None:
        text = str(value or "").strip()
        if not text:
            return None
        normalized = text.casefold()
        known = {
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
        return known.get(normalized, text.upper() if len(text) in {2, 3} else text.upper())

    @staticmethod
    def _country_name(value: Any) -> str | None:
        text = str(value or "").strip()
        if not text:
            return None
        names = {
            "GER": "Deutschland",
            "DEU": "Deutschland",
            "AUT": "Österreich",
            "ENG": "England",
            "ESP": "Spanien",
            "FRA": "Frankreich",
            "ITA": "Italien",
            "NED": "Niederlande",
        }
        return names.get(text.upper()) or country_name_for_code(text) or text

    def upsert_daily_index(
        self,
        records: Iterable[FotMobMatchIndexRecord],
        *,
        observation_date: str,
        source_endpoint: str | None = None,
        payload_hash: str | None = None,
        fetched_at: str | None = None,
        provider: str = "FOTMOB",
    ) -> dict[str, int]:
        """Persist the date-bounded fixture catalog without storing stats."""

        record_list = list(records)
        fetched = fetched_at or _now()
        day = str(observation_date)
        inserted = updated = 0
        with self._lock, self.connection:
            for record in record_list:
                country_code = record.country_code or self._country_code(record.country)
                country_name = record.country_name or self._country_name(record.country)
                existing = self.connection.execute(
                    """
                    SELECT 1 FROM fotmob_daily_index
                    WHERE provider = ? AND observation_date = ? AND fotmob_match_id = ?
                    """,
                    (provider.upper(), day, record.provider_match_id),
                ).fetchone()
                first_seen = record.first_seen_at or fetched
                self.connection.execute(
                    """
                    INSERT INTO fotmob_daily_index (
                        provider, observation_date, fotmob_match_id, league_id,
                        league_name, country_code, country_name, season_id,
                        season_label, kickoff_at_utc, home_team_id, home_team_name,
                        away_team_id, away_team_name, round, match_status, is_next_day,
                        source_endpoint, payload_hash, fetched_at, first_seen_at,
                        last_seen_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(provider, observation_date, fotmob_match_id) DO UPDATE SET
                        league_id = excluded.league_id,
                        league_name = COALESCE(excluded.league_name, fotmob_daily_index.league_name),
                        country_code = COALESCE(excluded.country_code, fotmob_daily_index.country_code),
                        country_name = COALESCE(excluded.country_name, fotmob_daily_index.country_name),
                        season_id = COALESCE(excluded.season_id, fotmob_daily_index.season_id),
                        season_label = COALESCE(excluded.season_label, fotmob_daily_index.season_label),
                        kickoff_at_utc = COALESCE(excluded.kickoff_at_utc, fotmob_daily_index.kickoff_at_utc),
                        home_team_id = COALESCE(excluded.home_team_id, fotmob_daily_index.home_team_id),
                        home_team_name = excluded.home_team_name,
                        away_team_id = COALESCE(excluded.away_team_id, fotmob_daily_index.away_team_id),
                        away_team_name = excluded.away_team_name,
                        round = COALESCE(excluded.round, fotmob_daily_index.round),
                        match_status = COALESCE(excluded.match_status, fotmob_daily_index.match_status),
                        is_next_day = excluded.is_next_day,
                        source_endpoint = COALESCE(excluded.source_endpoint, fotmob_daily_index.source_endpoint),
                        payload_hash = COALESCE(excluded.payload_hash, fotmob_daily_index.payload_hash),
                        fetched_at = excluded.fetched_at,
                        last_seen_at = excluded.last_seen_at
                    """,
                    (
                        provider.upper(), day, record.provider_match_id,
                        record.league_id, record.league_name, country_code,
                        country_name, record.season_id, record.season_label,
                        record.kickoff_at, record.home_team_id, record.home_team_name,
                        record.away_team_id, record.away_team_name, record.round_name,
                        record.match_status, int(bool(record.is_next_day)), source_endpoint, payload_hash, fetched,
                        first_seen, fetched,
                    ),
                )
                if existing is None:
                    inserted += 1
                else:
                    updated += 1
        return {"discovered": len(record_list), "inserted": inserted, "updated": updated}

    def record_daily_load_run(
        self,
        observation_date: str,
        league_id: str,
        *,
        season_id: str | None,
        status: str,
        fixture_count: int = 0,
        selected_count: int = 0,
        detail_count: int = 0,
        skipped_no_halftime_count: int = 0,
        feed_group_count: int | None = None,
        feed_entry_count: int | None = None,
        feed_unique_count: int | None = None,
        next_day_count: int | None = None,
        duplicates_removed_count: int | None = None,
        payload_hash: str | None = None,
        source_endpoint: str | None = None,
        error: str | None = None,
        fetched_at: str | None = None,
        provider: str = "FOTMOB",
    ) -> None:
        with self._lock, self.connection:
            self.connection.execute(
                """
                INSERT INTO fotmob_daily_load_runs (
                    provider, observation_date, league_id, season_id, status,
                    fetched_at, fixture_count, selected_count, detail_count,
                    skipped_no_halftime_count,
                    feed_group_count, feed_entry_count, feed_unique_count,
                    next_day_count, duplicates_removed_count,
                    payload_hash, source_endpoint, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          COALESCE(?, 0), COALESCE(?, 0), COALESCE(?, 0),
                          COALESCE(?, 0), COALESCE(?, 0), ?, ?, ?)
                ON CONFLICT(provider, observation_date, league_id, season_id) DO UPDATE SET
                    status = excluded.status,
                    fetched_at = excluded.fetched_at,
                    fixture_count = excluded.fixture_count,
                    selected_count = excluded.selected_count,
                    detail_count = excluded.detail_count,
                    skipped_no_halftime_count = excluded.skipped_no_halftime_count,
                    feed_group_count = CASE WHEN ? IS NULL THEN fotmob_daily_load_runs.feed_group_count ELSE excluded.feed_group_count END,
                    feed_entry_count = CASE WHEN ? IS NULL THEN fotmob_daily_load_runs.feed_entry_count ELSE excluded.feed_entry_count END,
                    feed_unique_count = CASE WHEN ? IS NULL THEN fotmob_daily_load_runs.feed_unique_count ELSE excluded.feed_unique_count END,
                    next_day_count = CASE WHEN ? IS NULL THEN fotmob_daily_load_runs.next_day_count ELSE excluded.next_day_count END,
                    duplicates_removed_count = CASE WHEN ? IS NULL THEN fotmob_daily_load_runs.duplicates_removed_count ELSE excluded.duplicates_removed_count END,
                    payload_hash = COALESCE(excluded.payload_hash, fotmob_daily_load_runs.payload_hash),
                    source_endpoint = COALESCE(excluded.source_endpoint, fotmob_daily_load_runs.source_endpoint),
                    error = CASE
                        WHEN excluded.status IN ('COMPLETE', 'PASS') THEN NULL
                        ELSE COALESCE(excluded.error, fotmob_daily_load_runs.error)
                    END
                """,
                (
                    provider.upper(), str(observation_date), str(league_id),
                    season_id, str(status), fetched_at or _now(),
                    max(0, int(fixture_count)), max(0, int(selected_count)),
                    max(0, int(detail_count)), max(0, int(skipped_no_halftime_count)),
                    feed_group_count, feed_entry_count, feed_unique_count,
                    next_day_count, duplicates_removed_count,
                    payload_hash, source_endpoint, error,
                    feed_group_count, feed_entry_count, feed_unique_count,
                    next_day_count, duplicates_removed_count,
                ),
            )

    def daily_index(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        *,
        league_id: str | None = None,
        league_name: str | None = None,
        country_code: str | None = None,
        country_name: str | None = None,
        season_id: str | None = None,
        season_label: str | None = None,
        provider: str = "FOTMOB",
        limit: int = 500,
        order_by: str = "observation_date",
        ascending: bool = False,
    ) -> list[sqlite3.Row]:
        clauses = ["i.provider = ?"]
        params: list[Any] = [provider.upper()]
        if start_date:
            clauses.append("i.observation_date >= ?")
            params.append(str(start_date))
        if end_date:
            clauses.append("i.observation_date <= ?")
            params.append(str(end_date))
        if league_id:
            clauses.append("i.league_id = ?")
            params.append(str(league_id))
        if league_name:
            clauses.append("i.league_name = ?")
            params.append(str(league_name))
        if country_code:
            clauses.append("i.country_code = ?")
            params.append(str(country_code).upper())
        if country_name:
            clauses.append("i.country_name = ?")
            params.append(str(country_name))
        if season_id:
            clauses.append("i.season_id = ?")
            params.append(str(season_id))
        if season_label:
            clauses.append("i.season_label = ?")
            params.append(str(season_label))
        sort_columns = {
            "observation_date": "i.observation_date",
            "kickoff_at_utc": "i.kickoff_at_utc",
            "country_name": "i.country_name",
            "league_name": "i.league_name",
            "season_label": "i.season_label",
            "home_team_name": "i.home_team_name",
            "away_team_name": "i.away_team_name",
            "fotmob_match_id": "i.fotmob_match_id",
        }
        sort_column = sort_columns.get(str(order_by), "i.observation_date")
        sort_direction = "ASC" if ascending else "DESC"
        params.append(max(1, int(limit)))
        with self._lock:
            return list(self.connection.execute(
                f"""
                SELECT i.*, m.detail_status, m.data_quality, m.ml_eligible,
                       a.archive_path AS canonical_archive_path,
                       COALESCE(a.archive_path, legacy.archive_path) AS historical_archive_path,
                       COALESCE(a.source_type, legacy.source_type) AS historical_source_type
                FROM fotmob_daily_index i
                LEFT JOIN fotmob_match_index m
                  ON m.provider = i.provider AND m.fotmob_match_id = i.fotmob_match_id
                LEFT JOIN fotmob_historical_archive_index a
                  ON a.provider = i.provider AND a.fotmob_match_id = i.fotmob_match_id
                 AND a.schema_version = 'fotmob_match_core_v2'
                LEFT JOIN fotmob_historical_archive_index legacy
                  ON legacy.provider = i.provider AND legacy.fotmob_match_id = i.fotmob_match_id
                 AND legacy.schema_version = 'fotmob_historical_v1'
                WHERE {' AND '.join(clauses)}
                ORDER BY {sort_column} {sort_direction}, i.kickoff_at_utc, i.fotmob_match_id
                LIMIT ?
                """,
                params,
            ).fetchall())

    def daily_load_runs(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        *,
        league_id: str | None = None,
        provider: str = "FOTMOB",
        limit: int = 500,
    ) -> list[sqlite3.Row]:
        clauses = ["provider = ?"]
        params: list[Any] = [provider.upper()]
        if start_date:
            clauses.append("observation_date >= ?")
            params.append(str(start_date))
        if end_date:
            clauses.append("observation_date <= ?")
            params.append(str(end_date))
        if league_id:
            clauses.append("league_id = ?")
            params.append(str(league_id))
        params.append(max(1, int(limit)))
        with self._lock:
            return list(self.connection.execute(
                f"""
                SELECT * FROM fotmob_daily_load_runs
                WHERE {' AND '.join(clauses)}
                ORDER BY observation_date DESC, league_id, season_id
                LIMIT ?
                """,
                params,
            ).fetchall())

    def daily_status(self, league_id: str | None = None) -> dict[str, Any]:
        clauses = ["provider = 'FOTMOB'"]
        params: list[Any] = []
        if league_id:
            clauses.append("league_id = ?")
            params.append(str(league_id))
        where = " AND ".join(clauses)
        with self._lock:
            index = self.connection.execute(
                f"SELECT COUNT(*) AS n, MIN(observation_date) AS first_date, MAX(observation_date) AS last_date FROM fotmob_daily_index WHERE {where}",
                params,
            ).fetchone()
            runs = self.connection.execute(
                f"SELECT COUNT(*) AS n, COUNT(DISTINCT observation_date) AS days FROM fotmob_daily_load_runs WHERE {where}",
                params,
            ).fetchone()
            statuses = self.connection.execute(
                f"SELECT status, COUNT(*) AS n FROM fotmob_daily_load_runs WHERE {where} GROUP BY status",
                params,
            ).fetchall()
        return {
            "index_rows": int(index["n"] or 0) if index else 0,
            "loaded_days": int(runs["days"] or 0) if runs else 0,
            "run_rows": int(runs["n"] or 0) if runs else 0,
            "first_date": index["first_date"] if index else None,
            "last_date": index["last_date"] if index else None,
            "run_status": {str(row["status"]): int(row["n"] or 0) for row in statuses},
        }

    def save_performance_profile(self, profile: Mapping[str, Any]) -> int:
        """Insert or update one measured RPS/worker profile."""

        values = {
            "run_id": str(profile.get("run_id") or "unknown"),
            "tested_at": str(profile.get("tested_at") or _now()),
            "from_date": profile.get("from_date"),
            "to_date": profile.get("to_date"),
            "phase": str(profile.get("phase") or "RPS").upper(),
            "rps": float(profile.get("rps") or 0.0),
            "workers": max(1, int(profile.get("workers") or 1)),
            "requests": max(0, int(profile.get("requests") or 0)),
            "attempts": max(0, int(profile.get("attempts") or 0)),
            "successful": max(0, int(profile.get("successful") or 0)),
            "http_429": max(0, int(profile.get("429") or profile.get("http_429") or 0)),
            "http_403": max(0, int(profile.get("403") or profile.get("http_403") or 0)),
            "http_5xx": max(0, int(profile.get("5xx") or profile.get("http_5xx") or 0)),
            "timeouts": max(0, int(profile.get("timeouts") or profile.get("timeout") or 0)),
            "connection_errors": max(0, int(profile.get("connection_errors") or 0)),
            "retries": max(0, int(profile.get("retries") or 0)),
            "parse_failures": max(0, int(profile.get("parse_failures") or 0)),
            "success_rate": float(profile.get("success_rate") or 0.0),
            "rate_limit_rate": float(
                profile.get("429_rate")
                or profile.get("rate_limit_rate")
                or 0.0
            ),
            "error_rate": float(profile.get("error_rate") or 0.0),
            "median_latency_ms": float(profile.get("median_latency_ms") or 0.0),
            "p95_latency_ms": float(profile.get("p95_latency_ms") or 0.0),
            "effective_rps": float(profile.get("effective_rps") or 0.0),
            "matches_per_minute": float(profile.get("matches_per_minute") or 0.0),
            "megabytes_per_minute": float(profile.get("megabytes_per_minute") or 0.0),
            "connection_pool_size": (
                max(1, int(profile["connection_pool_size"]))
                if profile.get("connection_pool_size") is not None
                else None
            ),
            "cpu_time_seconds": _optional_float(profile.get("cpu_time_seconds")),
            "cpu_utilization_percent": _optional_float(
                profile.get("cpu_utilization_percent")
            ),
            "rss_peak_bytes": _optional_int(profile.get("rss_peak_bytes")),
            "rss_delta_bytes": _optional_int(profile.get("rss_delta_bytes")),
            "rate_wait_ms": _optional_float(profile.get("rate_wait_ms")),
            "rate_wait_ratio": _optional_float(profile.get("rate_wait_ratio")),
            "controller_rps": _optional_float(profile.get("controller_rps")),
            "rate_slot_rps": _optional_float(profile.get("rate_slot_rps")),
            "rate_slot_span_seconds": _optional_float(
                profile.get("rate_slot_span_seconds")
            ),
            "rate_slot_interval_median_ms": _optional_float(
                profile.get("rate_slot_interval_median_ms")
            ),
            "request_start_rps": _optional_float(profile.get("request_start_rps")),
            "request_start_span_seconds": _optional_float(
                profile.get("request_start_span_seconds")
            ),
            "request_start_interval_median_ms": _optional_float(
                profile.get("request_start_interval_median_ms")
            ),
            "detail_call_median_ms": _optional_float(
                profile.get("detail_call_median_ms")
            ),
            "detail_call_p95_ms": _optional_float(profile.get("detail_call_p95_ms")),
            "parse_median_ms": _optional_float(profile.get("parse_median_ms")),
            "parse_p95_ms": _optional_float(profile.get("parse_p95_ms")),
            "status": str(profile.get("status") or "UNKNOWN").upper(),
            "bottleneck": profile.get("bottleneck"),
            "notes": profile.get("notes"),
        }
        columns = tuple(values)
        placeholders = ", ".join("?" for _ in columns)
        updates = ", ".join(
            f"{column} = excluded.{column}"
            for column in columns
            if column not in {"run_id", "phase", "rps", "workers"}
        )
        with self._lock, self.connection:
            self.connection.execute(
                f"""
                INSERT INTO fotmob_performance_profile ({', '.join(columns)})
                VALUES ({placeholders})
                ON CONFLICT(run_id, phase, rps, workers) DO UPDATE SET {updates}
                """,
                tuple(values[column] for column in columns),
            )
            row = self.connection.execute(
                """
                SELECT profile_id
                FROM fotmob_performance_profile
                WHERE run_id = ? AND phase = ? AND rps = ? AND workers = ?
                """,
                (values["run_id"], values["phase"], values["rps"], values["workers"]),
            ).fetchone()
        return int(row["profile_id"]) if row else 0

    def performance_profiles(
        self,
        *,
        run_id: str | None = None,
        phase: str | None = None,
        limit: int = 100,
    ) -> list[sqlite3.Row]:
        clauses = ["1 = 1"]
        params: list[Any] = []
        if run_id:
            clauses.append("run_id = ?")
            params.append(str(run_id))
        if phase:
            clauses.append("phase = ?")
            params.append(str(phase).upper())
        params.append(max(1, int(limit)))
        with self._lock:
            return list(
                self.connection.execute(
                    f"""
                    SELECT * FROM fotmob_performance_profile
                    WHERE {' AND '.join(clauses)}
                    ORDER BY tested_at DESC, phase, rps, workers
                    LIMIT ?
                    """,
                    params,
                ).fetchall()
            )

    def known_stable_max_rps(
        self,
        *,
        confirmations: int = 2,
        workers: int | None = None,
    ) -> float | None:
        clauses = ["phase = 'RPS'", "status = 'STABLE'"]
        params: list[Any] = []
        if workers is not None:
            clauses.append("workers = ?")
            params.append(max(1, int(workers)))
        with self._lock:
            row = self.connection.execute(
                f"""
                SELECT MAX(rps) AS max_rps
                FROM (
                    SELECT rps
                    FROM fotmob_performance_profile
                    WHERE {' AND '.join(clauses)}
                    GROUP BY rps
                    HAVING COUNT(DISTINCT run_id) >= ?
                )
                """,
                (*params, max(1, int(confirmations))),
            ).fetchone()
        if not row or row["max_rps"] is None:
            return None
        return float(row["max_rps"])

    def match_index(
        self,
        league_id: str,
        season_id: str,
        *,
        provider: str = "FOTMOB",
        only_sample: bool = False,
    ) -> list[sqlite3.Row]:
        query = """
            SELECT i.*
            FROM fotmob_match_index i
        """
        params: list[Any] = [provider.upper(), str(league_id), str(season_id)]
        if only_sample:
            query += """
                INNER JOIN fotmob_history_samples s
                    ON s.provider = i.provider
                   AND s.league_id = i.league_id
                   AND s.season_id = i.season_id
                   AND s.fotmob_match_id = i.fotmob_match_id
            """
        query += """
            WHERE i.provider = ? AND i.league_id = ? AND i.season_id = ?
            ORDER BY CASE WHEN i.kickoff_at IS NULL THEN 1 ELSE 0 END,
                     i.kickoff_at, i.fotmob_match_id
        """
        with self._lock:
            return list(self.connection.execute(query, params).fetchall())

    def match_index_for_league(
        self,
        league_id: str,
        *,
        provider: str = "FOTMOB",
    ) -> list[sqlite3.Row]:
        """Return all indexed seasons for resolver-side fixture lookup."""

        with self._lock:
            return list(self.connection.execute(
                """
                SELECT * FROM fotmob_match_index
                WHERE provider = ? AND league_id = ?
                ORDER BY CASE WHEN kickoff_at IS NULL THEN 1 ELSE 0 END,
                         kickoff_at, fotmob_match_id
                """,
                (provider.upper(), str(league_id)),
            ).fetchall())

    def missing_archive_ids(
        self,
        league_id: str,
        *,
        schema_version: str = FOTMOB_HISTORICAL_SCHEMA_VERSION,
        provider: str = "FOTMOB",
    ) -> list[str]:
        """Return indexed fixtures whose detail row is not archived yet."""

        with self._lock:
            rows = self.connection.execute(
                """
                SELECT i.fotmob_match_id
                FROM fotmob_match_index i
                LEFT JOIN fotmob_historical_archive_index a
                    ON a.provider = i.provider
                   AND a.fotmob_match_id = i.fotmob_match_id
                   AND a.schema_version = ?
                WHERE i.provider = ? AND i.league_id = ?
                  AND a.fotmob_match_id IS NULL
                ORDER BY CASE WHEN i.kickoff_at IS NULL THEN 1 ELSE 0 END,
                         i.kickoff_at, i.fotmob_match_id
                """,
                (schema_version, provider.upper(), str(league_id)),
            ).fetchall()
        return [str(row["fotmob_match_id"]) for row in rows]

    def set_sample(self, league_id: str, season_id: str, match_ids: Iterable[str], provider: str = "FOTMOB") -> None:
        ids = [str(value) for value in match_ids]
        now = _now()
        with self._lock, self.connection:
            self.connection.execute(
                "DELETE FROM fotmob_history_samples WHERE provider = ? AND league_id = ? AND season_id = ?",
                (provider.upper(), str(league_id), str(season_id)),
            )
            for rank, match_id in enumerate(ids, start=1):
                self.connection.execute(
                    """
                    INSERT INTO fotmob_history_samples
                        (provider, league_id, season_id, sample_rank, fotmob_match_id, selected_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (provider.upper(), str(league_id), str(season_id), rank, match_id, now),
                )

    def sample(self, league_id: str, season_id: str, provider: str = "FOTMOB") -> list[sqlite3.Row]:
        with self._lock:
            return list(self.connection.execute(
                """
                SELECT s.sample_rank AS sample_rank,
                       s.selected_at AS sample_selected_at,
                       i.*
                FROM fotmob_history_samples s
                INNER JOIN fotmob_match_index i ON i.fotmob_match_id = s.fotmob_match_id
                WHERE s.provider = ? AND s.league_id = ? AND s.season_id = ?
                ORDER BY s.sample_rank
                """,
                (provider.upper(), str(league_id), str(season_id)),
            ).fetchall())

    def recover_stale(self, stale_minutes: int = 30) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=max(1, int(stale_minutes)))).isoformat()
        with self._lock, self.connection:
            cursor = self.connection.execute(
                """
                UPDATE fotmob_match_index
                SET detail_status = 'NOT_FETCHED', worker_id = NULL,
                    last_error = COALESCE(last_error, 'recovered stale IN_PROGRESS claim')
                WHERE detail_status = 'IN_PROGRESS'
                  AND last_attempt_at IS NOT NULL
                  AND last_attempt_at < ?
                """,
                (cutoff,),
            )
        return int(cursor.rowcount if cursor.rowcount is not None else 0)

    def claim_next(
        self,
        league_id: str,
        season_id: str,
        *,
        worker_id: str,
        retry_failed: bool = False,
        max_attempts: int = 3,
        stale_minutes: int = 30,
        only_sample: bool = False,
    ) -> sqlite3.Row | None:
        self.recover_stale(stale_minutes)
        statuses = ["NOT_FETCHED"]
        if retry_failed:
            statuses.append("FAILED")
        placeholders = ", ".join("?" for _ in statuses)
        params: list[Any] = [*statuses, str(league_id), str(season_id), max(1, int(max_attempts)), max(1, int(max_attempts))]
        now = _now()
        with self._lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                query = """
                    SELECT i.*
                    FROM fotmob_match_index i
                """
                if only_sample:
                    query += """
                        INNER JOIN fotmob_history_samples s
                            ON s.provider = i.provider
                           AND s.league_id = i.league_id
                           AND s.season_id = i.season_id
                           AND s.fotmob_match_id = i.fotmob_match_id
                    """
                query += f"""
                    WHERE i.detail_status IN ({placeholders})
                      AND i.league_id = ? AND i.season_id = ?
                      AND i.attempt_count < ?
                      AND (i.detail_status != 'FAILED' OR i.attempt_count < ?)
                    ORDER BY CASE WHEN i.kickoff_at IS NULL THEN 1 ELSE 0 END,
                             i.kickoff_at, i.fotmob_match_id
                    LIMIT 1
                """
                row = self.connection.execute(query, params).fetchone()
                if row is None:
                    self.connection.commit()
                    return None
                cursor = self.connection.execute(
                    """
                    UPDATE fotmob_match_index
                    SET detail_status = 'IN_PROGRESS', worker_id = ?,
                        attempt_count = attempt_count + 1, last_attempt_at = ?,
                        last_checked_at = NULL, last_error = NULL
                    WHERE fotmob_match_id = ? AND detail_status = ?
                    """,
                    (worker_id, now, row["fotmob_match_id"], row["detail_status"]),
                )
                if cursor.rowcount != 1:
                    self.connection.rollback()
                    return None
                claimed = self.connection.execute(
                    "SELECT * FROM fotmob_match_index WHERE fotmob_match_id = ?",
                    (row["fotmob_match_id"],),
                ).fetchone()
                self.connection.commit()
                return claimed
            except Exception:
                self.connection.rollback()
                raise

    def claim_match(
        self,
        provider_match_id: str,
        *,
        worker_id: str,
        retry_failed: bool = False,
        max_attempts: int = 3,
        stale_minutes: int = 30,
        refresh_existing: bool = False,
    ) -> sqlite3.Row | None:
        """Atomically claim one known fixture for a date-bounded job.

        A daily load may deliberately refresh a legacy row once so that the
        fresh canonical archive can supersede it.  Already fresh rows remain
        idempotent and are skipped unless a caller explicitly changes policy.
        """

        self.recover_stale(stale_minutes)
        now = _now()
        with self._lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                row = self.connection.execute(
                    "SELECT * FROM fotmob_match_index WHERE fotmob_match_id = ?",
                    (str(provider_match_id),),
                ).fetchone()
                if row is None:
                    self.connection.commit()
                    return None
                status = str(row["detail_status"] or "NOT_FETCHED")
                source = str(row["source_type"] or "FRESH_INDEX").upper()
                allowed = status == "NOT_FETCHED" or (
                    retry_failed and status == "FAILED"
                )
                if refresh_existing and source != "FRESH_FETCH" and status in {
                    "FETCHED", "PARTIAL", "SKIPPED_NO_HALFTIME",
                }:
                    allowed = True
                if not allowed or int(row["attempt_count"] or 0) >= max(1, int(max_attempts)):
                    self.connection.commit()
                    return None
                cursor = self.connection.execute(
                    """
                    UPDATE fotmob_match_index
                    SET detail_status = 'IN_PROGRESS', worker_id = ?,
                        attempt_count = attempt_count + 1, last_attempt_at = ?,
                        last_checked_at = NULL, last_error = NULL
                    WHERE fotmob_match_id = ? AND detail_status = ?
                    """,
                    (worker_id, now, str(provider_match_id), status),
                )
                if cursor.rowcount != 1:
                    self.connection.rollback()
                    return None
                claimed = self.connection.execute(
                    "SELECT * FROM fotmob_match_index WHERE fotmob_match_id = ?",
                    (str(provider_match_id),),
                ).fetchone()
                self.connection.commit()
                return claimed
            except Exception:
                self.connection.rollback()
                raise

    def mark_success(
        self,
        provider_match_id: str,
        *,
        data_quality: str,
        ml_eligible: bool,
        parser_version: str = FOTMOB_HISTORICAL_PARSER_VERSION,
        schema_version: str = FOTMOB_HISTORICAL_SCHEMA_VERSION,
        raw_payload_path: str | None = None,
        payload_hash: str | None = None,
        second_half_goals: int | None = None,
        second_half_goal_class: str | None = None,
        worker_id: str | None = None,
        source_type: str = "FRESH_FETCH",
        source_context: str | None = "HISTORY_DETAIL",
        stats_period: str | None = "FULL_MATCH",
        captured_live: bool = False,
        field_provenance: Mapping[str, Any] | None = None,
    ) -> str:
        detail_status = "FETCHED" if data_quality == "COMPLETE" else "PARTIAL"
        normalized_source = str(source_type or "FRESH_FETCH").upper()
        if normalized_source not in FOTMOB_SOURCE_PRIORITY:
            normalized_source = "FRESH_FETCH"
        with self._lock, self.connection:
            self.connection.execute(
                """
                UPDATE fotmob_match_index
                SET detail_status = ?, last_checked_at = ?, last_error = NULL,
                    worker_id = NULL, data_quality = ?, ml_eligible = ?,
                    parser_version = ?, schema_version = ?, raw_payload_path = ?,
                    payload_hash = ?, second_half_goals = ?, second_half_goal_class = ?,
                    source_type = ?, source_context = ?, stats_period = ?,
                    captured_live = ?, field_provenance_json = ?
                WHERE fotmob_match_id = ?
                  AND (? IS NULL OR worker_id = ?)
                """,
                (
                    detail_status, _now(), data_quality, int(ml_eligible), parser_version,
                    schema_version, raw_payload_path, payload_hash, second_half_goals,
                    second_half_goal_class, normalized_source, source_context, stats_period,
                    int(bool(captured_live)), _json(field_provenance or {}),
                    str(provider_match_id), worker_id, worker_id,
                ),
            )
        return detail_status

    def mark_skipped_no_halftime(
        self,
        provider_match_id: str,
        *,
        reason: str = "FotMob FirstHalf-Statistiken nicht vorhanden",
        worker_id: str | None = None,
    ) -> str:
        """Record a deliberate detail skip without creating an archive row."""

        with self._lock, self.connection:
            self.connection.execute(
                """
                UPDATE fotmob_match_index
                SET detail_status = 'SKIPPED_NO_HALFTIME',
                    last_checked_at = ?, last_error = ?, worker_id = NULL,
                    data_quality = 'NO_HALFTIME', ml_eligible = 0,
                    parser_version = NULL, schema_version = NULL,
                    raw_payload_path = NULL, payload_hash = NULL,
                    second_half_goals = NULL, second_half_goal_class = NULL
                WHERE fotmob_match_id = ?
                  AND (? IS NULL OR worker_id = ?)
                """,
                (_now(), str(reason)[:2000], str(provider_match_id), worker_id, worker_id),
            )
        return "SKIPPED_NO_HALFTIME"

    def mark_skipped_no_data(
        self,
        provider_match_id: str,
        *,
        reason: str = "FotMob Detaildaten nicht vorhanden",
        worker_id: str | None = None,
    ) -> str:
        """Record a provider fixture whose detail endpoint has no data."""

        with self._lock, self.connection:
            self.connection.execute(
                """
                UPDATE fotmob_match_index
                SET detail_status = 'SKIPPED_NO_DATA',
                    last_checked_at = ?, last_error = ?, worker_id = NULL,
                    data_quality = 'NO_DATA', ml_eligible = 0,
                    parser_version = NULL, schema_version = NULL,
                    raw_payload_path = NULL, payload_hash = NULL,
                    second_half_goals = NULL, second_half_goal_class = NULL
                WHERE fotmob_match_id = ?
                  AND (? IS NULL OR worker_id = ?)
                """,
                (_now(), str(reason)[:2000], str(provider_match_id), worker_id, worker_id),
            )
        return "SKIPPED_NO_DATA"

    def mark_failure(self, provider_match_id: str, error: str, *, max_attempts: int = 3, worker_id: str | None = None) -> str:
        with self._lock, self.connection:
            row = self.connection.execute(
                "SELECT attempt_count FROM fotmob_match_index WHERE fotmob_match_id = ?",
                (str(provider_match_id),),
            ).fetchone()
            attempts = int(row["attempt_count"] if row is not None else max_attempts)
            detail_status = "FAILED" if attempts >= max(1, int(max_attempts)) else "NOT_FETCHED"
            self.connection.execute(
                """
                UPDATE fotmob_match_index
                SET detail_status = ?, last_checked_at = ?, last_error = ?, worker_id = NULL
                WHERE fotmob_match_id = ?
                  AND (? IS NULL OR worker_id = ?)
                """,
                (detail_status, _now(), str(error)[:2000], str(provider_match_id), worker_id, worker_id),
            )
        return detail_status

    def release_worker(self, worker_id: str) -> int:
        with self._lock, self.connection:
            cursor = self.connection.execute(
                """
                UPDATE fotmob_match_index
                SET detail_status = 'NOT_FETCHED', worker_id = NULL,
                    last_error = 'released after interrupted worker'
                WHERE detail_status = 'IN_PROGRESS' AND worker_id = ?
                """,
                (worker_id,),
            )
        return int(cursor.rowcount if cursor.rowcount is not None else 0)

    def status(
        self,
        league_id: str,
        season_id: str,
        provider: str = "FOTMOB",
        *,
        only_sample: bool = False,
    ) -> dict[str, Any]:
        sample_join = ""
        if only_sample:
            sample_join = """
                INNER JOIN fotmob_history_samples s
                    ON s.provider = i.provider
                   AND s.league_id = i.league_id
                   AND s.season_id = i.season_id
                   AND s.fotmob_match_id = i.fotmob_match_id
            """
        with self._lock:
            rows = self.connection.execute(
                f"""
                SELECT detail_status, COUNT(*) AS n
                FROM fotmob_match_index i
                {sample_join}
                WHERE i.provider = ? AND i.league_id = ? AND i.season_id = ?
                GROUP BY detail_status
                """,
                (provider.upper(), str(league_id), str(season_id)),
            ).fetchall()
            archive_row = self.connection.execute(
                f"""
                SELECT COUNT(DISTINCT a.fotmob_match_id) AS n
                FROM fotmob_match_index i
                {sample_join}
                INNER JOIN fotmob_historical_archive_index a
                    ON a.provider = i.provider
                   AND a.fotmob_match_id = i.fotmob_match_id
                WHERE i.provider = ? AND i.league_id = ? AND i.season_id = ?
                """,
                (provider.upper(), str(league_id), str(season_id)),
            ).fetchone()
        counts = {str(row["detail_status"]): int(row["n"]) for row in rows}
        total = sum(counts.values())
        return {
            "league_id": str(league_id),
            "season_id": str(season_id),
            "total": total,
            "counts": counts,
            "fetched": counts.get("FETCHED", 0),
            "partial": counts.get("PARTIAL", 0),
            "failed": counts.get("FAILED", 0),
            "archived": int(archive_row["n"] if archive_row else 0),
            "remaining": sum(
                counts.get(status, 0)
                for status in ("NOT_FETCHED", "IN_PROGRESS", "FAILED")
            ),
        }

    def archive_key_exists(
        self,
        provider_match_id: str,
        schema_version: str = FOTMOB_HISTORICAL_SCHEMA_VERSION,
        provider: str = "FOTMOB",
    ) -> bool:
        return self.archive_entry(provider_match_id, schema_version, provider=provider) is not None

    def archive_entry(
        self,
        provider_match_id: str,
        schema_version: str = FOTMOB_HISTORICAL_SCHEMA_VERSION,
        *,
        provider: str = "FOTMOB",
    ) -> sqlite3.Row | None:
        with self._lock:
            return self.connection.execute(
                """
                SELECT * FROM fotmob_historical_archive_index
                WHERE provider = ? AND fotmob_match_id = ? AND schema_version = ?
                """,
                (provider.upper(), str(provider_match_id), schema_version),
            ).fetchone()

    def mark_archive_written(
        self,
        provider_match_id: str,
        archive_path: str,
        *,
        payload_hash: str | None = None,
        schema_version: str = FOTMOB_HISTORICAL_SCHEMA_VERSION,
        parser_version: str = FOTMOB_HISTORICAL_PARSER_VERSION,
        provider: str = "FOTMOB",
        source_type: str = "FRESH_FETCH",
        source_priority: int | None = None,
        source_context: str | None = None,
        stats_period: str | None = None,
        captured_live: bool = False,
        field_provenance: Mapping[str, Any] | None = None,
    ) -> None:
        normalized_source = str(source_type or "FRESH_FETCH").upper()
        if normalized_source not in FOTMOB_SOURCE_PRIORITY:
            normalized_source = "FRESH_FETCH"
        priority = (
            int(source_priority)
            if source_priority is not None
            else FOTMOB_SOURCE_PRIORITY[normalized_source]
        )
        with self._lock, self.connection:
            self.connection.execute(
                """
                INSERT INTO fotmob_historical_archive_index (
                    provider, fotmob_match_id, schema_version, parser_version,
                    archive_path, payload_hash, written_at, source_type, source_priority,
                    source_context, stats_period, captured_live, field_provenance_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider, fotmob_match_id, schema_version) DO UPDATE SET
                    parser_version = excluded.parser_version,
                    archive_path = excluded.archive_path,
                    payload_hash = excluded.payload_hash,
                    written_at = excluded.written_at,
                    source_type = excluded.source_type,
                    source_priority = excluded.source_priority,
                    source_context = excluded.source_context,
                    stats_period = excluded.stats_period,
                    captured_live = excluded.captured_live,
                    field_provenance_json = excluded.field_provenance_json
                """,
                (
                    provider.upper(), str(provider_match_id), schema_version, parser_version,
                    str(archive_path), payload_hash, _now(), normalized_source, priority,
                    source_context, stats_period, int(bool(captured_live)),
                    _json(field_provenance or {}),
                ),
            )

    def mark_imported(
        self,
        provider_match_id: str,
        *,
        data_quality: str,
        ml_eligible: bool,
        parser_version: str = FOTMOB_HISTORICAL_PARSER_VERSION,
        schema_version: str = FOTMOB_HISTORICAL_SCHEMA_VERSION,
        raw_payload_path: str | None = None,
        payload_hash: str | None = None,
        second_half_goals: int | None = None,
        second_half_goal_class: str | None = None,
        source_type: str = "LEGACY_IMPORT",
        source_context: str = "LEGACY_SQLITE",
        stats_period: str = "FIRST_HALF_AND_FULL_MATCH",
        captured_live: bool = False,
        field_provenance: Mapping[str, Any] | None = None,
    ) -> bool:
        """Mark a legacy row without downgrading an already fresh detail row."""

        normalized_source = str(source_type or "LEGACY_IMPORT").upper()
        if normalized_source not in FOTMOB_SOURCE_PRIORITY:
            normalized_source = "LEGACY_IMPORT"
        detail_status = "FETCHED" if data_quality in {"COMPLETE", "SCORE_ONLY"} else "PARTIAL"
        with self._lock, self.connection:
            existing = self.connection.execute(
                "SELECT source_type FROM fotmob_match_index WHERE fotmob_match_id = ?",
                (str(provider_match_id),),
            ).fetchone()
            existing_source = str(
                existing["source_type"] if existing and existing["source_type"] else "FRESH_INDEX"
            ).upper()
            if FOTMOB_SOURCE_PRIORITY.get(existing_source, 0) > FOTMOB_SOURCE_PRIORITY[normalized_source]:
                return False
            self.connection.execute(
                """
                UPDATE fotmob_match_index
                SET detail_status = ?, last_checked_at = ?, last_error = NULL,
                    data_quality = ?, ml_eligible = ?, parser_version = ?,
                    schema_version = ?, raw_payload_path = ?, payload_hash = ?,
                    second_half_goals = ?, second_half_goal_class = ?,
                    source_type = ?, source_context = ?, stats_period = ?,
                    captured_live = ?, field_provenance_json = ?
                WHERE fotmob_match_id = ?
                """,
                (
                    detail_status, _now(), data_quality, int(bool(ml_eligible)), parser_version,
                    schema_version, raw_payload_path, payload_hash, second_half_goals,
                    second_half_goal_class, normalized_source, source_context, stats_period,
                    int(bool(captured_live)), _json(field_provenance or {}), str(provider_match_id),
                ),
            )
        return True

    def save_raw_payload(
        self,
        payload: Mapping[str, Any],
        *,
        league_id: str,
        season_label: str,
        provider_match_id: str,
    ) -> tuple[str, str]:
        """Store one development payload as zstd JSON and return path/hash."""

        if self.raw_root is None:
            raise RuntimeError("raw_root is not configured")
        import zstandard as zstd

        encoded = _json(payload).encode("utf-8")
        payload_hash = hashlib.sha256(encoded).hexdigest()
        directory = (
            self.raw_root
            / "fotmob"
            / "historical"
            / f"league_id={_safe_path_part(league_id)}"
            / f"season={_safe_path_part(season_label)}"
        )
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / f"{_safe_path_part(provider_match_id)}-{payload_hash[:12]}.json.zst"
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_bytes(zstd.ZstdCompressor(level=3).compress(encoded))
        temporary.replace(destination)
        return str(destination), payload_hash


class FotMobHistoricalArchive:
    """Batch Parquet writer partitioned by league and season."""

    def __init__(self, archive_root: Path | str, compression: str = "zstd") -> None:
        self.root = Path(archive_root)
        self.snapshot_root = self.root / "fotmob" / "historical"
        self.compression = compression
        self._lock = threading.RLock()

    @staticmethod
    def _parquet_row(row: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(row)
        # These are Hive partition columns.  Keeping a second, differently
        # typed copy inside the file makes ``pyarrow.read_table(path)`` reject
        # the dataset (for example ``league_id=54`` is inferred as int while
        # the payload column is a string).  Readers recover them from the
        # partition path automatically.
        result.pop("league_id", None)
        result.pop("season_label", None)
        for key in ("ht_extra_stats_json", "ft_extra_stats_json", "timeline_json"):
            result[key] = _json(result.get(key, {} if "stats" in key else []))
        provenance = result.get("field_provenance_json", {})
        result["field_provenance_json"] = (
            provenance if isinstance(provenance, str) else _json(provenance or {})
        )
        result["ml_eligible"] = int(bool(result.get("ml_eligible")))
        result["captured_live"] = int(bool(result.get("captured_live")))
        return result

    @staticmethod
    def _partition_season(row: Mapping[str, Any]) -> str:
        return _safe_path_part(row.get("season_label") or row.get("season_id"), "unknown-season")

    def write_batch(self, store: FotMobHistoryStore, rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
        candidates = list(rows)
        if not candidates:
            return {"written": 0, "skipped": 0, "replaced": 0, "paths": []}

        def source_info(row: Mapping[str, Any]) -> tuple[str, int]:
            source = str(row.get("source_type", "FRESH_FETCH") or "FRESH_FETCH").upper()
            if source not in FOTMOB_SOURCE_PRIORITY:
                source = "FRESH_FETCH"
            value = row.get("source_priority")
            priority = int(value) if value is not None else FOTMOB_SOURCE_PRIORITY[source]
            return source, priority

        # A batch can contain an imported row and a fresh row for the same
        # provider key.  Keep only the highest-quality source before touching
        # Parquet, so a retry can never create a second training record.
        selected_by_key: dict[tuple[str, str, str], Mapping[str, Any]] = {}
        skipped = 0
        for row in candidates:
            provider = str(row.get("provider", "FOTMOB")).upper()
            match_id = str(row["fotmob_match_id"])
            schema_version = str(row.get("schema_version", FOTMOB_HISTORICAL_SCHEMA_VERSION))
            key = (provider, match_id, schema_version)
            if key not in selected_by_key:
                selected_by_key[key] = row
                continue
            _, previous_priority = source_info(selected_by_key[key])
            _, current_priority = source_info(row)
            if current_priority > previous_priority:
                selected_by_key[key] = row
            else:
                skipped += 1

        new_rows: list[Mapping[str, Any]] = []
        replacements: list[tuple[sqlite3.Row, Mapping[str, Any]]] = []
        for key, row in selected_by_key.items():
            provider, match_id, schema_version = key
            _, candidate_priority = source_info(row)
            existing = store.archive_entry(match_id, schema_version, provider=provider)
            if existing is not None:
                existing_priority = int(existing["source_priority"] or 0)
                if existing_priority >= candidate_priority:
                    skipped += 1
                    continue
                replacements.append((existing, row))
            new_rows.append(row)
        if not new_rows:
            return {"written": 0, "skipped": skipped, "replaced": 0, "paths": []}
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError as exc:
            raise RuntimeError("pyarrow is required for the historical Parquet archive") from exc

        paths: list[str] = []
        with self._lock:
            groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
            for row in new_rows:
                key = (str(row.get("league_id") or "unknown"), self._partition_season(row))
                groups.setdefault(key, []).append(self._parquet_row(row))
            for (league_id, season), group in groups.items():
                partition = self.snapshot_root / f"league_id={_safe_path_part(league_id)}" / f"season={season}"
                partition.mkdir(parents=True, exist_ok=True)
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
                destination = partition / f"fotmob-historical-{stamp}-{hashlib.sha256(stamp.encode()).hexdigest()[:8]}.parquet"
                temporary = destination.with_suffix(destination.suffix + ".tmp")
                pq.write_table(pa.Table.from_pylist(group), temporary, compression=self.compression)
                temporary.replace(destination)
                paths.append(str(destination))
                for row in group:
                    source_type, source_priority = source_info(row)
                    provenance = row.get("field_provenance_json")
                    if isinstance(provenance, str):
                        try:
                            provenance = json.loads(provenance)
                        except ValueError:
                            provenance = {"raw": provenance}
                    store.mark_archive_written(
                        str(row["fotmob_match_id"]),
                        str(destination),
                        payload_hash=row.get("payload_hash"),
                        schema_version=str(row.get("schema_version", FOTMOB_HISTORICAL_SCHEMA_VERSION)),
                        parser_version=str(row.get("parser_version", FOTMOB_HISTORICAL_PARSER_VERSION)),
                        provider=str(row.get("provider", "FOTMOB")),
                        source_type=source_type,
                        source_priority=source_priority,
                        source_context=row.get("source_context"),
                        stats_period=row.get("stats_period"),
                        captured_live=bool(row.get("captured_live")),
                        field_provenance=provenance if isinstance(provenance, Mapping) else None,
                    )

            # When a fresh detail supersedes a legacy row, remove the old row
            # from its physical file as well as replacing the active catalog
            # pointer.  The operation is limited to archive files previously
            # recorded in the catalog and never touches the source database.
            replaced = 0
            for existing, row in replacements:
                old_path = Path(str(existing["archive_path"]))
                new_path = next(
                    (
                        path for path in paths
                        if path != str(old_path)
                        and Path(path).parent == old_path.parent
                    ),
                    None,
                )
                if new_path is None or not old_path.exists():
                    continue
                try:
                    # ``old_path`` lives below Hive-style directories.  Read
                    # the physical file without re-attaching inferred
                    # partition columns; otherwise a rewrite can reintroduce
                    # a string/int ``league_id`` conflict.
                    table = pq.read_table(old_path, partitioning=None)
                    remaining = [
                        {
                            key: value
                            for key, value in item.items()
                            if key not in {"league_id", "season_label"}
                        }
                        for item in table.to_pylist()
                        if not (
                            str(item.get("provider", "FOTMOB")).upper()
                            == str(row.get("provider", "FOTMOB")).upper()
                            and str(item.get("fotmob_match_id")) == str(row["fotmob_match_id"])
                            and str(item.get("schema_version"))
                            == str(row.get("schema_version", FOTMOB_HISTORICAL_SCHEMA_VERSION))
                        )
                    ]
                    if remaining:
                        rewrite = old_path.with_suffix(old_path.suffix + ".rewrite.tmp")
                        pq.write_table(pa.Table.from_pylist(remaining), rewrite, compression=self.compression)
                        rewrite.replace(old_path)
                    else:
                        old_path.unlink(missing_ok=True)
                    replaced += 1
                except (OSError, ValueError, KeyError):
                    # The catalog remains authoritative if an old physical
                    # file was moved or is not readable anymore.
                    continue
        return {"written": len(new_rows), "skipped": skipped, "replaced": replaced, "paths": paths}
