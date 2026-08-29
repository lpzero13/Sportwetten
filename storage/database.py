"""SQLite persistence for normalized Tipico observations."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from models.event import LiveEvent
from models.event_state import EventState
from models.market import Market, Outcome
from models.snapshot import Snapshot


SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    competition_id TEXT,
    competition_name TEXT NOT NULL,
    sport TEXT NOT NULL,
    home_team_id TEXT,
    home_team TEXT NOT NULL,
    away_team_id TEXT,
    away_team TEXT NOT NULL,
    kickoff_time TEXT,
    status TEXT,
    period TEXT,
    display_time TEXT,
    score_home INTEGER,
    score_away INTEGER,
    ht_score_home INTEGER,
    ht_score_away INTEGER,
    bet_markets_count INTEGER,
    section_number INTEGER,
    red_cards_home INTEGER,
    red_cards_away INTEGER,
    sport_radar_match_id TEXT,
    bet_genius_id TEXT,
    extra_time INTEGER,
    penalties INTEGER,
    break_before TEXT,
    clock_data_json TEXT NOT NULL DEFAULT '{}',
    raw_data_json TEXT NOT NULL DEFAULT '{}',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    last_updated_at TEXT
);

CREATE TABLE IF NOT EXISTS event_states (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    status TEXT NOT NULL,
    period TEXT NOT NULL,
    display_time TEXT NOT NULL,
    section_number INTEGER,
    score_home INTEGER,
    score_away INTEGER,
    ht_score_home INTEGER,
    ht_score_away INTEGER,
    red_cards_home INTEGER,
    red_cards_away INTEGER,
    raw_state_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_event_states_event_observed
    ON event_states(event_id, observed_at, id);

CREATE TABLE IF NOT EXISTS markets (
    market_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    caption TEXT NOT NULL,
    short_caption TEXT NOT NULL,
    type TEXT NOT NULL,
    fixed_param TEXT NOT NULL,
    standard INTEGER NOT NULL,
    status TEXT NOT NULL,
    category_ids_json TEXT NOT NULL DEFAULT '[]',
    category_names_json TEXT NOT NULL DEFAULT '[]',
    raw_data_json TEXT NOT NULL DEFAULT '{}',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_markets_event
    ON markets(event_id);

CREATE TABLE IF NOT EXISTS outcomes (
    outcome_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    market_id TEXT NOT NULL,
    caption TEXT NOT NULL,
    choice_param TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    raw_data_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_outcomes_market
    ON outcomes(market_id);

CREATE TABLE IF NOT EXISTS odds_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    market_id TEXT NOT NULL,
    outcome_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    snapshot_id INTEGER,
    odds REAL,
    quote_raw TEXT,
    status TEXT NOT NULL,
    available INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_odds_history_event
    ON odds_history(event_id);

CREATE INDEX IF NOT EXISTS idx_odds_history_market
    ON odds_history(market_id);

CREATE INDEX IF NOT EXISTS idx_odds_history_outcome_observed
    ON odds_history(outcome_id, observed_at, id);

CREATE INDEX IF NOT EXISTS idx_odds_history_observed
    ON odds_history(observed_at);

CREATE TABLE IF NOT EXISTS competitions (
    competition_id TEXT PRIMARY KEY,
    competition_name TEXT NOT NULL,
    country_or_region TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    events_observed INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    snapshot_type TEXT NOT NULL,
    trigger_reason TEXT,
    match_status TEXT,
    display_time TEXT,
    score_home INTEGER,
    score_away INTEGER,
    ht_score_home INTEGER,
    ht_score_away INTEGER,
    market_count INTEGER NOT NULL DEFAULT 0,
    outcome_count INTEGER NOT NULL DEFAULT 0,
    open_outcome_count INTEGER NOT NULL DEFAULT 0,
    paused_outcome_count INTEGER NOT NULL DEFAULT 0,
    snapshot_quality TEXT,
    raw_payload_path TEXT,
    second_half_goals INTEGER,
    second_half_goal_class TEXT
);

CREATE INDEX IF NOT EXISTS idx_snapshots_event_observed
    ON snapshots(event_id, observed_at, snapshot_id);

CREATE INDEX IF NOT EXISTS idx_snapshots_observed_type
    ON snapshots(observed_at, snapshot_type);

CREATE TABLE IF NOT EXISTS market_presence (
    presence_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    market_id TEXT NOT NULL,
    snapshot_id INTEGER NOT NULL,
    observed_at TEXT NOT NULL,
    present INTEGER NOT NULL,
    market_type TEXT,
    fixed_param TEXT,
    market_status TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_market_presence_snapshot_market
    ON market_presence(snapshot_id, market_id);

CREATE INDEX IF NOT EXISTS idx_market_presence_event_observed
    ON market_presence(event_id, observed_at, market_id);

CREATE TABLE IF NOT EXISTS canonical_outcomes (
    canonical_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    market_id TEXT NOT NULL,
    outcome_id TEXT NOT NULL,
    snapshot_id INTEGER,
    observed_at TEXT NOT NULL,
    canonical_type TEXT NOT NULL,
    scope TEXT NOT NULL,
    period TEXT NOT NULL,
    side TEXT,
    line REAL,
    team TEXT,
    odds REAL,
    status TEXT NOT NULL,
    available INTEGER NOT NULL DEFAULT 0,
    raw_market_type TEXT NOT NULL,
    raw_market_caption TEXT NOT NULL,
    raw_fixed_param TEXT NOT NULL,
    raw_choice_param TEXT,
    raw_outcome_caption TEXT NOT NULL,
    settlement_scope TEXT NOT NULL DEFAULT 'UNKNOWN',
    normalizer_version TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_canonical_outcome_observation
    ON canonical_outcomes(
        event_id, market_id, outcome_id, observed_at, normalizer_version
    );

CREATE INDEX IF NOT EXISTS idx_canonical_outcomes_event_observed
    ON canonical_outcomes(event_id, observed_at, canonical_type);

CREATE INDEX IF NOT EXISTS idx_canonical_outcomes_type
    ON canonical_outcomes(canonical_type, raw_market_type);

CREATE TABLE IF NOT EXISTS strategy_evaluations (
    evaluation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    strategy_type TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    normalizer_version TEXT NOT NULL,
    status TEXT NOT NULL,
    total_stake REAL NOT NULL,
    q_zero REAL,
    q_two_plus REAL,
    source_zero TEXT,
    source_two_plus TEXT,
    stake_zero REAL,
    stake_two_plus REAL,
    payout_zero REAL,
    payout_two_plus REAL,
    payout_difference REAL,
    covered_profit REAL,
    win_roi REAL,
    p1_max REAL,
    p1_tipico REAL,
    p1_buffer REAL
);

CREATE INDEX IF NOT EXISTS idx_strategy_evaluations_event_observed
    ON strategy_evaluations(event_id, strategy_type, observed_at, evaluation_id);
"""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _bool_int(value: bool | None) -> int | None:
    return None if value is None else int(bool(value))


