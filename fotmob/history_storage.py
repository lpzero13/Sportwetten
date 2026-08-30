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
    FOTMOB_DETAIL_STATUSES,
    FOTMOB_HISTORICAL_PARSER_VERSION,
    FOTMOB_HISTORICAL_SCHEMA_VERSION,
    FotMobMatchIndexRecord,
    FotMobSeasonRef,
)


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
    second_half_goal_class TEXT
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
    PRIMARY KEY (provider, fotmob_match_id, schema_version)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
                self.connection.execute(
                    """
                    INSERT INTO fotmob_match_index (
                        fotmob_match_id, provider, league_id, season_id, season_label,
                        league_name, country, kickoff_at, home_team_id, home_team_name,
                        away_team_id, away_team_name, round, match_status,
                        first_seen_at, last_seen_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(fotmob_match_id) DO UPDATE SET
                        provider = excluded.provider,
                        league_id = excluded.league_id,
                        season_id = excluded.season_id,
                        season_label = excluded.season_label,
                        league_name = COALESCE(excluded.league_name, fotmob_match_index.league_name),
                        country = COALESCE(excluded.country, fotmob_match_index.country),
                        kickoff_at = COALESCE(excluded.kickoff_at, fotmob_match_index.kickoff_at),
                        home_team_id = COALESCE(excluded.home_team_id, fotmob_match_index.home_team_id),
                        home_team_name = excluded.home_team_name,
                        away_team_id = COALESCE(excluded.away_team_id, fotmob_match_index.away_team_id),
                        away_team_name = excluded.away_team_name,
                        round = COALESCE(excluded.round, fotmob_match_index.round),
                        match_status = COALESCE(excluded.match_status, fotmob_match_index.match_status),
                        last_seen_at = excluded.last_seen_at
                    """,
                    (
                        record.provider_match_id,
                        record.provider.upper(),
                        record.league_id,
                        record.season_id,
                        record.season_label,
                        record.league_name,
                        record.country,
                        record.kickoff_at,
                        record.home_team_id,
                        record.home_team_name,
                        record.away_team_id,
                        record.away_team_name,
                        record.round_name,
                        record.match_status,
                        seen_at,
                        seen_at,
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
    ) -> str:
        detail_status = "FETCHED" if data_quality == "COMPLETE" else "PARTIAL"
        with self._lock, self.connection:
            self.connection.execute(
                """
                UPDATE fotmob_match_index
                SET detail_status = ?, last_checked_at = ?, last_error = NULL,
                    worker_id = NULL, data_quality = ?, ml_eligible = ?,
                    parser_version = ?, schema_version = ?, raw_payload_path = ?,
                    payload_hash = ?, second_half_goals = ?, second_half_goal_class = ?
                WHERE fotmob_match_id = ?
                  AND (? IS NULL OR worker_id = ?)
                """,
                (
                    detail_status, _now(), data_quality, int(ml_eligible), parser_version,
                    schema_version, raw_payload_path, payload_hash, second_half_goals,
                    second_half_goal_class, str(provider_match_id), worker_id, worker_id,
                ),
            )
        return detail_status

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
        with self._lock:
            return self.connection.execute(
                """
                SELECT 1 FROM fotmob_historical_archive_index
                WHERE provider = ? AND fotmob_match_id = ? AND schema_version = ?
                """,
                (provider.upper(), str(provider_match_id), schema_version),
            ).fetchone() is not None

    def mark_archive_written(
        self,
        provider_match_id: str,
        archive_path: str,
        *,
        payload_hash: str | None = None,
        schema_version: str = FOTMOB_HISTORICAL_SCHEMA_VERSION,
        parser_version: str = FOTMOB_HISTORICAL_PARSER_VERSION,
        provider: str = "FOTMOB",
    ) -> None:
        with self._lock, self.connection:
            self.connection.execute(
                """
                INSERT INTO fotmob_historical_archive_index (
                    provider, fotmob_match_id, schema_version, parser_version,
                    archive_path, payload_hash, written_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider, fotmob_match_id, schema_version) DO UPDATE SET
                    parser_version = excluded.parser_version,
                    archive_path = excluded.archive_path,
                    payload_hash = excluded.payload_hash,
                    written_at = excluded.written_at
                """,
                (
                    provider.upper(), str(provider_match_id), schema_version, parser_version,
                    str(archive_path), payload_hash, _now(),
                ),
            )

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
        for key in ("ht_extra_stats_json", "ft_extra_stats_json", "timeline_json"):
            result[key] = _json(result.get(key, {} if "stats" in key else []))
        result["ml_eligible"] = int(bool(result.get("ml_eligible")))
        return result

    @staticmethod
    def _partition_season(row: Mapping[str, Any]) -> str:
        return _safe_path_part(row.get("season_label") or row.get("season_id"), "unknown-season")

    def write_batch(self, store: FotMobHistoryStore, rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
        candidates = list(rows)
        if not candidates:
            return {"written": 0, "skipped": 0, "paths": []}
        new_rows: list[Mapping[str, Any]] = []
        seen_keys: set[tuple[str, str, str]] = set()
        for row in candidates:
            provider = str(row.get("provider", "FOTMOB")).upper()
            match_id = str(row["fotmob_match_id"])
            schema_version = str(row.get("schema_version", FOTMOB_HISTORICAL_SCHEMA_VERSION))
            key = (provider, match_id, schema_version)
            if key in seen_keys or store.archive_key_exists(match_id, schema_version, provider=provider):
                continue
            seen_keys.add(key)
            new_rows.append(row)
        skipped = len(candidates) - len(new_rows)
        if not new_rows:
            return {"written": 0, "skipped": skipped, "paths": []}
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
                    store.mark_archive_written(
                        str(row["fotmob_match_id"]),
                        str(destination),
                        payload_hash=row.get("payload_hash"),
                        schema_version=str(row.get("schema_version", FOTMOB_HISTORICAL_SCHEMA_VERSION)),
                        parser_version=str(row.get("parser_version", FOTMOB_HISTORICAL_PARSER_VERSION)),
                        provider=str(row.get("provider", "FOTMOB")),
                    )
        return {"written": len(new_rows), "skipped": skipped, "paths": paths}
