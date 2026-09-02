"""Lean FotMob persistence on the existing SQLite database and Parquet root."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from collections.abc import Iterable, Mapping
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import (
    FOTMOB_SCHEMA_VERSION,
    FOTMOB_SNAPSHOT_TYPES,
    FotMobMatch,
    FotMobSnapshot,
    FotMobStats,
)


FOTMOB_SCHEMA = """
CREATE TABLE IF NOT EXISTS matches (
    internal_match_id TEXT PRIMARY KEY,
    tipico_event_id TEXT UNIQUE,
    kickoff_at TEXT,
    competition_id TEXT,
    competition_name TEXT,
    competition_country TEXT,
    home_team_id TEXT,
    home_team TEXT NOT NULL,
    away_team_id TEXT,
    away_team TEXT NOT NULL,
    status TEXT,
    score_home INTEGER,
    score_away INTEGER,
    ht_score_home INTEGER,
    ht_score_away INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_matches_kickoff ON matches(kickoff_at);

CREATE TABLE IF NOT EXISTS match_provider_links (
    internal_match_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    provider_match_id TEXT NOT NULL,
    match_confidence REAL NOT NULL DEFAULT 0,
    match_status TEXT NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL,
    verified_at TEXT,
    PRIMARY KEY (internal_match_id, provider),
    UNIQUE (provider, provider_match_id)
);

CREATE INDEX IF NOT EXISTS idx_match_provider_links_status
    ON match_provider_links(provider, match_status, match_confidence);

CREATE TABLE IF NOT EXISTS teams (
    team_id TEXT PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    country TEXT,
    gender TEXT,
    age_group TEXT,
    reserve_flag INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS team_provider_aliases (
    team_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    provider_team_id TEXT,
    provider_name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    verified INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    verified_at TEXT,
    PRIMARY KEY (team_id, provider, normalized_name)
);

CREATE INDEX IF NOT EXISTS idx_team_alias_lookup
    ON team_provider_aliases(provider, normalized_name);

CREATE TABLE IF NOT EXISTS competition_provider_aliases (
    competition_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    provider_competition_id TEXT,
    provider_name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    country TEXT,
    verified INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    verified_at TEXT,
    PRIMARY KEY (competition_id, provider, normalized_name)
);

CREATE INDEX IF NOT EXISTS idx_competition_alias_lookup
    ON competition_provider_aliases(provider, normalized_name);

CREATE TABLE IF NOT EXISTS competition_provider_links (
    internal_competition_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    provider_competition_id TEXT NOT NULL,
    tipico_competition_name TEXT NOT NULL,
    tipico_country TEXT,
    provider_competition_name TEXT,
    provider_country TEXT,
    confidence REAL NOT NULL DEFAULT 1,
    match_status TEXT NOT NULL DEFAULT 'MANUALLY_CONFIRMED',
    source TEXT,
    created_at TEXT NOT NULL,
    verified_at TEXT,
    PRIMARY KEY (internal_competition_id, provider)
);

CREATE INDEX IF NOT EXISTS idx_competition_provider_links_lookup
    ON competition_provider_links(provider, provider_competition_id, tipico_country);

CREATE TABLE IF NOT EXISTS provider_event_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL DEFAULT 'FOTMOB',
    tipico_event_id TEXT NOT NULL,
    fotmob_match_id TEXT,
    tipico_competition_id TEXT,
    fotmob_league_id TEXT,
    tipico_home_team TEXT,
    tipico_away_team TEXT,
    fotmob_home_team TEXT,
    fotmob_away_team TEXT,
    tipico_kickoff TEXT,
    fotmob_kickoff TEXT,
    match_confidence REAL NOT NULL DEFAULT 0,
    match_method TEXT NOT NULL,
    match_status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_verified_at TEXT,
    reason TEXT,
    UNIQUE (provider, tipico_event_id)
);

CREATE INDEX IF NOT EXISTS idx_provider_event_links_lookup
    ON provider_event_links(provider, tipico_event_id, match_status);

CREATE INDEX IF NOT EXISTS idx_provider_event_links_provider_match
    ON provider_event_links(provider, fotmob_match_id);

CREATE TABLE IF NOT EXISTS fotmob_current_state (
    internal_match_id TEXT PRIMARY KEY,
    provider_match_id TEXT NOT NULL UNIQUE,
    observed_at TEXT NOT NULL,
    status TEXT,
    period TEXT,
    minute INTEGER,
    added_time INTEGER,
    score_home INTEGER,
    score_away INTEGER,
    ht_score_home INTEGER,
    ht_score_away INTEGER,
    xg_home REAL,
    xg_away REAL,
    shots_home REAL,
    shots_away REAL,
    shots_on_target_home REAL,
    shots_on_target_away REAL,
    big_chances_home REAL,
    big_chances_away REAL,
    corners_home REAL,
    corners_away REAL,
    possession_home REAL,
    possession_away REAL,
    yellow_cards_home REAL,
    yellow_cards_away REAL,
    red_cards_home REAL,
    red_cards_away REAL,
    stats_json TEXT NOT NULL DEFAULT '{}',
    ht_stats_json TEXT,
    extra_stats_json TEXT NOT NULL DEFAULT '{}',
    events_json TEXT NOT NULL DEFAULT '[]',
    raw_data_json TEXT NOT NULL DEFAULT '{}',
    raw_payload_path TEXT,
    payload_hash TEXT,
    result_consistency TEXT,
    ht_consistency TEXT,
    quality TEXT,
    provider TEXT NOT NULL DEFAULT 'FOTMOB',
    stats_period TEXT,
    source_context TEXT,
    captured_live INTEGER NOT NULL DEFAULT 0,
    tipico_event_id TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fotmob_snapshots (
    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    internal_match_id TEXT NOT NULL,
    provider_match_id TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    snapshot_type TEXT NOT NULL,
    status TEXT,
    period TEXT,
    minute INTEGER,
    added_time INTEGER,
    score_home INTEGER,
    score_away INTEGER,
    ht_score_home INTEGER,
    ht_score_away INTEGER,
    xg_home REAL,
    xg_away REAL,
    shots_home REAL,
    shots_away REAL,
    shots_on_target_home REAL,
    shots_on_target_away REAL,
    big_chances_home REAL,
    big_chances_away REAL,
    corners_home REAL,
    corners_away REAL,
    possession_home REAL,
    possession_away REAL,
    yellow_cards_home REAL,
    yellow_cards_away REAL,
    red_cards_home REAL,
    red_cards_away REAL,
    stats_json TEXT NOT NULL DEFAULT '{}',
    ht_stats_json TEXT,
    extra_stats_json TEXT NOT NULL DEFAULT '{}',
    events_json TEXT NOT NULL DEFAULT '[]',
    raw_payload_path TEXT,
    result_consistency TEXT,
    ht_consistency TEXT,
    snapshot_quality TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    archive_path TEXT,
    exported_at TEXT,
    payload_hash TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT 'FOTMOB',
    stats_period TEXT,
    source_context TEXT,
    captured_live INTEGER NOT NULL DEFAULT 0,
    tipico_event_id TEXT,
    UNIQUE (internal_match_id, snapshot_type)
);

CREATE INDEX IF NOT EXISTS idx_fotmob_snapshots_match_time
    ON fotmob_snapshots(internal_match_id, captured_at, snapshot_id);

CREATE TABLE IF NOT EXISTS fotmob_snapshot_outbox (
    snapshot_id INTEGER PRIMARY KEY,
    internal_match_id TEXT NOT NULL,
    snapshot_type TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    exported INTEGER NOT NULL DEFAULT 0,
    exported_at TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (internal_match_id, snapshot_type)
);

CREATE INDEX IF NOT EXISTS idx_fotmob_outbox_pending
    ON fotmob_snapshot_outbox(exported, captured_at, snapshot_id);

CREATE TABLE IF NOT EXISTS match_data_quality (
    internal_match_id TEXT PRIMARY KEY,
    fotmob_matched INTEGER NOT NULL DEFAULT 0,
    fotmob_ht_available INTEGER NOT NULL DEFAULT 0,
    fotmob_ht_stats_available INTEGER NOT NULL DEFAULT 0,
    tipico_ht_available INTEGER NOT NULL DEFAULT 0,
    result_consistency TEXT,
    ht_consistency TEXT,
    fotmob_result_status TEXT,
    quality_flags_json TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL
);
"""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def internal_match_id_for_tipico(event_id: str) -> str:
    digest = hashlib.sha256(f"TIPICO:{event_id}".encode("utf-8")).hexdigest()[:24]
    return f"match_{digest}"


def _hash_payload(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _stats_values(stats: FotMobStats) -> list[Any]:
    return [
        stats.xg_home, stats.xg_away, stats.shots_home, stats.shots_away,
        stats.shots_on_target_home, stats.shots_on_target_away,
        stats.big_chances_home, stats.big_chances_away,
        stats.corners_home, stats.corners_away,
        stats.possession_home, stats.possession_away,
        stats.yellow_cards_home, stats.yellow_cards_away,
        stats.red_cards_home, stats.red_cards_away,
    ]


class FotMobStore:
    """Repository using the existing ``Database`` connection and no new DB."""

    def __init__(self, database: Any, archive_root: Path | str) -> None:
        self.database = database
        self.archive_root = Path(archive_root)
        self.archive_root.mkdir(parents=True, exist_ok=True)
        self._lock = getattr(database, "_lock", threading.RLock())
        with self._lock, database.connection:
            database.connection.executescript(FOTMOB_SCHEMA)
            for table, columns in {
                "fotmob_current_state": {
                    "provider": "TEXT NOT NULL DEFAULT 'FOTMOB'",
                    "stats_period": "TEXT",
                    "source_context": "TEXT",
                    "captured_live": "INTEGER NOT NULL DEFAULT 0",
                    "tipico_event_id": "TEXT",
                },
                "fotmob_snapshots": {
                    "provider": "TEXT NOT NULL DEFAULT 'FOTMOB'",
                    "stats_period": "TEXT",
                    "source_context": "TEXT",
                    "captured_live": "INTEGER NOT NULL DEFAULT 0",
                    "tipico_event_id": "TEXT",
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
        # Migrate the V0.5.3 relation into the richer additive link layer.
        # Nothing is deleted and the legacy table remains available to older
        # integrations.
        self.sync_legacy_provider_event_links()

    def _connection(self) -> sqlite3.Connection:
        return self.database.connection

    def upsert_tipico_event(self, event: Any, *, observed_at: str | None = None) -> str:
        internal_id = internal_match_id_for_tipico(str(event.event_id))
        now = observed_at or _now()
        values = (
            internal_id,
            str(event.event_id),
            getattr(event, "kickoff_time", None),
            getattr(event, "competition_id", None),
            getattr(event, "competition_name", None),
            getattr(event, "competition_country", None),
            getattr(event, "home_team_id", None),
            getattr(event, "home_team", ""),
            getattr(event, "away_team_id", None),
            getattr(event, "away_team", ""),
            getattr(event, "status", None),
            getattr(event, "score_home", None),
            getattr(event, "score_away", None),
            getattr(event, "ht_score_home", None),
            getattr(event, "ht_score_away", None),
            now,
            now,
        )
        with self._lock, self._connection():
            self._connection().execute(
                """
                INSERT INTO matches (
                    internal_match_id, tipico_event_id, kickoff_at, competition_id,
                    competition_name, competition_country, home_team_id, home_team,
                    away_team_id, away_team, status, score_home, score_away,
                    ht_score_home, ht_score_away, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(internal_match_id) DO UPDATE SET
                    kickoff_at = excluded.kickoff_at,
                    competition_id = excluded.competition_id,
                    competition_name = excluded.competition_name,
                    competition_country = COALESCE(excluded.competition_country, matches.competition_country),
                    home_team_id = excluded.home_team_id,
                    home_team = excluded.home_team,
                    away_team_id = excluded.away_team_id,
                    away_team = excluded.away_team,
                    status = excluded.status,
                    score_home = excluded.score_home,
                    score_away = excluded.score_away,
                    ht_score_home = excluded.ht_score_home,
                    ht_score_away = excluded.ht_score_away,
                    updated_at = excluded.updated_at
                """,
                values,
            )
        return internal_id

    def match_row(self, internal_match_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self._connection().execute(
                "SELECT * FROM matches WHERE internal_match_id = ?", (internal_match_id,)
            ).fetchone()

    def match_row_for_tipico_event(self, event_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self._connection().execute(
                "SELECT * FROM matches WHERE tipico_event_id = ?", (str(event_id),)
            ).fetchone()

    @staticmethod
    def _canonical_link_status(status: Any) -> str:
        value = str(status or "UNMATCHED").strip().upper()
        return {
            "MANUALLY_CONFIRMED": "MANUAL",
            "REJECTED": "INVALIDATED",
        }.get(value, value)

    def upsert_provider_event_link(
        self,
        *,
        tipico_event_id: str,
        fotmob_match_id: str | None,
        tipico_competition_id: str | None = None,
        fotmob_league_id: str | None = None,
        tipico_home_team: str | None = None,
        tipico_away_team: str | None = None,
        fotmob_home_team: str | None = None,
        fotmob_away_team: str | None = None,
        tipico_kickoff: str | None = None,
        fotmob_kickoff: str | None = None,
        match_confidence: float = 0.0,
        match_method: str = "UNKNOWN",
        match_status: str = "UNMATCHED",
        reason: str | None = None,
        last_verified_at: str | None = None,
        provider: str = "FOTMOB",
    ) -> None:
        """Persist one explainable Tipico/provider relation.

        ``fotmob_match_id`` remains nullable for AMBIGUOUS/UNMATCHED rows;
        retaining those decisions prevents the resolver from inventing a
        provider request just because no positive link exists yet.
        """

        now = _now()
        provider_name = str(provider).upper()
        status = self._canonical_link_status(match_status)
        provider_match_id = (
            str(fotmob_match_id) if fotmob_match_id not in (None, "") else None
        )
        with self._lock, self._connection():
            self._connection().execute(
                """
                INSERT INTO provider_event_links (
                    provider, tipico_event_id, fotmob_match_id,
                    tipico_competition_id, fotmob_league_id,
                    tipico_home_team, tipico_away_team,
                    fotmob_home_team, fotmob_away_team,
                    tipico_kickoff, fotmob_kickoff, match_confidence,
                    match_method, match_status, created_at, updated_at,
                    last_verified_at, reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider, tipico_event_id) DO UPDATE SET
                    fotmob_match_id = excluded.fotmob_match_id,
                    tipico_competition_id = COALESCE(
                        excluded.tipico_competition_id,
                        provider_event_links.tipico_competition_id
                    ),
                    fotmob_league_id = COALESCE(
                        excluded.fotmob_league_id,
                        provider_event_links.fotmob_league_id
                    ),
                    tipico_home_team = COALESCE(
                        excluded.tipico_home_team,
                        provider_event_links.tipico_home_team
                    ),
                    tipico_away_team = COALESCE(
                        excluded.tipico_away_team,
                        provider_event_links.tipico_away_team
                    ),
                    fotmob_home_team = COALESCE(
                        excluded.fotmob_home_team,
                        provider_event_links.fotmob_home_team
                    ),
                    fotmob_away_team = COALESCE(
                        excluded.fotmob_away_team,
                        provider_event_links.fotmob_away_team
                    ),
                    tipico_kickoff = COALESCE(
                        excluded.tipico_kickoff,
                        provider_event_links.tipico_kickoff
                    ),
                    fotmob_kickoff = COALESCE(
                        excluded.fotmob_kickoff,
                        provider_event_links.fotmob_kickoff
                    ),
                    match_confidence = excluded.match_confidence,
                    match_method = excluded.match_method,
                    match_status = excluded.match_status,
                    updated_at = excluded.updated_at,
                    last_verified_at = COALESCE(
                        excluded.last_verified_at,
                        provider_event_links.last_verified_at
                    ),
                    reason = excluded.reason
                """,
                (
                    provider_name,
                    str(tipico_event_id),
                    provider_match_id,
                    tipico_competition_id,
                    fotmob_league_id,
                    tipico_home_team,
                    tipico_away_team,
                    fotmob_home_team,
                    fotmob_away_team,
                    tipico_kickoff,
                    fotmob_kickoff,
                    max(0.0, min(1.0, float(match_confidence))),
                    str(match_method or "UNKNOWN"),
                    status,
                    now,
                    now,
                    last_verified_at,
                    reason,
                ),
            )

    def provider_event_link_for_tipico_event(
        self,
        tipico_event_id: str,
        provider: str = "FOTMOB",
    ) -> sqlite3.Row | None:
        with self._lock:
            return self._connection().execute(
                """
                SELECT * FROM provider_event_links
                WHERE provider = ? AND tipico_event_id = ?
                """,
                (str(provider).upper(), str(tipico_event_id)),
            ).fetchone()

    def provider_event_links(
        self,
        provider: str = "FOTMOB",
        *,
        match_status: str | None = None,
        limit: int = 500,
    ) -> list[sqlite3.Row]:
        clauses = ["provider = ?"]
        params: list[Any] = [str(provider).upper()]
        if match_status:
            clauses.append("match_status = ?")
            params.append(self._canonical_link_status(match_status))
        params.append(max(1, int(limit)))
        with self._lock:
            return list(self._connection().execute(
                f"""
                SELECT * FROM provider_event_links
                WHERE {' AND '.join(clauses)}
                ORDER BY updated_at DESC, id DESC
                LIMIT ?
                """,
                params,
            ).fetchall())

    def sync_legacy_provider_event_links(self) -> int:
        """Backfill the new link view from already stored V0.5.3 links."""

        with self._lock:
            rows = self._connection().execute(
                """
                SELECT l.provider, l.provider_match_id, l.match_confidence,
                       l.match_status, l.reason, l.created_at, l.verified_at,
                       m.tipico_event_id, m.competition_id,
                       m.home_team, m.away_team, m.kickoff_at
                FROM match_provider_links l
                JOIN matches m ON m.internal_match_id = l.internal_match_id
                WHERE l.provider = 'FOTMOB' AND m.tipico_event_id IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM provider_event_links p
                      WHERE p.provider = 'FOTMOB'
                        AND p.tipico_event_id = m.tipico_event_id
                  )
                """
            ).fetchall()
        for row in rows:
            self.upsert_provider_event_link(
                tipico_event_id=str(row["tipico_event_id"]),
                fotmob_match_id=row["provider_match_id"],
                tipico_competition_id=row["competition_id"],
                tipico_home_team=row["home_team"],
                tipico_away_team=row["away_team"],
                tipico_kickoff=row["kickoff_at"],
                match_confidence=float(row["match_confidence"] or 0.0),
                match_method="LEGACY_LINK_MIGRATION",
                match_status=str(row["match_status"] or "UNMATCHED"),
                reason=row["reason"],
                last_verified_at=row["verified_at"],
                provider=str(row["provider"]),
            )
        return len(rows)

    def upsert_fotmob_match(self, internal_match_id: str, match: FotMobMatch, *, observed_at: str | None = None) -> None:
        """Refresh provider metadata without replacing Tipico's identity."""

        now = observed_at or _now()
        with self._lock, self._connection():
            self._connection().execute(
                """
                UPDATE matches SET
                    kickoff_at = COALESCE(kickoff_at, ?),
                    competition_id = COALESCE(competition_id, ?),
                    competition_name = COALESCE(competition_name, ?),
                    competition_country = COALESCE(competition_country, ?),
                    updated_at = ?
                WHERE internal_match_id = ?
                """,
                (
                    match.kickoff_at, match.competition_id, match.competition_name,
                    match.competition_country, now, internal_match_id,
                ),
            )

    def upsert_link(
        self,
        *,
        internal_match_id: str,
        provider: str,
        provider_match_id: str,
        confidence: float,
        status: str,
        reason: str | None = None,
        verified_at: str | None = None,
    ) -> None:
        now = _now()
        with self._lock, self._connection():
            self._connection().execute(
                """
                INSERT INTO match_provider_links (
                    internal_match_id, provider, provider_match_id,
                    match_confidence, match_status, reason, created_at, verified_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(internal_match_id, provider) DO UPDATE SET
                    provider_match_id = excluded.provider_match_id,
                    match_confidence = excluded.match_confidence,
                    match_status = excluded.match_status,
                    reason = excluded.reason,
                    verified_at = COALESCE(excluded.verified_at, match_provider_links.verified_at)
                """,
                (
                    internal_match_id, provider.upper(), str(provider_match_id),
                    max(0.0, min(1.0, float(confidence))), status, reason, now, verified_at,
                ),
            )

    def link_for_internal(self, internal_match_id: str, provider: str = "FOTMOB") -> sqlite3.Row | None:
        with self._lock:
            return self._connection().execute(
                """
                SELECT * FROM match_provider_links
                WHERE internal_match_id = ? AND provider = ?
                """,
                (internal_match_id, provider.upper()),
            ).fetchone()

    def links(self, provider: str = "FOTMOB", limit: int = 500) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._connection().execute(
                """
                SELECT l.*, m.tipico_event_id, m.home_team, m.away_team,
                       m.competition_name, m.competition_country, m.kickoff_at
                FROM match_provider_links l
                LEFT JOIN matches m ON m.internal_match_id = l.internal_match_id
                WHERE l.provider = ?
                ORDER BY l.created_at DESC
                LIMIT ?
                """,
                (provider.upper(), max(1, int(limit))),
            ).fetchall())

    def upsert_team(
        self,
        *,
        team_id: str,
        canonical_name: str,
        country: str | None = None,
        gender: str | None = None,
        age_group: str | None = None,
        reserve_flag: bool = False,
    ) -> None:
        now = _now()
        with self._lock, self._connection():
            self._connection().execute(
                """
                INSERT INTO teams (
                    team_id, canonical_name, country, gender, age_group,
                    reserve_flag, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(team_id) DO UPDATE SET
                    canonical_name = excluded.canonical_name,
                    country = COALESCE(excluded.country, teams.country),
                    gender = COALESCE(excluded.gender, teams.gender),
                    age_group = COALESCE(excluded.age_group, teams.age_group),
                    reserve_flag = excluded.reserve_flag,
                    updated_at = excluded.updated_at
                """,
                (team_id, canonical_name, country, gender, age_group, int(reserve_flag), now, now),
            )

    def upsert_team_alias(
        self,
        *,
        team_id: str,
        provider: str,
        provider_name: str,
        normalized_name: str,
        provider_team_id: str | None = None,
        verified: bool = False,
    ) -> None:
        now = _now()
        with self._lock, self._connection():
            self._connection().execute(
                """
                INSERT INTO team_provider_aliases (
                    team_id, provider, provider_team_id, provider_name,
                    normalized_name, verified, created_at, verified_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(team_id, provider, normalized_name) DO UPDATE SET
                    provider_team_id = COALESCE(excluded.provider_team_id, team_provider_aliases.provider_team_id),
                    provider_name = excluded.provider_name,
                    verified = MAX(team_provider_aliases.verified, excluded.verified),
                    verified_at = COALESCE(excluded.verified_at, team_provider_aliases.verified_at)
                """,
                (
                    team_id, provider.upper(), provider_team_id, provider_name,
                    normalized_name, int(verified), now, now if verified else None,
                ),
            )

    def team_aliases(self, provider: str = "FOTMOB") -> list[sqlite3.Row]:
        with self._lock:
            return list(self._connection().execute(
                "SELECT * FROM team_provider_aliases WHERE provider = ? ORDER BY normalized_name",
                (provider.upper(),),
            ).fetchall())

    def team_alias_map(self, provider: str | None = "FOTMOB") -> dict[str, str]:
        with self._lock:
            where = "WHERE a.provider = ?" if provider else ""
            params = (provider.upper(),) if provider else ()
            rows = self._connection().execute(
                f"""
                SELECT a.normalized_name, t.canonical_name
                FROM team_provider_aliases a
                JOIN teams t ON t.team_id = a.team_id
                {where}
                """,
                params,
            ).fetchall()
        return {str(row["normalized_name"]): str(row["canonical_name"]) for row in rows}

    def upsert_competition_alias(
        self,
        *,
        competition_id: str,
        provider: str,
        provider_name: str,
        normalized_name: str,
        provider_competition_id: str | None = None,
        country: str | None = None,
        verified: bool = False,
    ) -> None:
        now = _now()
        with self._lock, self._connection():
            self._connection().execute(
                """
                INSERT INTO competition_provider_aliases (
                    competition_id, provider, provider_competition_id, provider_name,
                    normalized_name, country, verified, created_at, verified_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(competition_id, provider, normalized_name) DO UPDATE SET
                    provider_competition_id = COALESCE(excluded.provider_competition_id, competition_provider_aliases.provider_competition_id),
                    provider_name = excluded.provider_name,
                    country = COALESCE(excluded.country, competition_provider_aliases.country),
                    verified = MAX(competition_provider_aliases.verified, excluded.verified),
                    verified_at = COALESCE(excluded.verified_at, competition_provider_aliases.verified_at)
                """,
                (
                    competition_id, provider.upper(), provider_competition_id,
                    provider_name, normalized_name, country, int(verified), now,
                    now if verified else None,
                ),
            )

    def competition_alias_map(self, provider: str | None = "FOTMOB") -> dict[str, str]:
        with self._lock:
            where = "WHERE provider = ?" if provider else ""
            params = (provider.upper(),) if provider else ()
            rows = self._connection().execute(
                f"""
                SELECT normalized_name, competition_id
                FROM competition_provider_aliases
                {where}
                """,
                params,
            ).fetchall()
        return {str(row["normalized_name"]): str(row["competition_id"]) for row in rows}

    def upsert_competition_provider_link(
        self,
        *,
        internal_competition_id: str,
        provider: str,
        provider_competition_id: str,
        tipico_competition_name: str,
        tipico_country: str | None = None,
        provider_competition_name: str | None = None,
        provider_country: str | None = None,
        confidence: float = 1.0,
        match_status: str = "MANUALLY_CONFIRMED",
        source: str | None = None,
        verified_at: str | None = None,
    ) -> None:
        now = _now()
        with self._lock, self._connection():
            self._connection().execute(
                """
                INSERT INTO competition_provider_links (
                    internal_competition_id, provider, provider_competition_id,
                    tipico_competition_name, tipico_country, provider_competition_name,
                    provider_country, confidence, match_status, source, created_at, verified_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(internal_competition_id, provider) DO UPDATE SET
                    provider_competition_id = excluded.provider_competition_id,
                    tipico_competition_name = excluded.tipico_competition_name,
                    tipico_country = COALESCE(excluded.tipico_country, competition_provider_links.tipico_country),
                    provider_competition_name = COALESCE(excluded.provider_competition_name, competition_provider_links.provider_competition_name),
                    provider_country = COALESCE(excluded.provider_country, competition_provider_links.provider_country),
                    confidence = excluded.confidence,
                    match_status = excluded.match_status,
                    source = COALESCE(excluded.source, competition_provider_links.source),
                    verified_at = COALESCE(excluded.verified_at, competition_provider_links.verified_at)
                """,
                (
                    str(internal_competition_id), provider.upper(), str(provider_competition_id),
                    str(tipico_competition_name), tipico_country, provider_competition_name,
                    provider_country, max(0.0, min(1.0, float(confidence))), match_status,
                    source, now, verified_at,
                ),
            )

    def competition_link_for_internal(
        self,
        internal_competition_id: str,
        provider: str = "FOTMOB",
    ) -> sqlite3.Row | None:
        with self._lock:
            return self._connection().execute(
                """
                SELECT * FROM competition_provider_links
                WHERE internal_competition_id = ? AND provider = ?
                """,
                (str(internal_competition_id), provider.upper()),
            ).fetchone()

    def competition_links(self, provider: str | None = "FOTMOB") -> list[sqlite3.Row]:
        with self._lock:
            if provider is None:
                return list(self._connection().execute(
                    "SELECT * FROM competition_provider_links ORDER BY internal_competition_id"
                ).fetchall())
            return list(self._connection().execute(
                """
                SELECT * FROM competition_provider_links
                WHERE provider = ?
                ORDER BY internal_competition_id
                """,
                (provider.upper(),),
            ).fetchall())

    def _state_values(self, match: FotMobMatch) -> list[Any]:
        return [
            match.provider_match_id, match.status, match.period, match.minute,
            match.added_time, match.score_home, match.score_away,
            match.ht_score_home, match.ht_score_away, *_stats_values(match.stats),
            _json(match.stats.to_dict()),
            _json(match.ht_stats.to_dict()) if match.ht_stats else None,
            _json(match.stats.extra_stats), _json([item.to_dict() for item in match.events]),
            _json(match.raw_data), _hash_payload(match.raw_data),
        ]

    def upsert_current_state(
        self,
        *,
        internal_match_id: str,
        match: FotMobMatch,
        observed_at: str,
        result_consistency: str | None = None,
        ht_consistency: str | None = None,
        quality: str | None = None,
        raw_payload_path: str | None = None,
        provider: str = "FOTMOB",
        stats_period: str | None = "FULL_MATCH",
        source_context: str | None = None,
        captured_live: bool = False,
        tipico_event_id: str | None = None,
    ) -> None:
        state_values = self._state_values(match)
        values = [internal_match_id, state_values[0], observed_at, *state_values[1:]]
        values.extend([
            raw_payload_path, result_consistency, ht_consistency, quality,
            provider.upper(), stats_period, source_context, int(bool(captured_live)),
            tipico_event_id, observed_at,
        ])
        columns = (
            "internal_match_id", "provider_match_id", "observed_at", "status", "period",
            "minute", "added_time", "score_home", "score_away", "ht_score_home", "ht_score_away",
            "xg_home", "xg_away", "shots_home", "shots_away", "shots_on_target_home",
            "shots_on_target_away", "big_chances_home", "big_chances_away", "corners_home",
            "corners_away", "possession_home", "possession_away", "yellow_cards_home",
            "yellow_cards_away", "red_cards_home", "red_cards_away", "stats_json",
            "ht_stats_json", "extra_stats_json", "events_json", "raw_data_json",
            "payload_hash", "raw_payload_path", "result_consistency", "ht_consistency",
            "quality", "provider", "stats_period", "source_context", "captured_live",
            "tipico_event_id", "updated_at",
        )
        updates = ", ".join(
            f"{column} = excluded.{column}" for column in columns[1:]
        )
        with self._lock, self._connection():
            self._connection().execute(
                f"""
                INSERT INTO fotmob_current_state ({', '.join(columns)})
                VALUES ({', '.join('?' for _ in columns)})
                ON CONFLICT(internal_match_id) DO UPDATE SET {updates}
                """,
                values,
            )

    def current_state(self, internal_match_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self._connection().execute(
                "SELECT * FROM fotmob_current_state WHERE internal_match_id = ?",
                (internal_match_id,),
            ).fetchone()

    def _snapshot_payload(self, snapshot: FotMobSnapshot) -> dict[str, Any]:
        return {
            "schema_version": snapshot.schema_version,
            "internal_match_id": snapshot.internal_match_id,
            "snapshot_type": snapshot.snapshot_type,
            "captured_at": snapshot.captured_at,
            "quality": snapshot.quality,
            "result_consistency": snapshot.result_consistency,
            "ht_consistency": snapshot.ht_consistency,
            "raw_payload_path": snapshot.raw_payload_path,
            "extra_stats": snapshot.extra_stats,
            "provider": snapshot.provider,
            "stats_period": snapshot.stats_period,
            "source_context": snapshot.source_context,
            "captured_live": snapshot.captured_live,
            "tipico_event_id": snapshot.tipico_event_id,
            "match": snapshot.match.to_dict(),
        }

    def save_snapshot(self, snapshot: FotMobSnapshot) -> tuple[int, bool]:
        if snapshot.snapshot_type not in FOTMOB_SNAPSHOT_TYPES:
            raise ValueError(f"Unsupported FotMob snapshot type: {snapshot.snapshot_type}")
        payload = self._snapshot_payload(snapshot)
        payload_json = _json(payload)
        payload_hash = _hash_payload(payload)
        match = snapshot.match
        values = [
            snapshot.internal_match_id, match.provider_match_id, snapshot.captured_at,
            snapshot.snapshot_type, match.status, match.period, match.minute,
            match.added_time, match.score_home, match.score_away,
            match.ht_score_home, match.ht_score_away, *_stats_values(match.stats),
            _json(match.stats.to_dict()), _json(match.ht_stats.to_dict()) if match.ht_stats else None,
            _json({**match.stats.extra_stats, **snapshot.extra_stats}),
            _json([item.to_dict() for item in match.events]), snapshot.raw_payload_path,
            snapshot.result_consistency, snapshot.ht_consistency, snapshot.quality,
            snapshot.schema_version, payload_hash, snapshot.provider.upper(),
            snapshot.stats_period, snapshot.source_context, int(bool(snapshot.captured_live)),
            snapshot.tipico_event_id,
        ]
        columns = (
            "internal_match_id", "provider_match_id", "captured_at", "snapshot_type", "status",
            "period", "minute", "added_time", "score_home", "score_away", "ht_score_home",
            "ht_score_away", "xg_home", "xg_away", "shots_home", "shots_away",
            "shots_on_target_home", "shots_on_target_away", "big_chances_home", "big_chances_away",
            "corners_home", "corners_away", "possession_home", "possession_away",
            "yellow_cards_home", "yellow_cards_away", "red_cards_home", "red_cards_away",
            "stats_json", "ht_stats_json", "extra_stats_json", "events_json",
            "raw_payload_path", "result_consistency", "ht_consistency", "snapshot_quality",
            "schema_version", "payload_hash", "provider", "stats_period", "source_context",
            "captured_live", "tipico_event_id",
        )
        with self._lock, self._connection():
            existing = self._connection().execute(
                "SELECT snapshot_id FROM fotmob_snapshots WHERE internal_match_id = ? AND snapshot_type = ?",
                (snapshot.internal_match_id, snapshot.snapshot_type),
            ).fetchone()
            if existing is not None:
                return int(existing["snapshot_id"]), False
            cursor = self._connection().execute(
                f"INSERT INTO fotmob_snapshots ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                values,
            )
            snapshot_id = int(cursor.lastrowid)
            self._connection().execute(
                """
                INSERT INTO fotmob_snapshot_outbox (
                    snapshot_id, internal_match_id, snapshot_type, captured_at,
                    payload_json, payload_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id, snapshot.internal_match_id, snapshot.snapshot_type,
                    snapshot.captured_at, payload_json, payload_hash, _now(),
                ),
            )
        return snapshot_id, True

    def pending_outbox(self, batch_size: int = 100) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._connection().execute(
                """
                SELECT * FROM fotmob_snapshot_outbox
                WHERE exported = 0
                ORDER BY captured_at, snapshot_id
                LIMIT ?
                """,
                (max(1, int(batch_size)),),
            ).fetchall())

    def mark_outbox_error(self, snapshot_ids: Iterable[int], error: str) -> None:
        ids = [int(item) for item in snapshot_ids]
        if not ids:
            return
        placeholders = ", ".join("?" for _ in ids)
        with self._lock, self._connection():
            self._connection().execute(
                f"UPDATE fotmob_snapshot_outbox SET attempts = attempts + 1, last_error = ? WHERE snapshot_id IN ({placeholders})",
                [str(error), *ids],
            )

    def mark_exported(self, snapshot_ids: Iterable[int], archive_path: str, exported_at: str) -> int:
        ids = [int(item) for item in snapshot_ids]
        if not ids:
            return 0
        placeholders = ", ".join("?" for _ in ids)
        with self._lock, self._connection():
            self._connection().execute(
                f"UPDATE fotmob_snapshots SET archive_path = ?, exported_at = ? WHERE snapshot_id IN ({placeholders})",
                [str(archive_path), str(exported_at), *ids],
            )
            cursor = self._connection().execute(
                f"UPDATE fotmob_snapshot_outbox SET exported = 1, exported_at = ?, last_error = NULL WHERE snapshot_id IN ({placeholders})",
                [str(exported_at), *ids],
            )
        return int(cursor.rowcount)

    def delete_exported_outbox(self) -> int:
        with self._lock, self._connection():
            cursor = self._connection().execute(
                "DELETE FROM fotmob_snapshot_outbox WHERE exported = 1"
            )
        return int(cursor.rowcount)

    def snapshots_for_match(self, internal_match_id: str, limit: int = 50) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._connection().execute(
                "SELECT * FROM fotmob_snapshots WHERE internal_match_id = ? ORDER BY captured_at, snapshot_id LIMIT ?",
                (internal_match_id, max(1, int(limit))),
            ).fetchall())

    def upsert_quality(
        self,
        *,
        internal_match_id: str,
        fotmob_matched: bool,
        fotmob_ht_available: bool,
        fotmob_ht_stats_available: bool,
        tipico_ht_available: bool,
        result_consistency: str | None,
        ht_consistency: str | None,
        fotmob_result_status: str | None,
        quality_flags: Iterable[str] = (),
    ) -> None:
        with self._lock, self._connection():
            self._connection().execute(
                """
                INSERT INTO match_data_quality (
                    internal_match_id, fotmob_matched, fotmob_ht_available,
                    fotmob_ht_stats_available, tipico_ht_available,
                    result_consistency, ht_consistency, fotmob_result_status,
                    quality_flags_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(internal_match_id) DO UPDATE SET
                    fotmob_matched = excluded.fotmob_matched,
                    fotmob_ht_available = excluded.fotmob_ht_available,
                    fotmob_ht_stats_available = excluded.fotmob_ht_stats_available,
                    tipico_ht_available = excluded.tipico_ht_available,
                    result_consistency = excluded.result_consistency,
                    ht_consistency = excluded.ht_consistency,
                    fotmob_result_status = excluded.fotmob_result_status,
                    quality_flags_json = excluded.quality_flags_json,
                    updated_at = excluded.updated_at
                """,
                (
                    internal_match_id, int(fotmob_matched), int(fotmob_ht_available),
                    int(fotmob_ht_stats_available), int(tipico_ht_available),
                    result_consistency, ht_consistency, fotmob_result_status,
                    _json(list(quality_flags)), _now(),
                ),
            )

    def quality(self, internal_match_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self._connection().execute(
                "SELECT * FROM match_data_quality WHERE internal_match_id = ?",
                (internal_match_id,),
            ).fetchone()

    def debug_rows(self, limit: int = 200) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._connection().execute(
                """
                SELECT m.internal_match_id, m.tipico_event_id, m.home_team, m.away_team,
                       m.competition_name, m.competition_country, m.kickoff_at,
                       l.provider_match_id, l.match_confidence, l.match_status,
                       l.reason, c.observed_at, c.status AS fotmob_status,
                       c.period, c.minute, c.xg_home, c.xg_away,
                       c.ht_stats_json, q.result_consistency, q.ht_consistency,
                       q.quality_flags_json
                FROM matches m
                LEFT JOIN match_provider_links l
                    ON l.internal_match_id = m.internal_match_id AND l.provider = 'FOTMOB'
                LEFT JOIN fotmob_current_state c
                    ON c.internal_match_id = m.internal_match_id
                LEFT JOIN match_data_quality q
                    ON q.internal_match_id = m.internal_match_id
                ORDER BY COALESCE(c.observed_at, m.updated_at) DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall())

    def metrics_for_date(self, date_text: str | None = None) -> dict[str, Any]:
        day = date_text or datetime.now(timezone.utc).date().isoformat()
        with self._lock:
            conn = self._connection()
            values = {
                "matches": conn.execute(
                    "SELECT COUNT(*) AS n FROM matches WHERE substr(updated_at, 1, 10) = ?", (day,)
                ).fetchone(),
                "links": conn.execute(
                    "SELECT COUNT(*) AS n FROM match_provider_links WHERE provider = 'FOTMOB' AND substr(created_at, 1, 10) = ?", (day,)
                ).fetchone(),
                "current_state": conn.execute(
                    "SELECT COUNT(*) AS n FROM fotmob_current_state WHERE substr(updated_at, 1, 10) = ?", (day,)
                ).fetchone(),
                "snapshots": conn.execute(
                    "SELECT COUNT(*) AS n FROM fotmob_snapshots WHERE substr(captured_at, 1, 10) = ?", (day,)
                ).fetchone(),
                "outbox_pending": conn.execute(
                    "SELECT COUNT(*) AS n FROM fotmob_snapshot_outbox WHERE exported = 0"
                ).fetchone(),
                "ht_stats": conn.execute(
                    "SELECT COUNT(*) AS n FROM match_data_quality WHERE fotmob_ht_stats_available = 1 AND substr(updated_at, 1, 10) = ?", (day,)
                ).fetchone(),
                "xg_available": conn.execute(
                    "SELECT COUNT(*) AS n FROM fotmob_current_state WHERE xg_home IS NOT NULL AND xg_away IS NOT NULL AND substr(updated_at, 1, 10) = ?", (day,)
                ).fetchone(),
                "big_chances_available": conn.execute(
                    "SELECT COUNT(*) AS n FROM fotmob_current_state WHERE big_chances_home IS NOT NULL AND big_chances_away IS NOT NULL AND substr(updated_at, 1, 10) = ?", (day,)
                ).fetchone(),
            }
            status_rows = conn.execute(
                """
                SELECT match_status, COUNT(*) AS n
                FROM match_provider_links
                WHERE provider = 'FOTMOB'
                GROUP BY match_status
                """
            ).fetchall()
        counts = {str(row["match_status"]): int(row["n"]) for row in status_rows}
        considered = sum(counts.values())
        automatic = counts.get("EXACT", 0) + counts.get("HIGH_CONFIDENCE", 0)
        return {
            **{key: int(row["n"]) if row else 0 for key, row in values.items()},
            "date": day,
            "matching_status": counts,
            "matches_considered": considered,
            "automatic_links": automatic,
            "automatic_match_rate": automatic / considered if considered else 0.0,
        }


class FotMobParquetArchive:
    """Write outbox rows as flat, ZSTD-compressed Parquet partitions."""

    def __init__(self, root: Path | str, compression: str = "zstd") -> None:
        self.root = Path(root)
        self.snapshot_root = self.root / "fotmob" / "snapshots"
        self.compression = compression
        self.last_export_at: str | None = None
        self.last_error: str | None = None

    @staticmethod
    def _flat_row(payload: Mapping[str, Any], payload_hash: str) -> dict[str, Any]:
        match = payload.get("match") if isinstance(payload.get("match"), Mapping) else {}
        stats = match.get("stats") if isinstance(match.get("stats"), Mapping) else {}
        return {
            "schema_version": payload.get("schema_version", FOTMOB_SCHEMA_VERSION),
            "internal_match_id": payload.get("internal_match_id"),
            "provider": payload.get("provider", "FOTMOB"),
            "provider_match_id": match.get("provider_match_id"),
            "snapshot_type": payload.get("snapshot_type"),
            "captured_at": payload.get("captured_at"),
            "stats_period": payload.get("stats_period"),
            "source_context": payload.get("source_context"),
            "captured_live": int(bool(payload.get("captured_live"))),
            "tipico_event_id": payload.get("tipico_event_id"),
            "quality": payload.get("quality"),
            "result_consistency": payload.get("result_consistency"),
            "ht_consistency": payload.get("ht_consistency"),
            "kickoff_at": match.get("kickoff_at"),
            "competition_id": match.get("competition_id"),
            "competition_name": match.get("competition_name"),
            "competition_country": match.get("competition_country"),
            "home_team": match.get("home_team"),
            "away_team": match.get("away_team"),
            "status": match.get("status"),
            "period": match.get("period"),
            "minute": match.get("minute"),
            "score_home": match.get("score_home"),
            "score_away": match.get("score_away"),
            "ht_score_home": match.get("ht_score_home"),
            "ht_score_away": match.get("ht_score_away"),
            "xg_home": stats.get("xg_home"),
            "xg_away": stats.get("xg_away"),
            "shots_home": stats.get("shots_home"),
            "shots_away": stats.get("shots_away"),
            "shots_on_target_home": stats.get("shots_on_target_home"),
            "shots_on_target_away": stats.get("shots_on_target_away"),
            "big_chances_home": stats.get("big_chances_home"),
            "big_chances_away": stats.get("big_chances_away"),
            "corners_home": stats.get("corners_home"),
            "corners_away": stats.get("corners_away"),
            "possession_home": stats.get("possession_home"),
            "possession_away": stats.get("possession_away"),
            "stats_json": _json(stats),
            "ht_stats_json": _json(match.get("ht_stats")) if match.get("ht_stats") else None,
            "extra_stats_json": _json(payload.get("extra_stats", {})),
            "events_json": _json(match.get("events", [])),
            "payload_hash": payload_hash,
        }

    def _partition_path(self, captured_at: str) -> Path:
        try:
            value = datetime.fromisoformat(str(captured_at).replace("Z", "+00:00"))
        except ValueError:
            value = datetime.now(timezone.utc)
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        value = value.astimezone(timezone.utc)
        return (
            self.snapshot_root
            / f"year={value.year:04d}"
            / f"month={value.month:02d}"
            / f"date={value.date().isoformat()}"
        )

    def export_pending(self, store: FotMobStore, *, batch_size: int = 100) -> dict[str, Any]:
        pending = store.pending_outbox(batch_size)
        if not pending:
            return {"snapshots_exported": 0, "errors": 0, "outbox_pending": 0}
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq

            rows = [
                self._flat_row(json.loads(str(row["payload_json"])), str(row["payload_hash"]))
                for row in pending
            ]
            table = pa.Table.from_pylist(rows)
            partition = self._partition_path(str(pending[0]["captured_at"]))
            partition.mkdir(parents=True, exist_ok=True)
            destination = partition / f"fotmob-snapshots-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}.parquet"
            temporary = destination.with_suffix(destination.suffix + ".tmp")
            pq.write_table(table, temporary, compression=self.compression)
            temporary.replace(destination)
            exported_at = _now()
            store.mark_exported([int(row["snapshot_id"]) for row in pending], str(destination), exported_at)
            store.delete_exported_outbox()
            self.last_export_at = exported_at
            self.last_error = None
            return {
                "snapshots_exported": len(pending),
                "errors": 0,
                "path": str(destination),
                "outbox_pending": len(store.pending_outbox(batch_size)),
            }
        except Exception as exc:  # pragma: no cover - exercised by deployment failures
            self.last_error = str(exc)
            store.mark_outbox_error([int(row["snapshot_id"]) for row in pending], str(exc))
            return {"snapshots_exported": 0, "errors": 1, "error": str(exc)}

    @property
    def total_size_bytes(self) -> int:
        if not self.root.exists():
            return 0
        return sum(path.stat().st_size for path in self.root.rglob("*") if path.is_file())

    def size_for_date(self, date_text: str) -> int:
        path = self.snapshot_root / f"year={date_text[:4]}" / f"month={date_text[5:7]}" / f"date={date_text}"
        if not path.exists():
            return 0
        return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