class Database:
    """Thread-safe SQLite wrapper with explicit observation semantics."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.connection = sqlite3.connect(
            self.path,
            check_same_thread=False,
            timeout=30,
        )
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.executescript(SCHEMA)
        self._ensure_column("odds_history", "snapshot_id", "INTEGER")
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_odds_history_snapshot ON odds_history(snapshot_id)"
        )
        self._backfill_competitions()
        self.connection.commit()

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        """Apply small additive migrations to databases created by V0.1."""

        columns = {
            str(row["name"])
            for row in self.connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            self.connection.execute(
                f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
            )

    def _backfill_competitions(self) -> None:
        """Populate the new metadata table for events collected by V0.1."""

        self.connection.execute(
            """
            INSERT INTO competitions (
                competition_id, competition_name, country_or_region,
                first_seen_at, last_seen_at, events_observed
            )
            SELECT competition_id, MAX(competition_name), NULL,
                   MIN(first_seen_at), MAX(last_seen_at), COUNT(*)
            FROM events
            WHERE competition_id IS NOT NULL
            GROUP BY competition_id
            ON CONFLICT(competition_id) DO UPDATE SET
                competition_name = excluded.competition_name,
                first_seen_at = MIN(competitions.first_seen_at, excluded.first_seen_at),
                last_seen_at = MAX(competitions.last_seen_at, excluded.last_seen_at),
                events_observed = MAX(competitions.events_observed, excluded.events_observed)
            """
        )

    def close(self) -> None:
        with self._lock:
            self.connection.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def upsert_event(self, event: LiveEvent, observed_at: str) -> None:
        values = (
            event.event_id,
            event.competition_id,
            event.competition_name,
            event.sport,
            event.home_team_id,
            event.home_team,
            event.away_team_id,
            event.away_team,
            event.kickoff_time,
            event.status,
            event.period,
            event.display_minute,
            event.score_home,
            event.score_away,
            event.ht_score_home,
            event.ht_score_away,
            event.bet_markets_count,
            event.section_number,
            event.red_cards_home,
            event.red_cards_away,
            event.sport_radar_match_id,
            event.bet_genius_id,
            _bool_int(event.extra_time),
            _bool_int(event.penalties),
            _json(event.break_before),
            _json(event.clock_data),
            _json(event.raw_data),
            event.first_seen_at or observed_at,
            observed_at,
            event.last_updated_at or observed_at,
        )
        with self._lock, self.connection:
            self.connection.execute(
                """
                INSERT INTO events (
                    event_id, competition_id, competition_name, sport,
                    home_team_id, home_team, away_team_id, away_team,
                    kickoff_time, status, period, display_time,
                    score_home, score_away, ht_score_home, ht_score_away,
                    bet_markets_count, section_number, red_cards_home,
                    red_cards_away, sport_radar_match_id, bet_genius_id,
                    extra_time, penalties, break_before, clock_data_json,
                    raw_data_json, first_seen_at, last_seen_at, last_updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id) DO UPDATE SET
                    competition_id = excluded.competition_id,
                    competition_name = excluded.competition_name,
                    sport = excluded.sport,
                    home_team_id = excluded.home_team_id,
                    home_team = excluded.home_team,
                    away_team_id = excluded.away_team_id,
                    away_team = excluded.away_team,
                    kickoff_time = excluded.kickoff_time,
                    status = excluded.status,
                    period = excluded.period,
                    display_time = excluded.display_time,
                    score_home = excluded.score_home,
                    score_away = excluded.score_away,
                    ht_score_home = excluded.ht_score_home,
                    ht_score_away = excluded.ht_score_away,
                    bet_markets_count = excluded.bet_markets_count,
                    section_number = excluded.section_number,
                    red_cards_home = excluded.red_cards_home,
                    red_cards_away = excluded.red_cards_away,
                    sport_radar_match_id = excluded.sport_radar_match_id,
                    bet_genius_id = excluded.bet_genius_id,
                    extra_time = excluded.extra_time,
                    penalties = excluded.penalties,
                    break_before = excluded.break_before,
                    clock_data_json = excluded.clock_data_json,
                    raw_data_json = excluded.raw_data_json,
                    last_seen_at = excluded.last_seen_at,
                    last_updated_at = excluded.last_updated_at
                """,
                values,
            )
            if event.competition_id:
                groups = event.raw_data.get("groups") if isinstance(event.raw_data, dict) else None
                region = (
                    str(groups[1])
                    if isinstance(groups, (list, tuple)) and len(groups) > 1 and groups[1]
                    else None
                )
                self._upsert_competition_in_transaction(
                    str(event.competition_id),
                    event.competition_name or str(event.competition_id),
                    region,
                    event.first_seen_at or observed_at,
                    observed_at,
                )

    def _upsert_competition_in_transaction(
        self,
        competition_id: str,
        competition_name: str,
        country_or_region: str | None,
        first_seen_at: str,
        last_seen_at: str,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO competitions (
                competition_id, competition_name, country_or_region,
                first_seen_at, last_seen_at, events_observed
            ) VALUES (?, ?, ?, ?, ?, 0)
            ON CONFLICT(competition_id) DO UPDATE SET
                competition_name = excluded.competition_name,
                country_or_region = COALESCE(
                    excluded.country_or_region, competitions.country_or_region
                ),
                first_seen_at = MIN(competitions.first_seen_at, excluded.first_seen_at),
                last_seen_at = MAX(competitions.last_seen_at, excluded.last_seen_at)
            """,
            (
                competition_id,
                competition_name or competition_id,
                country_or_region,
                first_seen_at,
                last_seen_at,
            ),
        )
        self.connection.execute(
            """
            UPDATE competitions
            SET events_observed = (
                SELECT COUNT(*) FROM events
                WHERE events.competition_id = competitions.competition_id
            )
            WHERE competition_id = ?
            """,
            (competition_id,),
        )

    def upsert_competition(
        self,
        competition_id: str,
        competition_name: str,
        country_or_region: str | None,
        observed_at: str,
    ) -> None:
        """Persist competition metadata without filtering any competition out."""

        resolved_id = str(competition_id)
        with self._lock, self.connection:
            self._upsert_competition_in_transaction(
                resolved_id,
                competition_name,
                country_or_region,
                observed_at,
                observed_at,
            )

    def record_event_state_if_changed(self, state: EventState) -> bool:
        """Store a state only when it differs from the latest state."""

        with self._lock:
            previous = self.connection.execute(
                """
                SELECT status, period, display_time, section_number,
                       score_home, score_away, ht_score_home, ht_score_away,
                       red_cards_home, red_cards_away
                FROM event_states
                WHERE event_id = ?
                ORDER BY observed_at DESC, id DESC
                LIMIT 1
                """,
                (state.event_id,),
            ).fetchone()
            if previous is not None:
                previous_key = tuple(previous)
                if previous_key == state.relevant_key:
                    return False
            with self.connection:
                self.connection.execute(
                    """
                    INSERT INTO event_states (
                        event_id, observed_at, status, period, display_time,
                        section_number, score_home, score_away,
                        ht_score_home, ht_score_away, red_cards_home,
                        red_cards_away, raw_state_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        state.event_id,
                        state.observed_at,
                        state.status,
                        state.period,
                        state.display_time,
                        state.section_number,
                        state.score_home,
                        state.score_away,
                        state.ht_score_home,
                        state.ht_score_away,
                        state.red_cards_home,
                        state.red_cards_away,
                        _json(state.raw_state or {}),
                    ),
                )
            return True

    def upsert_market(self, market: Market, observed_at: str) -> None:
        with self._lock, self.connection:
            self.connection.execute(
                """
                INSERT INTO markets (
                    market_id, event_id, caption, short_caption, type,
                    fixed_param, standard, status, category_ids_json,
                    category_names_json, raw_data_json, first_seen_at,
                    last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(market_id) DO UPDATE SET
                    event_id = excluded.event_id,
                    caption = excluded.caption,
                    short_caption = excluded.short_caption,
                    type = excluded.type,
                    fixed_param = excluded.fixed_param,
                    standard = excluded.standard,
                    status = excluded.status,
                    category_ids_json = excluded.category_ids_json,
                    category_names_json = excluded.category_names_json,
                    raw_data_json = excluded.raw_data_json,
                    last_seen_at = excluded.last_seen_at
                """,
                (
                    market.market_id,
                    market.event_id,
                    market.caption,
                    market.short_caption,
                    market.type,
                    market.fixed_param,
                    int(market.standard),
                    market.status,
                    _json(market.category_ids),
                    _json(market.category_names),
                    _json(market.raw_data),
                    market.first_seen_at or observed_at,
                    observed_at,
                ),
            )

    def upsert_outcome(self, outcome: Outcome, event_id: str, observed_at: str) -> None:
        with self._lock, self.connection:
            self.connection.execute(
                """
                INSERT INTO outcomes (
                    outcome_id, event_id, market_id, caption, choice_param,
                    first_seen_at, last_seen_at, raw_data_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(outcome_id) DO UPDATE SET
                    event_id = excluded.event_id,
                    market_id = excluded.market_id,
                    caption = excluded.caption,
                    choice_param = excluded.choice_param,
                    last_seen_at = excluded.last_seen_at,
                    raw_data_json = excluded.raw_data_json
                """,
                (
                    outcome.outcome_id,
                    event_id,
                    outcome.market_id,
                    outcome.caption,
                    outcome.choice_param,
                    outcome.first_seen_at or observed_at,
                    observed_at,
                    _json(outcome.raw_data),
                ),
            )

    def create_snapshot(self, snapshot: Snapshot) -> int:
        """Insert a snapshot and return its database identifier."""

        with self._lock, self.connection:
            cursor = self.connection.execute(
                """
                INSERT INTO snapshots (
                    event_id, observed_at, snapshot_type, trigger_reason,
                    match_status, display_time, score_home, score_away,
                    ht_score_home, ht_score_away, market_count, outcome_count,
                    open_outcome_count, paused_outcome_count, snapshot_quality,
                    raw_payload_path, second_half_goals, second_half_goal_class
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.event_id,
                    snapshot.observed_at,
                    snapshot.snapshot_type,
                    snapshot.trigger_reason,
                    snapshot.match_status,
                    snapshot.display_time,
                    snapshot.score_home,
                    snapshot.score_away,
                    snapshot.ht_score_home,
                    snapshot.ht_score_away,
                    snapshot.market_count,
                    snapshot.outcome_count,
                    snapshot.open_outcome_count,
                    snapshot.paused_outcome_count,
                    snapshot.snapshot_quality,
                    snapshot.raw_payload_path,
                    snapshot.second_half_goals,
                    snapshot.second_half_goal_class,
                ),
            )
            return int(cursor.lastrowid)

    def add_market_presence(
        self,
        *,
        event_id: str,
        market_id: str,
        snapshot_id: int,
        observed_at: str,
        present: bool = True,
        market_type: str | None = None,
        fixed_param: str | None = None,
        market_status: str | None = None,
    ) -> None:
        with self._lock, self.connection:
            self.connection.execute(
                """
                INSERT INTO market_presence (
                    event_id, market_id, snapshot_id, observed_at, present,
                    market_type, fixed_param, market_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(snapshot_id, market_id) DO UPDATE SET
                    present = excluded.present,
                    market_type = excluded.market_type,
                    fixed_param = excluded.fixed_param,
                    market_status = excluded.market_status
                """,
                (
                    event_id,
                    market_id,
                    snapshot_id,
                    observed_at,
                    int(present),
                    market_type,
                    fixed_param,
                    market_status,
                ),
            )

    def save_canonical_outcomes(
        self,
        outcomes: list[Any],
        *,
        snapshot_id: int | None = None,
    ) -> int:
        """Append normalized outcomes without overwriting raw/provider data."""

        inserted = 0
        with self._lock, self.connection:
            for outcome in outcomes:
                cursor = self.connection.execute(
                    """
                    INSERT OR IGNORE INTO canonical_outcomes (
                        event_id, market_id, outcome_id, snapshot_id, observed_at,
                        canonical_type, scope, period, side, line, team, odds,
                        status, available, raw_market_type, raw_market_caption,
                        raw_fixed_param, raw_choice_param, raw_outcome_caption,
                        settlement_scope, normalizer_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                              ?, ?, ?)
                    """,
                    (
                        str(outcome.event_id),
                        str(outcome.market_id),
                        str(outcome.outcome_id),
                        snapshot_id,
                        str(outcome.observed_at),
                        str(outcome.canonical_type),
                        str(outcome.scope),
                        str(outcome.period),
                        outcome.side,
                        outcome.line,
                        outcome.team,
                        outcome.odds,
                        str(outcome.status),
                        int(bool(outcome.available)),
                        str(outcome.raw_market_type),
                        str(outcome.raw_market_caption),
                        str(outcome.raw_fixed_param),
                        outcome.raw_choice_param,
                        str(outcome.raw_outcome_caption),
                        str(outcome.settlement_scope),
                        str(outcome.normalizer_version),
                    ),
                )
                if cursor.rowcount > 0:
                    inserted += cursor.rowcount
        return inserted

    def record_strategy_evaluation_if_changed(
        self,
        *,
        event_id: str,
        observed_at: str,
        strategy_type: str,
        strategy_version: str,
        normalizer_version: str,
        status: str,
        total_stake: float,
        q_zero: float | None,
        q_two_plus: float | None,
        source_zero: str | None,
        source_two_plus: str | None,
        stake_zero: float | None,
        stake_two_plus: float | None,
        payout_zero: float | None,
        payout_two_plus: float | None,
        payout_difference: float | None,
        covered_profit: float | None,
        win_roi: float | None,
        p1_max: float | None,
        p1_tipico: float | None,
        p1_buffer: float | None,
    ) -> bool:
        """Persist strategy state only when a material input changed."""

        signature = (
            status,
            float(total_stake),
            q_zero,
            q_two_plus,
            source_zero,
            source_two_plus,
            p1_tipico,
            normalizer_version,
            strategy_version,
        )
        with self._lock:
            previous = self.connection.execute(
                """
                SELECT status, total_stake, q_zero, q_two_plus,
                       source_zero, source_two_plus, p1_tipico,
                       normalizer_version, strategy_version
                FROM strategy_evaluations
                WHERE event_id = ? AND strategy_type = ?
                ORDER BY observed_at DESC, evaluation_id DESC
                LIMIT 1
                """,
                (str(event_id), str(strategy_type)),
            ).fetchone()
            if previous is not None and tuple(previous) == signature:
                return False
            with self.connection:
                self.connection.execute(
                    """
                    INSERT INTO strategy_evaluations (
                        event_id, observed_at, strategy_type, strategy_version,
                        normalizer_version, status, total_stake, q_zero,
                        q_two_plus, source_zero, source_two_plus, stake_zero,
                        stake_two_plus, payout_zero, payout_two_plus,
                        payout_difference, covered_profit, win_roi, p1_max,
                        p1_tipico, p1_buffer
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                              ?, ?, ?, ?, ?)
                    """,
                    (
                        str(event_id),
                        observed_at,
                        strategy_type,
                        strategy_version,
                        normalizer_version,
                        status,
                        total_stake,
                        q_zero,
                        q_two_plus,
                        source_zero,
                        source_two_plus,
                        stake_zero,
                        stake_two_plus,
                        payout_zero,
                        payout_two_plus,
                        payout_difference,
                        covered_profit,
                        win_roi,
                        p1_max,
                        p1_tipico,
                        p1_buffer,
                    ),
                )
            return True

    def latest_snapshot_for_event(
        self,
        event_id: str,
        *,
        snapshot_type: str | None = None,
        usable_only: bool = False,
    ) -> sqlite3.Row | None:
        clauses = ["event_id = ?"]
        params: list[Any] = [str(event_id)]
        if snapshot_type:
            clauses.append("snapshot_type = ?")
            params.append(str(snapshot_type))
        if usable_only:
            clauses.append("COALESCE(snapshot_quality, '') != 'FAILED'")
        query = (
            "SELECT * FROM snapshots WHERE "
            + " AND ".join(clauses)
            + " ORDER BY observed_at DESC, snapshot_id DESC LIMIT 1"
        )
        with self._lock:
            return self.connection.execute(query, params).fetchone()

    def odds_history_for_event(self, event_id: str, limit: int = 500) -> list[sqlite3.Row]:
        with self._lock:
            return list(
                self.connection.execute(
                    """
                    SELECT h.*, m.caption AS market_caption,
                           m.type AS market_type, o.caption AS outcome_caption
                    FROM odds_history h
                    LEFT JOIN markets m ON m.market_id = h.market_id
                    LEFT JOIN outcomes o ON o.outcome_id = h.outcome_id
                    WHERE h.event_id = ?
                    ORDER BY h.observed_at DESC, h.id DESC
                    LIMIT ?
                    """,
                    (str(event_id), max(1, int(limit))),
                ).fetchall()
            )

    def canonical_metrics_for_date(self, date_text: str | None = None) -> dict[str, Any]:
        day = date_text or datetime.now(timezone.utc).date().isoformat()
        with self._lock:
            row = self.connection.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN canonical_type != 'UNKNOWN' THEN 1 ELSE 0 END) AS known,
                       SUM(CASE WHEN canonical_type = 'UNKNOWN' THEN 1 ELSE 0 END) AS unknown,
                       COUNT(DISTINCT event_id) AS events,
                       MAX(observed_at) AS latest_observed_at
                FROM canonical_outcomes
                WHERE substr(observed_at, 1, 10) = ?
                """,
                (day,),
            ).fetchone()
        return {
            "date": day,
            "total": int(row["total"] or 0) if row else 0,
            "known": int(row["known"] or 0) if row else 0,
            "unknown": int(row["unknown"] or 0) if row else 0,
            "events": int(row["events"] or 0) if row else 0,
            "latest_observed_at": row["latest_observed_at"] if row else None,
        }

    def unknown_market_types(self, limit: int = 50) -> list[sqlite3.Row]:
        with self._lock:
            return list(
                self.connection.execute(
                    """
                    SELECT raw_market_type, raw_market_caption,
                           COUNT(*) AS outcome_count,
                           MAX(observed_at) AS latest_observed_at
                    FROM canonical_outcomes
                    WHERE canonical_type = 'UNKNOWN'
                    GROUP BY raw_market_type, raw_market_caption
                    ORDER BY outcome_count DESC, raw_market_type
                    LIMIT ?
                    """,
                    (max(1, int(limit)),),
                ).fetchall()
            )

    def market_type_counts(self, limit: int = 50) -> list[sqlite3.Row]:
        with self._lock:
            return list(
                self.connection.execute(
                    """
                    SELECT type, COUNT(*) AS market_count,
                           MAX(last_seen_at) AS latest_seen_at
                    FROM markets
                    GROUP BY type
                    ORDER BY market_count DESC, type
                    LIMIT ?
                    """,
                    (max(1, int(limit)),),
                ).fetchall()
            )

    @property
    def database_size_bytes(self) -> int:
        try:
            return int(self.path.stat().st_size)
        except OSError:
            return 0

    def canonical_outcomes_for_event(
        self,
        event_id: str,
        limit: int = 1000,
    ) -> list[sqlite3.Row]:
        with self._lock:
            return list(
                self.connection.execute(
                    """
                    SELECT * FROM canonical_outcomes
                    WHERE event_id = ?
                    ORDER BY observed_at DESC, canonical_id DESC
                    LIMIT ?
                    """,
                    (str(event_id), max(1, int(limit))),
                ).fetchall()
            )

    def strategy_evaluations_for_event(
        self,
        event_id: str,
        limit: int = 100,
    ) -> list[sqlite3.Row]:
        with self._lock:
            return list(
                self.connection.execute(
                    """
                    SELECT * FROM strategy_evaluations
                    WHERE event_id = ?
                    ORDER BY observed_at DESC, evaluation_id DESC
                    LIMIT ?
                    """,
                    (str(event_id), max(1, int(limit))),
                ).fetchall()
            )

    @staticmethod
    def _history_status(outcome: Outcome) -> str:
        if outcome.status:
            return outcome.status
        return "open" if outcome.is_available else "unavailable"

    def record_odds_change_if_needed(
        self,
        outcome: Outcome,
        event_id: str,
        observed_at: str,
        *,
        snapshot_id: int | None = None,
    ) -> bool:
        """Insert a history row only for odds/status/availability changes."""

        current = (
            outcome.odds,
            self._history_status(outcome),
            int(outcome.is_available),
        )
        with self._lock:
            previous = self.connection.execute(
                """
                SELECT odds, status, available
                FROM odds_history
                WHERE outcome_id = ?
                ORDER BY observed_at DESC, id DESC
                LIMIT 1
                """,
                (outcome.outcome_id,),
            ).fetchone()
            if previous is not None:
                previous_key = (
                    previous["odds"],
                    previous["status"],
                    previous["available"],
                )
                if previous_key == current:
                    return False
            with self.connection:
                self.connection.execute(
                    """
                    INSERT INTO odds_history (
                        event_id, market_id, outcome_id, observed_at,
                        snapshot_id, odds, quote_raw, status, available
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        outcome.market_id,
                        outcome.outcome_id,
                        observed_at,
                        snapshot_id,
                        outcome.odds,
                        outcome.quote_raw,
                        current[1],
                        current[2],
                    ),
                )
            return True

    def latest_event_state(self, event_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self.connection.execute(
                """
                SELECT *
                FROM event_states
                WHERE event_id = ?
                ORDER BY observed_at DESC, id DESC
                LIMIT 1
                """,
                (event_id,),
            ).fetchone()

    def recent_odds_changes(self, limit: int = 50) -> list[sqlite3.Row]:
        with self._lock:
            return list(
                self.connection.execute(
                    """
                    SELECT h.*, e.home_team, e.away_team,
                           m.caption AS market_caption,
                           o.caption AS outcome_caption
                    FROM odds_history h
                    LEFT JOIN events e ON e.event_id = h.event_id
                    LEFT JOIN markets m ON m.market_id = h.market_id
                    LEFT JOIN outcomes o ON o.outcome_id = h.outcome_id
                    ORDER BY h.observed_at DESC, h.id DESC
                    LIMIT ?
                    """,
                    (max(1, int(limit)),),
                ).fetchall()
            )

    def odds_changes_today(self) -> int:
        with self._lock:
            row = self.connection.execute(
                "SELECT COUNT(*) AS count FROM odds_history WHERE date(observed_at) = date('now')"
            ).fetchone()
            return int(row["count"]) if row else 0

    def count_rows(self, table: str) -> int:
        allowed = {
            "events",
            "event_states",
            "markets",
            "outcomes",
            "odds_history",
            "competitions",
            "snapshots",
            "market_presence",
            "canonical_outcomes",
            "strategy_evaluations",
        }
        if table not in allowed:
            raise ValueError(f"Unsupported table: {table}")
        with self._lock:
            row = self.connection.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
            return int(row["count"]) if row else 0

    def collection_metrics_for_date(self, date_text: str | None = None) -> dict[str, int]:
        """Return collector coverage counts for one UTC calendar date."""

        day = date_text or datetime.now(timezone.utc).date().isoformat()
        with self._lock:
            event_row = self.connection.execute(
                """
                SELECT COUNT(DISTINCT event_id) AS count
                FROM events
                WHERE (substr(first_seen_at, 1, 10) = ? OR substr(last_seen_at, 1, 10) = ?)
                  AND lower(sport) = 'soccer'
                """,
                (day, day),
            ).fetchone()
            competition_row = self.connection.execute(
                """
                SELECT COUNT(*) AS count FROM competitions
                WHERE (substr(first_seen_at, 1, 10) = ? OR substr(last_seen_at, 1, 10) = ?)
                """,
                (day, day),
            ).fetchone()
            snapshot_rows = self.connection.execute(
                """
                SELECT snapshot_type, COUNT(*) AS count
                FROM snapshots
                WHERE substr(observed_at, 1, 10) = ?
                GROUP BY snapshot_type
                """,
                (day,),
            ).fetchall()
            coverage_rows = self.connection.execute(
                """
                SELECT snapshot_type, COUNT(DISTINCT event_id) AS count
                FROM snapshots
                WHERE substr(observed_at, 1, 10) = ?
                  AND COALESCE(snapshot_quality, '') != 'FAILED'
                GROUP BY snapshot_type
                """,
                (day,),
            ).fetchall()
            prematch_events = self.connection.execute(
                """
                SELECT COUNT(DISTINCT event_id) AS count
                FROM snapshots
                WHERE substr(observed_at, 1, 10) = ?
                  AND snapshot_type IN ('PREMATCH', 'PRE_KICKOFF')
                  AND COALESCE(snapshot_quality, '') != 'FAILED'
                """,
                (day,),
            ).fetchone()
            core_events = self.connection.execute(
                """
                SELECT COUNT(DISTINCT s.event_id) AS count
                FROM snapshots s
                JOIN market_presence p ON p.snapshot_id = s.snapshot_id
                WHERE substr(s.observed_at, 1, 10) = ?
                  AND s.snapshot_type = 'LIVE_PERIODIC'
                  AND p.present = 1
                  AND (
                      lower(COALESCE(p.market_type, '')) IN (
                          'points-more-less-rest', 'next-point',
                          'team-points-more-less', 'score-both',
                          'points-more-less', 'section-points-more-less'
                      )
                      OR lower(COALESCE(p.market_type, '')) LIKE 'points-more-less%'
                      OR lower(COALESCE(p.market_type, '')) LIKE 'team-points-more-less%'
                  )
                  AND COALESCE(s.snapshot_quality, '') != 'FAILED'
                """,
                (day,),
            ).fetchone()
            failed_snapshots = self.connection.execute(
                """
                SELECT COUNT(*) AS count FROM snapshots
                WHERE substr(observed_at, 1, 10) = ?
                  AND snapshot_quality = 'FAILED'
                """,
                (day,),
            ).fetchone()

        snapshot_counts = {str(row["snapshot_type"]): int(row["count"]) for row in snapshot_rows}
        coverage = {str(row["snapshot_type"]): int(row["count"]) for row in coverage_rows}
        return {
            "football_events_seen": int(event_row["count"]) if event_row else 0,
            "competitions": int(competition_row["count"]) if competition_row else 0,
            "prematch_snapshots": snapshot_counts.get("PREMATCH", 0),
            "pre_kickoff_snapshots": snapshot_counts.get("PRE_KICKOFF", 0),
            "halftime_snapshots": snapshot_counts.get("HALFTIME", 0),
            "periodic_snapshots": snapshot_counts.get("LIVE_PERIODIC", 0),
            "goal_triggers": snapshot_counts.get("EVENT_TRIGGERED", 0),
            "final_snapshots": snapshot_counts.get("FINAL", 0),
            "failed_snapshots": int(failed_snapshots["count"]) if failed_snapshots else 0,
            "events_with_prematch_snapshot": int(prematch_events["count"])
            if prematch_events
            else 0,
            "events_with_halftime_snapshot": coverage.get("HALFTIME", 0),
            "events_with_final_result": coverage.get("FINAL", 0),
            "events_with_core_live_tracking": int(core_events["count"]) if core_events else 0,
        }

    def list_events_for_inspector(self, limit: int = 200) -> list[sqlite3.Row]:
        with self._lock:
            return list(
                self.connection.execute(
                    """
                    SELECT e.event_id, e.competition_name, e.home_team, e.away_team,
                           e.kickoff_time, e.status, e.period, e.display_time,
                           e.score_home, e.score_away, e.ht_score_home,
                           e.ht_score_away, e.first_seen_at, e.last_seen_at,
                           COUNT(DISTINCT s.snapshot_id) AS snapshot_count
                    FROM events e
                    LEFT JOIN snapshots s ON s.event_id = e.event_id
                    WHERE lower(e.sport) = 'soccer'
                    GROUP BY e.event_id
                    ORDER BY e.last_seen_at DESC, e.event_id
                    LIMIT ?
                    """,
                    (max(1, int(limit)),),
                ).fetchall()
            )

    def event_info(self, event_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self.connection.execute(
                "SELECT * FROM events WHERE event_id = ?",
                (str(event_id),),
            ).fetchone()

    def snapshots_for_event(self, event_id: str, limit: int = 500) -> list[sqlite3.Row]:
        with self._lock:
            return list(
                self.connection.execute(
                    """
                    SELECT * FROM snapshots
                    WHERE event_id = ?
                    ORDER BY observed_at, snapshot_id
                    LIMIT ?
                    """,
                    (str(event_id), max(1, int(limit))),
                ).fetchall()
            )

    def market_presence_for_snapshot(self, snapshot_id: int) -> list[sqlite3.Row]:
        with self._lock:
            return list(
                self.connection.execute(
                    """
                    SELECT * FROM market_presence
                    WHERE snapshot_id = ? AND present = 1
                    ORDER BY market_id
                    """,
                    (int(snapshot_id),),
                ).fetchall()
            )

    def mark_event_no_longer_live(self, event_id: str, observed_at: str) -> bool:
        """Keep historical data and add one state transition for a disappeared event."""

        latest = self.latest_event_state(event_id)
        if latest is None or str(latest["status"]).upper() in {"FINISHED", "NO_LONGER_LIVE"}:
            return False
        state = EventState(
            event_id=event_id,
            observed_at=observed_at,
            status="NO_LONGER_LIVE",
            period="NO_LONGER_LIVE",
            display_time="—",
            section_number=latest["section_number"],
            score_home=latest["score_home"],
            score_away=latest["score_away"],
            ht_score_home=latest["ht_score_home"],
            ht_score_away=latest["ht_score_away"],
            red_cards_home=latest["red_cards_home"],
            red_cards_away=latest["red_cards_away"],
            raw_state={"reason": "event_missing_from_live_feed"},
        )
        return self.record_event_state_if_changed(state)


def state_from_event(event: LiveEvent, observed_at: str) -> EventState:
    return EventState(
        event_id=event.event_id,
        observed_at=observed_at,
        status=event.status,
        period=event.period,
        display_time=event.display_minute,
        section_number=event.section_number,
        score_home=event.score_home,
        score_away=event.score_away,
        ht_score_home=event.ht_score_home,
        ht_score_away=event.ht_score_away,
        red_cards_home=event.red_cards_home,
        red_cards_away=event.red_cards_away,
        raw_state={
            "clockData": event.clock_data,
            "extraTime": event.extra_time,
            "penalties": event.penalties,
            "breakBefore": event.break_before,
        },
    )
