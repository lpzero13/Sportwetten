"""SQLite persistence for normalized Tipico observations."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections.abc import Iterable, Mapping
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
    competition_country TEXT,
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

-- Volatile state used by the live UI and collector.  Unlike event_states it
-- is deliberately one row per event and is safe to rebuild after a restart.
CREATE TABLE IF NOT EXISTS current_event_state (
    event_id TEXT PRIMARY KEY,
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
    second_half_goal_class TEXT,
    competition_id TEXT,
    competition_name TEXT,
    competition_country TEXT,
    home_team TEXT,
    away_team TEXT,
    kickoff_time TEXT,
    match_minute INTEGER,
    q_zero_best REAL,
    q_zero_source_type TEXT,
    q_zero_market_id TEXT,
    q_zero_outcome_id TEXT,
    q_two_plus_best REAL,
    q_two_plus_source_type TEXT,
    q_two_plus_market_id TEXT,
    q_two_plus_outcome_id TEXT,
    remaining_under_05 REAL,
    remaining_over_05 REAL,
    remaining_under_15 REAL,
    remaining_over_15 REAL,
    p0_market REAL,
    p1_market REAL,
    p2plus_market REAL,
    p1_break_even REAL,
    p1_buffer REAL,
    win_roi REAL,
    normalizer_version TEXT,
    strategy_version TEXT,
    relevant_markets_json TEXT NOT NULL DEFAULT '[]',
    goal_at TEXT,
    reopen_at TEXT,
    reopen_delay_seconds REAL,
    archive_path TEXT,
    exported_at TEXT,
    payload_hash TEXT
);

CREATE INDEX IF NOT EXISTS idx_snapshots_event_observed
    ON snapshots(event_id, observed_at, snapshot_id);

CREATE INDEX IF NOT EXISTS idx_snapshots_observed_type
    ON snapshots(observed_at, snapshot_type);

CREATE TABLE IF NOT EXISTS snapshot_outbox (
    snapshot_id INTEGER PRIMARY KEY,
    event_id TEXT NOT NULL,
    snapshot_type TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    exported INTEGER NOT NULL DEFAULT 0,
    exported_at TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(event_id, snapshot_type),
    FOREIGN KEY (snapshot_id) REFERENCES snapshots(snapshot_id)
);

CREATE INDEX IF NOT EXISTS idx_snapshot_outbox_pending
    ON snapshot_outbox(exported, captured_at, snapshot_id);

CREATE TABLE IF NOT EXISTS match_results (
    event_id TEXT PRIMARY KEY,
    competition_id TEXT,
    competition_name TEXT,
    competition_country TEXT,
    home_team TEXT NOT NULL,
    away_team TEXT NOT NULL,
    kickoff_at TEXT,
    ht_home INTEGER,
    ht_away INTEGER,
    ft_home INTEGER,
    ft_away INTEGER,
    first_half_goals INTEGER,
    second_half_goals INTEGER,
    second_half_goal_class TEXT,
    final_status TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    extra_time INTEGER,
    penalties INTEGER
);

CREATE INDEX IF NOT EXISTS idx_match_results_finished_at
    ON match_results(finished_at, event_id);

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

CREATE TABLE IF NOT EXISTS current_canonical_outcomes (
    event_id TEXT NOT NULL,
    market_id TEXT NOT NULL,
    outcome_id TEXT NOT NULL,
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
    normalizer_version TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(event_id, market_id, outcome_id)
);

CREATE INDEX IF NOT EXISTS idx_current_canonical_event_type
    ON current_canonical_outcomes(event_id, canonical_type, available);

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
    p1_buffer REAL,
    p_zero REAL,
    p_one REAL,
    p_two_plus REAL,
    trigger_type TEXT,
    is_eligible INTEGER
);

CREATE INDEX IF NOT EXISTS idx_strategy_evaluations_event_observed
    ON strategy_evaluations(event_id, strategy_type, observed_at, evaluation_id);

CREATE TABLE IF NOT EXISTS current_strategy_evaluations (
    event_id TEXT NOT NULL,
    strategy_type TEXT NOT NULL,
    observed_at TEXT NOT NULL,
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
    p1_buffer REAL,
    p_zero REAL,
    p_one REAL,
    p_two_plus REAL,
    last_transition_type TEXT,
    last_transition_at TEXT,
    last_evaluation_id INTEGER,
    updated_at TEXT NOT NULL,
    is_eligible INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(event_id, strategy_type)
);

CREATE INDEX IF NOT EXISTS idx_current_strategy_updated
    ON current_strategy_evaluations(updated_at, strategy_type, is_eligible);

CREATE TABLE IF NOT EXISTS paper_portfolios (
    portfolio_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    starting_bankroll REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'EUR',
    strategy_type TEXT NOT NULL DEFAULT 'ZERO_OR_2PLUS',
    stake_mode TEXT NOT NULL DEFAULT 'FIXED',
    fixed_stake REAL,
    bankroll_percentage REAL,
    min_stake REAL,
    max_stake REAL,
    minimum_win_roi REAL NOT NULL DEFAULT 0,
    minimum_p1_buffer REAL NOT NULL DEFAULT 0,
    maximum_tipico_p1 REAL NOT NULL DEFAULT 1,
    minimum_q_zero REAL NOT NULL DEFAULT 1,
    minimum_q_two_plus REAL NOT NULL DEFAULT 1,
    max_quote_age_seconds INTEGER NOT NULL DEFAULT 10,
    entry_window_start_seconds INTEGER NOT NULL DEFAULT 0,
    entry_window_end_seconds INTEGER NOT NULL DEFAULT 120,
    allow_all_competitions INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS paper_portfolio_competitions (
    portfolio_id TEXT NOT NULL,
    competition_id TEXT NOT NULL,
    PRIMARY KEY (portfolio_id, competition_id),
    FOREIGN KEY (portfolio_id) REFERENCES paper_portfolios(portfolio_id)
);

CREATE TABLE IF NOT EXISTS paper_trades (
    paper_trade_id TEXT PRIMARY KEY,
    portfolio_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    competition_id TEXT,
    competition_name TEXT NOT NULL,
    competition_country TEXT,
    created_at TEXT NOT NULL,
    strategy_evaluation_id INTEGER,
    strategy_type TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    normalizer_version TEXT NOT NULL,
    home_team TEXT NOT NULL,
    away_team TEXT NOT NULL,
    ht_score_home INTEGER,
    ht_score_away INTEGER,
    zero_market_id TEXT,
    zero_outcome_id TEXT,
    zero_market_type TEXT,
    zero_market_caption TEXT,
    zero_outcome_caption TEXT,
    q_zero REAL NOT NULL,
    zero_quote_observed_at TEXT,
    zero_quote_age_seconds REAL,
    two_plus_market_id TEXT,
    two_plus_outcome_id TEXT,
    two_plus_market_type TEXT,
    two_plus_market_caption TEXT,
    two_plus_outcome_caption TEXT,
    q_two_plus REAL NOT NULL,
    two_plus_quote_observed_at TEXT,
    two_plus_quote_age_seconds REAL,
    stake_total REAL NOT NULL,
    stake_zero REAL NOT NULL,
    stake_two_plus REAL NOT NULL,
    payout_zero REAL NOT NULL,
    payout_two_plus REAL NOT NULL,
    p_zero REAL,
    p_one REAL,
    p_two_plus REAL,
    p1_max REAL,
    p1_tipico REAL,
    p1_buffer REAL,
    win_roi REAL,
    entry_raw_payload_path TEXT,
    bankroll_before REAL NOT NULL,
    bankroll_after REAL NOT NULL,
    rank INTEGER,
    status TEXT NOT NULL DEFAULT 'OPEN',
    settled_at TEXT,
    final_score_home INTEGER,
    final_score_away INTEGER,
    second_half_goals INTEGER,
    settlement_reason TEXT,
    return_amount REAL,
    pnl REAL,
    invalidated_reason TEXT,
    entry_snapshot_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (portfolio_id) REFERENCES paper_portfolios(portfolio_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_paper_trade_one_entry
    ON paper_trades(portfolio_id, event_id, strategy_type);

CREATE INDEX IF NOT EXISTS idx_paper_trades_portfolio_status
    ON paper_trades(portfolio_id, status, created_at);

CREATE INDEX IF NOT EXISTS idx_paper_trades_event
    ON paper_trades(event_id, status);

CREATE TABLE IF NOT EXISTS paper_bankroll_transactions (
    transaction_id TEXT PRIMARY KEY,
    portfolio_id TEXT NOT NULL,
    paper_trade_id TEXT,
    created_at TEXT NOT NULL,
    transaction_type TEXT NOT NULL,
    amount REAL NOT NULL,
    balance_before REAL NOT NULL,
    balance_after REAL NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    note TEXT,
    FOREIGN KEY (portfolio_id) REFERENCES paper_portfolios(portfolio_id),
    FOREIGN KEY (paper_trade_id) REFERENCES paper_trades(paper_trade_id)
);

CREATE INDEX IF NOT EXISTS idx_paper_ledger_portfolio_time
    ON paper_bankroll_transactions(portfolio_id, created_at, transaction_id);

CREATE TABLE IF NOT EXISTS paper_signal_log (
    signal_id INTEGER PRIMARY KEY AUTOINCREMENT,
    portfolio_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    evaluation_id INTEGER,
    observed_at TEXT NOT NULL,
    decision TEXT NOT NULL,
    reason TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(portfolio_id, event_id, evaluation_id, decision),
    FOREIGN KEY (portfolio_id) REFERENCES paper_portfolios(portfolio_id)
);

CREATE TABLE IF NOT EXISTS paper_runtime_settings (
    setting_key TEXT PRIMARY KEY,
    setting_value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_worker_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    signals_seen INTEGER NOT NULL DEFAULT 0,
    trades_created INTEGER NOT NULL DEFAULT 0,
    trades_settled INTEGER NOT NULL DEFAULT 0,
    errors INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'RUNNING',
    error_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_paper_worker_runs_started
    ON paper_worker_runs(started_at DESC, run_id DESC);

-- Optional FotMob V0.5.2 historical catalog.  The tables live in the core
-- schema so a database opened by the UI already has the catalog contract;
-- fotmob.history_storage repeats the IF NOT EXISTS script for older DBs.
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
"""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _bool_int(value: bool | None) -> int | None:
    return None if value is None else int(bool(value))


SNAPSHOT_COLUMNS = (
    "event_id", "observed_at", "snapshot_type", "trigger_reason",
    "match_status", "display_time", "score_home", "score_away",
    "ht_score_home", "ht_score_away", "market_count", "outcome_count",
    "open_outcome_count", "paused_outcome_count", "snapshot_quality",
    "raw_payload_path", "second_half_goals", "second_half_goal_class",
    "competition_id", "competition_name", "competition_country",
    "home_team", "away_team", "kickoff_time", "match_minute",
    "q_zero_best", "q_zero_source_type", "q_zero_market_id", "q_zero_outcome_id",
    "q_two_plus_best", "q_two_plus_source_type", "q_two_plus_market_id",
    "q_two_plus_outcome_id", "remaining_under_05", "remaining_over_05",
    "remaining_under_15", "remaining_over_15", "p0_market", "p1_market",
    "p2plus_market", "p1_break_even", "p1_buffer", "win_roi",
    "normalizer_version", "strategy_version", "relevant_markets_json",
    "goal_at", "reopen_at", "reopen_delay_seconds", "archive_path",
    "exported_at", "payload_hash",
)


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
        self.connection.execute("PRAGMA busy_timeout = 30000")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA synchronous = NORMAL")
        self.connection.executescript(SCHEMA)
        self._ensure_column("events", "competition_country", "TEXT")
        self._ensure_column("odds_history", "snapshot_id", "INTEGER")
        self._ensure_column("strategy_evaluations", "p_zero", "REAL")
        self._ensure_column("strategy_evaluations", "p_one", "REAL")
        self._ensure_column("strategy_evaluations", "p_two_plus", "REAL")
        self._ensure_column("strategy_evaluations", "trigger_type", "TEXT")
        self._ensure_column("strategy_evaluations", "is_eligible", "INTEGER")
        for column, definition in (
            ("competition_id", "TEXT"),
            ("competition_name", "TEXT"),
            ("competition_country", "TEXT"),
            ("home_team", "TEXT"),
            ("away_team", "TEXT"),
            ("kickoff_time", "TEXT"),
            ("match_minute", "INTEGER"),
            ("q_zero_best", "REAL"),
            ("q_zero_source_type", "TEXT"),
            ("q_zero_market_id", "TEXT"),
            ("q_zero_outcome_id", "TEXT"),
            ("q_two_plus_best", "REAL"),
            ("q_two_plus_source_type", "TEXT"),
            ("q_two_plus_market_id", "TEXT"),
            ("q_two_plus_outcome_id", "TEXT"),
            ("remaining_under_05", "REAL"),
            ("remaining_over_05", "REAL"),
            ("remaining_under_15", "REAL"),
            ("remaining_over_15", "REAL"),
            ("p0_market", "REAL"),
            ("p1_market", "REAL"),
            ("p2plus_market", "REAL"),
            ("p1_break_even", "REAL"),
            ("p1_buffer", "REAL"),
            ("win_roi", "REAL"),
            ("normalizer_version", "TEXT"),
            ("strategy_version", "TEXT"),
            ("relevant_markets_json", "TEXT NOT NULL DEFAULT '[]'"),
            ("goal_at", "TEXT"),
            ("reopen_at", "TEXT"),
            ("reopen_delay_seconds", "REAL"),
            ("archive_path", "TEXT"),
            ("exported_at", "TEXT"),
            ("payload_hash", "TEXT"),
        ):
            self._ensure_column("snapshots", column, definition)
        self._ensure_column("paper_trades", "entry_raw_payload_path", "TEXT")
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_odds_history_snapshot ON odds_history(snapshot_id)"
        )
        self._ensure_snapshot_unique_index()
        self._backfill_competitions()
        self.connection.execute(
            """
            INSERT OR IGNORE INTO paper_runtime_settings
                (setting_key, setting_value, updated_at)
            VALUES ('enabled', '1', ?)
            """,
            (datetime.now(timezone.utc).isoformat(),),
        )
        self.connection.commit()

    def _ensure_snapshot_unique_index(self) -> None:
        """Enforce one standard historical slot per event without deleting legacy rows."""

        standard = (
            "PRE_KICKOFF", "HALFTIME", "HT_STABLE", "MINUTE_60", "MINUTE_70",
            "MINUTE_80", "FIRST_H2_GOAL_REOPEN", "MINUTE_85", "MINUTE_90", "FINAL",
        )
        placeholders = ", ".join("?" for _ in standard)
        duplicate = self.connection.execute(
            f"""
            SELECT 1 FROM snapshots
            WHERE snapshot_type IN ({placeholders})
            GROUP BY event_id, snapshot_type
            HAVING COUNT(*) > 1
            LIMIT 1
            """,
            standard,
        ).fetchone()
        if duplicate is not None:
            # Existing V0.3 databases may contain duplicate legacy-style
            # observations. Keep them for the migration report and let the
            # application-level idempotency guard protect new writes.
            pass
        else:
            self.connection.execute(
                f"""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_snapshots_event_type_unique
                ON snapshots(event_id, snapshot_type)
                WHERE snapshot_type IN (
                    'PRE_KICKOFF', 'HALFTIME', 'HT_STABLE', 'MINUTE_60', 'MINUTE_70',
                    'MINUTE_80', 'FIRST_H2_GOAL_REOPEN', 'MINUTE_85', 'MINUTE_90', 'FINAL'
                )
                """
            )
        # A trigger also protects a database that already contains duplicate
        # legacy rows and therefore cannot accept the unique index yet. It
        # never removes those rows; migration/cleanup remains explicit.
        self.connection.execute(
            """
            CREATE TRIGGER IF NOT EXISTS prevent_duplicate_standard_snapshot
            BEFORE INSERT ON snapshots
            WHEN NEW.snapshot_type IN (
                'PRE_KICKOFF', 'HALFTIME', 'HT_STABLE', 'MINUTE_60', 'MINUTE_70',
                'MINUTE_80', 'FIRST_H2_GOAL_REOPEN', 'MINUTE_85', 'MINUTE_90', 'FINAL'
            )
            AND EXISTS (
                SELECT 1 FROM snapshots
                WHERE event_id = NEW.event_id AND snapshot_type = NEW.snapshot_type
            )
            BEGIN
                SELECT RAISE(ABORT, 'duplicate standard snapshot slot');
            END;
            """
        )

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
            SELECT competition_id, MAX(competition_name), MAX(competition_country),
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
        self.connection.execute(
            """
            UPDATE events
            SET competition_country = (
                SELECT country_or_region
                FROM competitions
                WHERE competitions.competition_id = events.competition_id
            )
            WHERE (competition_country IS NULL OR competition_country = '')
              AND competition_id IS NOT NULL
              AND EXISTS (
                  SELECT 1 FROM competitions
                  WHERE competitions.competition_id = events.competition_id
                    AND country_or_region IS NOT NULL
              )
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
            event.competition_country,
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
                    event_id, competition_id, competition_name, competition_country, sport,
                    home_team_id, home_team, away_team_id, away_team,
                    kickoff_time, status, period, display_time,
                    score_home, score_away, ht_score_home, ht_score_away,
                    bet_markets_count, section_number, red_cards_home,
                    red_cards_away, sport_radar_match_id, bet_genius_id,
                    extra_time, penalties, break_before, clock_data_json,
                    raw_data_json, first_seen_at, last_seen_at, last_updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id) DO UPDATE SET
                    competition_id = excluded.competition_id,
                    competition_name = excluded.competition_name,
                    competition_country = COALESCE(
                        excluded.competition_country, events.competition_country
                    ),
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
                    event.competition_country or region,
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

    def upsert_current_event_state(self, state: EventState) -> bool:
        """Replace the volatile state for an event and report semantic changes."""

        with self._lock:
            previous = self.connection.execute(
                """
                SELECT status, period, display_time, section_number,
                       score_home, score_away, ht_score_home, ht_score_away,
                       red_cards_home, red_cards_away
                FROM current_event_state
                WHERE event_id = ?
                """,
                (str(state.event_id),),
            ).fetchone()
            changed = previous is None or tuple(previous) != state.relevant_key
            with self.connection:
                self.connection.execute(
                    """
                    INSERT INTO current_event_state (
                        event_id, observed_at, status, period, display_time,
                        section_number, score_home, score_away,
                        ht_score_home, ht_score_away, red_cards_home,
                        red_cards_away, raw_state_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(event_id) DO UPDATE SET
                        observed_at = excluded.observed_at,
                        status = excluded.status,
                        period = excluded.period,
                        display_time = excluded.display_time,
                        section_number = excluded.section_number,
                        score_home = excluded.score_home,
                        score_away = excluded.score_away,
                        ht_score_home = excluded.ht_score_home,
                        ht_score_away = excluded.ht_score_away,
                        red_cards_home = excluded.red_cards_home,
                        red_cards_away = excluded.red_cards_away,
                        raw_state_json = excluded.raw_state_json
                    """,
                    (
                        state.event_id, state.observed_at, state.status,
                        state.period, state.display_time, state.section_number,
                        state.score_home, state.score_away, state.ht_score_home,
                        state.ht_score_away, state.red_cards_home,
                        state.red_cards_away, _json(state.raw_state or {}),
                    ),
                )
            return changed

    def current_event_state(self, event_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self.connection.execute(
                "SELECT * FROM current_event_state WHERE event_id = ?",
                (str(event_id),),
            ).fetchone()

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

    def upsert_current_strategy_state(self, values: Mapping[str, Any]) -> dict[str, Any]:
        """Persist current analysis and append only meaningful strategy transitions."""

        event_id = str(values["event_id"])
        strategy_type = str(values["strategy_type"])
        status = str(values.get("status") or "UNKNOWN")
        eligible = bool(values.get("is_eligible", status == "OK"))
        with self._lock:
            previous = self.connection.execute(
                """
                SELECT status, is_eligible, source_zero, source_two_plus,
                       last_transition_type, last_transition_at, last_evaluation_id
                FROM current_strategy_evaluations
                WHERE event_id = ? AND strategy_type = ?
                """,
                (event_id, strategy_type),
            ).fetchone()
            previous_eligible = bool(previous["is_eligible"]) if previous is not None else False
            previous_sources = (
                previous["source_zero"], previous["source_two_plus"]
            ) if previous is not None else (None, None)
            transition: str | None = None
            if eligible and (
                previous is None
                or previous["last_evaluation_id"] is None
            ):
                transition = "FIRST_ELIGIBLE"
            elif eligible and not previous_eligible:
                transition = "ELIGIBLE"
            elif not eligible and previous_eligible:
                transition = "INELIGIBLE"
            elif (
                eligible
                and previous is not None
                and previous_sources
                != (values.get("source_zero"), values.get("source_two_plus"))
            ):
                transition = "BEST_ODDS_SOURCE_CHANGED"

            evaluation_id: int | None = None
            transition_at = str(values.get("observed_at"))
            with self.connection:
                if transition is not None:
                    cursor = self.connection.execute(
                        """
                        INSERT INTO strategy_evaluations (
                            event_id, observed_at, strategy_type, strategy_version,
                            normalizer_version, status, total_stake, q_zero,
                            q_two_plus, source_zero, source_two_plus, stake_zero,
                            stake_two_plus, payout_zero, payout_two_plus,
                            payout_difference, covered_profit, win_roi, p1_max,
                            p1_tipico, p1_buffer, p_zero, p_one, p_two_plus,
                            trigger_type, is_eligible
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                                  ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            event_id, values["observed_at"], strategy_type,
                            values.get("strategy_version") or "",
                            values.get("normalizer_version") or "",
                            status, values.get("total_stake") or 0,
                            values.get("q_zero"), values.get("q_two_plus"),
                            values.get("source_zero"), values.get("source_two_plus"),
                            values.get("stake_zero"), values.get("stake_two_plus"),
                            values.get("payout_zero"), values.get("payout_two_plus"),
                            values.get("payout_difference"), values.get("covered_profit"),
                            values.get("win_roi"), values.get("p1_max"),
                            values.get("p1_tipico"), values.get("p1_buffer"),
                            values.get("p_zero"), values.get("p_one"),
                            values.get("p_two_plus"), transition, int(eligible),
                        ),
                    )
                    evaluation_id = int(cursor.lastrowid)
                self.connection.execute(
                    """
                    INSERT INTO current_strategy_evaluations (
                        event_id, strategy_type, observed_at, strategy_version,
                        normalizer_version, status, total_stake, q_zero, q_two_plus,
                        source_zero, source_two_plus, stake_zero, stake_two_plus,
                        payout_zero, payout_two_plus, payout_difference, covered_profit,
                        win_roi, p1_max, p1_tipico, p1_buffer, p_zero, p_one, p_two_plus,
                        last_transition_type, last_transition_at, last_evaluation_id,
                        updated_at, is_eligible
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                              ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(event_id, strategy_type) DO UPDATE SET
                        observed_at = excluded.observed_at,
                        strategy_version = excluded.strategy_version,
                        normalizer_version = excluded.normalizer_version,
                        status = excluded.status,
                        total_stake = excluded.total_stake,
                        q_zero = excluded.q_zero,
                        q_two_plus = excluded.q_two_plus,
                        source_zero = excluded.source_zero,
                        source_two_plus = excluded.source_two_plus,
                        stake_zero = excluded.stake_zero,
                        stake_two_plus = excluded.stake_two_plus,
                        payout_zero = excluded.payout_zero,
                        payout_two_plus = excluded.payout_two_plus,
                        payout_difference = excluded.payout_difference,
                        covered_profit = excluded.covered_profit,
                        win_roi = excluded.win_roi,
                        p1_max = excluded.p1_max,
                        p1_tipico = excluded.p1_tipico,
                        p1_buffer = excluded.p1_buffer,
                        p_zero = excluded.p_zero,
                        p_one = excluded.p_one,
                        p_two_plus = excluded.p_two_plus,
                        last_transition_type = COALESCE(excluded.last_transition_type,
                                                         current_strategy_evaluations.last_transition_type),
                        last_transition_at = COALESCE(excluded.last_transition_at,
                                                      current_strategy_evaluations.last_transition_at),
                        last_evaluation_id = COALESCE(excluded.last_evaluation_id,
                                                      current_strategy_evaluations.last_evaluation_id),
                        updated_at = excluded.updated_at,
                        is_eligible = excluded.is_eligible
                    """,
                    (
                        event_id, strategy_type, values["observed_at"],
                        values.get("strategy_version") or "",
                        values.get("normalizer_version") or "", status,
                        values.get("total_stake") or 0, values.get("q_zero"),
                        values.get("q_two_plus"), values.get("source_zero"),
                        values.get("source_two_plus"), values.get("stake_zero"),
                        values.get("stake_two_plus"), values.get("payout_zero"),
                        values.get("payout_two_plus"), values.get("payout_difference"),
                        values.get("covered_profit"), values.get("win_roi"),
                        values.get("p1_max"), values.get("p1_tipico"),
                        values.get("p1_buffer"), values.get("p_zero"),
                        values.get("p_one"), values.get("p_two_plus"),
                        transition, transition_at if transition else None,
                        evaluation_id, datetime.now(timezone.utc).isoformat(), int(eligible),
                    ),
                )
            return {
                "transition_type": transition,
                "evaluation_id": evaluation_id,
                "is_eligible": eligible,
            }

    def record_strategy_evaluation_event(
        self,
        values: Mapping[str, Any],
        *,
        trigger_type: str,
        is_eligible: bool,
    ) -> int:
        """Append one explicitly requested strategy audit event and return its ID."""

        columns = (
            "event_id", "observed_at", "strategy_type", "strategy_version",
            "normalizer_version", "status", "total_stake", "q_zero",
            "q_two_plus", "source_zero", "source_two_plus", "stake_zero",
            "stake_two_plus", "payout_zero", "payout_two_plus",
            "payout_difference", "covered_profit", "win_roi", "p1_max",
            "p1_tipico", "p1_buffer", "p_zero", "p_one", "p_two_plus",
            "trigger_type", "is_eligible",
        )
        available = values.keys() if hasattr(values, "keys") else ()

        def value(column: str) -> Any:
            return values[column] if column in available else None

        params = tuple(
            value(column)
            for column in columns[:-2]
        ) + (str(trigger_type), int(bool(is_eligible)))
        with self._lock, self.connection:
            cursor = self.connection.execute(
                f"""
                INSERT INTO strategy_evaluations ({', '.join(columns)})
                VALUES ({', '.join('?' for _ in columns)})
                """,
                params,
            )
            evaluation_id = int(cursor.lastrowid)
        return evaluation_id

    def attach_strategy_evaluation_to_paper_trade(
        self,
        paper_trade_id: str,
        evaluation_id: int,
    ) -> None:
        """Link the audit evaluation created at entry to the immutable trade row."""

        with self._lock, self.connection:
            self.connection.execute(
                """
                UPDATE paper_trades
                SET strategy_evaluation_id = ?
                WHERE paper_trade_id = ?
                """,
                (int(evaluation_id), str(paper_trade_id)),
            )

    def mark_strategy_entry_evaluation(
        self,
        event_id: str,
        strategy_type: str,
        evaluation_id: int,
        observed_at: str,
    ) -> None:
        """Make a paper-entry audit row the current strategy audit anchor."""

        with self._lock, self.connection:
            self.connection.execute(
                """
                UPDATE current_strategy_evaluations
                SET last_transition_type = 'PAPER_TRADE_ENTRY',
                    last_transition_at = ?, last_evaluation_id = ?
                WHERE event_id = ? AND strategy_type = ?
                """,
                (str(observed_at), int(evaluation_id), str(event_id), str(strategy_type)),
            )

    def current_strategy_evaluation_row(
        self,
        event_id: str,
        strategy_type: str = "ZERO_OR_2PLUS",
    ) -> sqlite3.Row | None:
        with self._lock:
            return self.connection.execute(
                "SELECT * FROM current_strategy_evaluations WHERE event_id = ? AND strategy_type = ?",
                (str(event_id), str(strategy_type)),
            ).fetchone()

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
        """Insert one historical slot and return its stable database identifier."""
        with self._lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                snapshot_id = self._insert_snapshot_locked(snapshot)
                self.connection.commit()
            except Exception:
                self.connection.rollback()
                raise
        snapshot.snapshot_id = snapshot_id
        return snapshot_id

    def _insert_snapshot_locked(self, snapshot: Snapshot) -> int:
        """Insert a snapshot while the caller owns the database transaction."""

        existing = self.connection.execute(
            """
            SELECT snapshot_id FROM snapshots
            WHERE event_id = ? AND snapshot_type = ?
            ORDER BY snapshot_id LIMIT 1
            """,
            (str(snapshot.event_id), str(snapshot.snapshot_type)),
        ).fetchone()
        if existing is not None:
            return int(existing["snapshot_id"])
        values = tuple(
            getattr(snapshot, column)
            if column != "relevant_markets_json"
            else (snapshot.relevant_markets_json or "[]")
            for column in SNAPSHOT_COLUMNS
        )
        try:
            cursor = self.connection.execute(
                f"INSERT INTO snapshots ({', '.join(SNAPSHOT_COLUMNS)}) VALUES ({', '.join('?' for _ in SNAPSHOT_COLUMNS)})",
                values,
            )
            return int(cursor.lastrowid)
        except sqlite3.IntegrityError:
            # Another process may have won the same slot between the read and
            # insert. Return its ID so idempotent callers converge.
            existing = self.connection.execute(
                """
                SELECT snapshot_id FROM snapshots
                WHERE event_id = ? AND snapshot_type = ?
                ORDER BY snapshot_id LIMIT 1
                """,
                (str(snapshot.event_id), str(snapshot.snapshot_type)),
            ).fetchone()
            if existing is None:
                raise
            return int(existing["snapshot_id"])

    def enqueue_historical_snapshot(
        self,
        snapshot: Snapshot,
        payload: Mapping[str, Any],
    ) -> tuple[int, bool]:
        """Atomically register a flat snapshot and its Parquet outbox row."""
        with self._lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                snapshot_id = self._insert_snapshot_locked(snapshot)
                result = self._enqueue_snapshot_payload_locked(snapshot_id, payload)
                self.connection.commit()
            except Exception:
                self.connection.rollback()
                raise
        snapshot.snapshot_id = snapshot_id
        return snapshot_id, bool(result)

    def enqueue_snapshot_payload(
        self,
        snapshot_id: int,
        payload: Mapping[str, Any],
    ) -> tuple[int, bool]:
        """Queue an already-created snapshot with its final ID in the payload."""

        with self._lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                created = self._enqueue_snapshot_payload_locked(snapshot_id, payload)
                self.connection.commit()
            except Exception:
                self.connection.rollback()
                raise
        return int(snapshot_id), bool(created)

    def _enqueue_snapshot_payload_locked(
        self,
        snapshot_id: int,
        payload: Mapping[str, Any],
    ) -> bool:
        """Queue payload while the caller owns an IMMEDIATE transaction."""

        payload_dict = dict(payload)
        payload_dict["snapshot_id"] = int(snapshot_id)
        encoded = _json(payload_dict)
        payload_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        snapshot_row = self.connection.execute(
            """
            SELECT event_id, snapshot_type, observed_at, relevant_markets_json
            FROM snapshots WHERE snapshot_id = ?
            """,
            (int(snapshot_id),),
        ).fetchone()
        if snapshot_row is None:
            raise KeyError(f"Unknown snapshot: {snapshot_id}")
        event_id = str(snapshot_row["event_id"])
        snapshot_type = str(snapshot_row["snapshot_type"])
        encoded_captured_at = str(
            payload_dict.get("captured_at") or snapshot_row["observed_at"]
        )
        self.connection.execute(
            """
            UPDATE snapshots
            SET payload_hash = ?, relevant_markets_json = ?
            WHERE snapshot_id = ?
            """,
            (
                payload_hash,
                str(
                    payload_dict.get("relevant_markets_json")
                    or snapshot_row["relevant_markets_json"]
                    or "[]"
                ),
                int(snapshot_id),
            ),
        )
        outbox = self.connection.execute(
            "SELECT snapshot_id FROM snapshot_outbox WHERE snapshot_id = ?",
            (int(snapshot_id),),
        ).fetchone()
        if outbox is not None:
            return False
        self.connection.execute(
            """
            INSERT INTO snapshot_outbox (
                snapshot_id, event_id, snapshot_type, captured_at,
                payload_json, payload_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(snapshot_id),
                event_id,
                snapshot_type,
                encoded_captured_at,
                encoded,
                payload_hash,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        return True

    def snapshot_exists(self, event_id: str, snapshot_type: str) -> bool:
        with self._lock:
            row = self.connection.execute(
                "SELECT 1 FROM snapshots WHERE event_id = ? AND snapshot_type = ? LIMIT 1",
                (str(event_id), str(snapshot_type)),
            ).fetchone()
        return row is not None

    def pending_snapshot_outbox(self, limit: int = 100) -> list[sqlite3.Row]:
        with self._lock:
            return list(
                self.connection.execute(
                    """
                    SELECT * FROM snapshot_outbox
                    WHERE exported = 0
                    ORDER BY captured_at, snapshot_id
                    LIMIT ?
                    """,
                    (max(1, int(limit)),),
                ).fetchall()
            )

    def mark_snapshot_outbox_error(self, snapshot_ids: Iterable[int], error: str) -> None:
        ids = [int(item) for item in snapshot_ids]
        if not ids:
            return
        placeholders = ", ".join("?" for _ in ids)
        with self._lock, self.connection:
            self.connection.execute(
                f"""
                UPDATE snapshot_outbox
                SET attempts = attempts + 1, last_error = ?
                WHERE snapshot_id IN ({placeholders})
                """,
                [str(error), *ids],
            )

    def mark_snapshots_exported(
        self,
        snapshot_ids: Iterable[int],
        archive_path: str,
        exported_at: str,
    ) -> int:
        ids = [int(item) for item in snapshot_ids]
        if not ids:
            return 0
        placeholders = ", ".join("?" for _ in ids)
        with self._lock:
            with self.connection:
                self.connection.execute(
                    f"""
                    UPDATE snapshots
                    SET archive_path = ?, exported_at = ?
                    WHERE snapshot_id IN ({placeholders})
                    """,
                    [str(archive_path), str(exported_at), *ids],
                )
                cursor = self.connection.execute(
                    f"""
                    UPDATE snapshot_outbox
                    SET exported = 1, exported_at = ?, last_error = NULL
                    WHERE snapshot_id IN ({placeholders})
                    """,
                    [str(exported_at), *ids],
                )
        return int(cursor.rowcount)

    def delete_exported_snapshot_outbox(self, *, before: str | None = None) -> int:
        clause = "WHERE exported = 1"
        params: list[Any] = []
        if before:
            clause += " AND exported_at <= ?"
            params.append(str(before))
        with self._lock, self.connection:
            cursor = self.connection.execute(
                f"DELETE FROM snapshot_outbox {clause}", params
            )
        return int(cursor.rowcount)

    def snapshot_archive_rows(self, *, date_text: str | None = None) -> list[sqlite3.Row]:
        clauses = ["exported_at IS NOT NULL"]
        params: list[Any] = []
        if date_text:
            clauses.append("substr(observed_at, 1, 10) = ?")
            params.append(str(date_text))
        with self._lock:
            return list(
                self.connection.execute(
                    "SELECT * FROM snapshots WHERE " + " AND ".join(clauses)
                    + " ORDER BY observed_at, snapshot_id",
                    params,
                ).fetchall()
            )

    def upsert_match_result(self, values: Mapping[str, Any]) -> sqlite3.Row:
        columns = (
            "event_id", "competition_id", "competition_name", "competition_country",
            "home_team", "away_team", "kickoff_at", "ht_home", "ht_away",
            "ft_home", "ft_away", "first_half_goals", "second_half_goals",
            "second_half_goal_class", "final_status", "finished_at", "extra_time",
            "penalties",
        )
        params = tuple(values.get(column) for column in columns)
        with self._lock:
            with self.connection:
                self.connection.execute(
                    f"""
                    INSERT INTO match_results ({', '.join(columns)})
                    VALUES ({', '.join('?' for _ in columns)})
                    ON CONFLICT(event_id) DO UPDATE SET
                        competition_id = excluded.competition_id,
                        competition_name = excluded.competition_name,
                        competition_country = excluded.competition_country,
                        home_team = excluded.home_team,
                        away_team = excluded.away_team,
                        kickoff_at = excluded.kickoff_at,
                        ht_home = excluded.ht_home,
                        ht_away = excluded.ht_away,
                        ft_home = excluded.ft_home,
                        ft_away = excluded.ft_away,
                        first_half_goals = excluded.first_half_goals,
                        second_half_goals = excluded.second_half_goals,
                        second_half_goal_class = excluded.second_half_goal_class,
                        final_status = excluded.final_status,
                        finished_at = excluded.finished_at,
                        extra_time = excluded.extra_time,
                        penalties = excluded.penalties
                    """,
                    params,
                )
            row = self.connection.execute(
                "SELECT * FROM match_results WHERE event_id = ?",
                (str(values["event_id"]),),
            ).fetchone()
        if row is None:
            raise RuntimeError("Match result was not persisted")
        return row

    def match_result_for_event(self, event_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self.connection.execute(
                "SELECT * FROM match_results WHERE event_id = ?",
                (str(event_id),),
            ).fetchone()

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

    def replace_current_canonical_outcomes(
        self,
        outcomes: list[Any],
        *,
        event_id: str | None = None,
    ) -> int:
        """Replace one event's volatile normalized market view in one transaction."""

        grouped: dict[str, list[Any]] = {}
        for outcome in outcomes:
            grouped.setdefault(str(outcome.event_id), []).append(outcome)
        if not grouped and event_id is not None:
            with self._lock, self.connection:
                self.connection.execute(
                    "DELETE FROM current_canonical_outcomes WHERE event_id = ?",
                    (str(event_id),),
                )
            return 0
        if not grouped:
            return 0
        columns = (
            "event_id", "market_id", "outcome_id", "observed_at", "canonical_type",
            "scope", "period", "side", "line", "team", "odds", "status",
            "available", "raw_market_type", "raw_market_caption", "raw_fixed_param",
            "raw_choice_param", "raw_outcome_caption", "settlement_scope",
            "normalizer_version", "updated_at",
        )
        with self._lock:
            with self.connection:
                for event_id, event_outcomes in grouped.items():
                    self.connection.execute(
                        "DELETE FROM current_canonical_outcomes WHERE event_id = ?",
                        (event_id,),
                    )
                    now = datetime.now(timezone.utc).isoformat()
                    for outcome in event_outcomes:
                        self.connection.execute(
                            f"""
                            INSERT INTO current_canonical_outcomes ({', '.join(columns)})
                            VALUES ({', '.join('?' for _ in columns)})
                            """,
                            (
                                str(outcome.event_id), str(outcome.market_id),
                                str(outcome.outcome_id), str(outcome.observed_at),
                                str(outcome.canonical_type), str(outcome.scope),
                                str(outcome.period), outcome.side, outcome.line,
                                outcome.team, outcome.odds, str(outcome.status),
                                int(bool(outcome.available)), str(outcome.raw_market_type),
                                str(outcome.raw_market_caption), str(outcome.raw_fixed_param),
                                outcome.raw_choice_param, str(outcome.raw_outcome_caption),
                                str(outcome.settlement_scope), str(outcome.normalizer_version),
                                now,
                            ),
                        )
        return sum(len(items) for items in grouped.values())

    def current_canonical_quotes_for_evaluation(
        self,
        event_id: str,
        canonical_types: Iterable[str],
    ) -> list[sqlite3.Row]:
        types = [str(item) for item in canonical_types]
        if not types:
            return []
        placeholders = ", ".join("?" for _ in types)
        with self._lock:
            return list(
                self.connection.execute(
                    f"""
                    SELECT * FROM current_canonical_outcomes
                    WHERE event_id = ? AND canonical_type IN ({placeholders})
                      AND available = 1 AND odds IS NOT NULL
                      AND lower(status) NOT IN
                          ('paused', 'suspended', 'stopped', 'closed', 'inactive')
                    ORDER BY odds ASC, outcome_id DESC
                    """,
                    [str(event_id), *types],
                ).fetchall()
            )

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
        p_zero: float | None = None,
        p_one: float | None = None,
        p_two_plus: float | None = None,
        trigger_type: str | None = None,
        is_eligible: bool | None = None,
        force: bool = False,
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
            if not force and previous is not None and tuple(previous) == signature:
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
                        p1_tipico, p1_buffer, p_zero, p_one, p_two_plus,
                        trigger_type, is_eligible
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                              ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        p_zero,
                        p_one,
                        p_two_plus,
                        trigger_type,
                        None if is_eligible is None else int(bool(is_eligible)),
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
        total = 0
        for candidate in (
            self.path,
            Path(str(self.path) + "-wal"),
            Path(str(self.path) + "-shm"),
        ):
            try:
                total += int(candidate.stat().st_size)
            except OSError:
                pass
        return total

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

    def recent_strategy_evaluations(
        self,
        *,
        strategy_type: str = "ZERO_OR_2PLUS",
        since: str | None = None,
        limit: int = 500,
    ) -> list[sqlite3.Row]:
        """Return current analysis first, with legacy transitions as fallback."""

        current_clauses = ["cs.strategy_type = ?"]
        legacy_clauses = ["se.strategy_type = ?"]
        current_params: list[Any] = [str(strategy_type)]
        legacy_params: list[Any] = [str(strategy_type)]
        if since:
            current_clauses.append("cs.observed_at >= ?")
            current_params.append(str(since))
            legacy_clauses.append("se.observed_at >= ?")
            legacy_params.append(str(since))
        query = (
            """
            SELECT cs.last_evaluation_id AS evaluation_id,
                   cs.event_id, cs.observed_at, cs.strategy_type,
                   cs.strategy_version, cs.normalizer_version, cs.status,
                   cs.total_stake, cs.q_zero, cs.q_two_plus, cs.source_zero,
                   cs.source_two_plus, cs.stake_zero, cs.stake_two_plus,
                   cs.payout_zero, cs.payout_two_plus, cs.payout_difference,
                   cs.covered_profit, cs.win_roi, cs.p1_max, cs.p1_tipico,
                   cs.p1_buffer, cs.p_zero, cs.p_one, cs.p_two_plus,
                   cs.last_transition_type AS trigger_type,
                   cs.is_eligible, e.competition_id, e.competition_name,
                   e.competition_country, e.home_team, e.away_team,
                   e.ht_score_home AS event_ht_score_home,
                   e.ht_score_away AS event_ht_score_away,
                   e.sport, e.extra_time, e.penalties
            FROM current_strategy_evaluations cs
            LEFT JOIN events e ON e.event_id = cs.event_id
            WHERE """
            + " AND ".join(current_clauses)
            + """
            UNION ALL
            SELECT se.evaluation_id, se.event_id, se.observed_at,
                   se.strategy_type, se.strategy_version, se.normalizer_version,
                   se.status, se.total_stake, se.q_zero, se.q_two_plus,
                   se.source_zero, se.source_two_plus, se.stake_zero,
                   se.stake_two_plus, se.payout_zero, se.payout_two_plus,
                   se.payout_difference, se.covered_profit, se.win_roi,
                   se.p1_max, se.p1_tipico, se.p1_buffer, se.p_zero,
                   se.p_one, se.p_two_plus, se.trigger_type, se.is_eligible,
                   e.competition_id, e.competition_name, e.competition_country,
                   e.home_team, e.away_team, e.ht_score_home AS event_ht_score_home,
                   e.ht_score_away AS event_ht_score_away, e.sport, e.extra_time,
                   e.penalties
            FROM strategy_evaluations se
            LEFT JOIN events e ON e.event_id = se.event_id
            WHERE """
            + " AND ".join(legacy_clauses)
            + """
              AND NOT EXISTS (
                  SELECT 1 FROM current_strategy_evaluations cs2
                  WHERE cs2.event_id = se.event_id
                    AND cs2.strategy_type = se.strategy_type
              )
            ORDER BY observed_at DESC, evaluation_id DESC LIMIT ?
        """
        )
        with self._lock:
            return list(
                self.connection.execute(
                    query,
                    [*current_params, *legacy_params, max(1, int(limit))],
                ).fetchall()
            )

    def canonical_quotes_for_evaluation(
        self,
        event_id: str,
        observed_at: str,
        canonical_types: Iterable[str],
    ) -> list[sqlite3.Row]:
        types = [str(item) for item in canonical_types]
        if not types:
            return []
        placeholders = ", ".join("?" for _ in types)
        with self._lock:
            return list(
                self.connection.execute(
                    f"""
                    SELECT * FROM canonical_outcomes
                    WHERE event_id = ? AND observed_at = ?
                      AND canonical_type IN ({placeholders})
                      AND available = 1 AND odds IS NOT NULL
                    ORDER BY odds ASC, canonical_id DESC
                    """,
                    [str(event_id), str(observed_at), *types],
                ).fetchall()
            )

    def first_halftime_observed_at(self, event_id: str) -> str | None:
        with self._lock:
            row = self.connection.execute(
                """
                SELECT MIN(observed_at) AS observed_at
                FROM snapshots
                WHERE event_id = ? AND snapshot_type = 'HALFTIME'
                """,
                (str(event_id),),
            ).fetchone()
            if row and row["observed_at"]:
                return str(row["observed_at"])
            row = self.connection.execute(
                """
                SELECT observed_at
                FROM current_event_state
                WHERE event_id = ?
                  AND (upper(period) IN ('HALF_TIME', 'HALFTIME', 'HT')
                       OR upper(display_time) = 'HZ')
                LIMIT 1
                """,
                (str(event_id),),
            ).fetchone()
            if row and row["observed_at"]:
                return str(row["observed_at"])
            row = self.connection.execute(
                """
                SELECT MIN(observed_at) AS observed_at
                FROM event_states
                WHERE event_id = ?
                  AND (upper(period) IN ('HALF_TIME', 'HALFTIME', 'HT')
                       OR upper(display_time) = 'HZ')
                """,
                (str(event_id),),
            ).fetchone()
        return str(row["observed_at"]) if row and row["observed_at"] else None

    def final_snapshot_for_event(self, event_id: str) -> sqlite3.Row | None:
        """Return the best persisted final result, never a disappeared live state."""

        with self._lock:
            return self.connection.execute(
                """
                SELECT * FROM snapshots
                WHERE event_id = ?
                  AND snapshot_type = 'FINAL'
                  AND score_home IS NOT NULL AND score_away IS NOT NULL
                  AND COALESCE(snapshot_quality, '') NOT IN ('FAILED')
                ORDER BY observed_at DESC, snapshot_id DESC
                LIMIT 1
                """,
                (str(event_id),),
            ).fetchone()

    def list_competitions(self, limit: int = 1000) -> list[sqlite3.Row]:
        with self._lock:
            return list(
                self.connection.execute(
                    """
                    SELECT competition_id, competition_name, country_or_region,
                           first_seen_at, last_seen_at, events_observed
                    FROM competitions
                    ORDER BY competition_name COLLATE NOCASE,
                             country_or_region COLLATE NOCASE, competition_id
                    LIMIT ?
                    """,
                    (max(1, int(limit)),),
                ).fetchall()
            )

    def update_event_competition_country(
        self,
        event_id: str,
        country_or_region: str,
        *,
        observed_at: str | None = None,
    ) -> bool:
        """Backfill country metadata without changing the event snapshot."""

        country = str(country_or_region).strip()
        if not country:
            return False
        with self._lock:
            with self.connection:
                cursor = self.connection.execute(
                    """
                    UPDATE events
                    SET competition_country = ?
                    WHERE event_id = ?
                      AND COALESCE(competition_country, '') != ?
                    """,
                    (country, str(event_id), country),
                )
                row = self.connection.execute(
                    """
                    SELECT competition_id, competition_name, first_seen_at, last_seen_at
                    FROM events WHERE event_id = ?
                    """,
                    (str(event_id),),
                ).fetchone()
                if row and row["competition_id"]:
                    stamp = observed_at or str(row["last_seen_at"] or row["first_seen_at"])
                    self._upsert_competition_in_transaction(
                        str(row["competition_id"]),
                        str(row["competition_name"] or row["competition_id"]),
                        country,
                        str(row["first_seen_at"] or stamp),
                        stamp,
                    )
            return cursor.rowcount > 0

    def paper_portfolio_rows(self, *, include_archived: bool = True) -> list[sqlite3.Row]:
        clause = "" if include_archived else " WHERE status != 'ARCHIVED'"
        with self._lock:
            return list(
                self.connection.execute(
                    "SELECT * FROM paper_portfolios" + clause + " ORDER BY created_at, portfolio_id"
                ).fetchall()
            )

    def paper_portfolio_row(self, portfolio_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self.connection.execute(
                "SELECT * FROM paper_portfolios WHERE portfolio_id = ?",
                (str(portfolio_id),),
            ).fetchone()

    def paper_portfolio_competition_ids(self, portfolio_id: str) -> tuple[str, ...]:
        with self._lock:
            rows = self.connection.execute(
                """
                SELECT competition_id FROM paper_portfolio_competitions
                WHERE portfolio_id = ? ORDER BY competition_id
                """,
                (str(portfolio_id),),
            ).fetchall()
        return tuple(str(row["competition_id"]) for row in rows)

    def insert_paper_portfolio(
        self,
        values: Mapping[str, Any],
        competition_ids: Iterable[str] = (),
    ) -> sqlite3.Row:
        columns = (
            "portfolio_id", "name", "created_at", "updated_at", "starting_bankroll",
            "currency", "strategy_type", "stake_mode", "fixed_stake",
            "bankroll_percentage", "min_stake", "max_stake", "minimum_win_roi",
            "minimum_p1_buffer", "maximum_tipico_p1", "minimum_q_zero",
            "minimum_q_two_plus", "max_quote_age_seconds", "entry_window_start_seconds",
            "entry_window_end_seconds", "allow_all_competitions", "status", "version",
        )
        params = tuple(values.get(column) for column in columns)
        with self._lock:
            with self.connection:
                self.connection.execute(
                    f"INSERT INTO paper_portfolios ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                    params,
                )
                for competition_id in competition_ids:
                    self.connection.execute(
                        """
                        INSERT OR IGNORE INTO paper_portfolio_competitions
                            (portfolio_id, competition_id) VALUES (?, ?)
                        """,
                        (str(values["portfolio_id"]), str(competition_id)),
                    )
            row = self.connection.execute(
                "SELECT * FROM paper_portfolios WHERE portfolio_id = ?",
                (str(values["portfolio_id"]),),
            ).fetchone()
        if row is None:
            raise RuntimeError("Paper portfolio was not persisted")
        return row

    def update_paper_portfolio(
        self,
        portfolio_id: str,
        values: Mapping[str, Any],
        competition_ids: Iterable[str] | None = None,
    ) -> sqlite3.Row | None:
        allowed = {
            "name", "updated_at", "starting_bankroll", "currency", "strategy_type",
            "stake_mode", "fixed_stake", "bankroll_percentage", "min_stake", "max_stake",
            "minimum_win_roi", "minimum_p1_buffer", "maximum_tipico_p1", "minimum_q_zero",
            "minimum_q_two_plus", "max_quote_age_seconds", "entry_window_start_seconds",
            "entry_window_end_seconds", "allow_all_competitions", "status", "version",
        }
        assignments = [(key, values[key]) for key in values if key in allowed]
        if not assignments and competition_ids is None:
            return self.paper_portfolio_row(portfolio_id)
        with self._lock:
            with self.connection:
                if assignments:
                    self.connection.execute(
                        "UPDATE paper_portfolios SET "
                        + ", ".join(f"{key} = ?" for key, _ in assignments)
                        + " WHERE portfolio_id = ?",
                        [value for _, value in assignments] + [str(portfolio_id)],
                    )
                if competition_ids is not None:
                    self.connection.execute(
                        "DELETE FROM paper_portfolio_competitions WHERE portfolio_id = ?",
                        (str(portfolio_id),),
                    )
                    for competition_id in competition_ids:
                        self.connection.execute(
                            """
                            INSERT OR IGNORE INTO paper_portfolio_competitions
                                (portfolio_id, competition_id) VALUES (?, ?)
                            """,
                            (str(portfolio_id), str(competition_id)),
                        )
            return self.connection.execute(
                "SELECT * FROM paper_portfolios WHERE portfolio_id = ?",
                (str(portfolio_id),),
            ).fetchone()

    def set_paper_runtime_setting(self, key: str, value: str, updated_at: str) -> None:
        with self._lock, self.connection:
            self.connection.execute(
                """
                INSERT INTO paper_runtime_settings (setting_key, setting_value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(setting_key) DO UPDATE SET
                    setting_value = excluded.setting_value,
                    updated_at = excluded.updated_at
                """,
                (str(key), str(value), str(updated_at)),
            )

    def get_paper_runtime_setting(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self.connection.execute(
                "SELECT setting_value FROM paper_runtime_settings WHERE setting_key = ?",
                (str(key),),
            ).fetchone()
        return str(row["setting_value"]) if row else default

    def paper_balance(self, portfolio_id: str) -> float:
        with self._lock:
            row = self.connection.execute(
                """
                SELECT p.starting_bankroll,
                       COALESCE(SUM(t.amount), 0) AS ledger_delta
                FROM paper_portfolios p
                LEFT JOIN paper_bankroll_transactions t
                  ON t.portfolio_id = p.portfolio_id
                WHERE p.portfolio_id = ?
                GROUP BY p.portfolio_id
                """,
                (str(portfolio_id),),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown paper portfolio: {portfolio_id}")
        return float(row["starting_bankroll"] or 0) + float(row["ledger_delta"] or 0)

    def paper_trade_rows(
        self,
        portfolio_id: str | None = None,
        *,
        status: str | None = None,
        limit: int = 500,
    ) -> list[sqlite3.Row]:
        clauses: list[str] = []
        params: list[Any] = []
        if portfolio_id:
            clauses.append("portfolio_id = ?")
            params.append(str(portfolio_id))
        if status:
            clauses.append("status = ?")
            params.append(str(status))
        params.append(max(1, int(limit)))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._lock:
            return list(
                self.connection.execute(
                    "SELECT * FROM paper_trades" + where
                    + " ORDER BY created_at DESC, paper_trade_id DESC LIMIT ?",
                    params,
                ).fetchall()
            )

    def paper_trade_row(self, paper_trade_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self.connection.execute(
                "SELECT * FROM paper_trades WHERE paper_trade_id = ?",
                (str(paper_trade_id),),
            ).fetchone()

    def log_paper_signal(self, values: Mapping[str, Any]) -> None:
        with self._lock, self.connection:
            self.connection.execute(
                """
                INSERT OR IGNORE INTO paper_signal_log (
                    portfolio_id, event_id, evaluation_id, observed_at,
                    decision, reason, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(values["portfolio_id"]), str(values["event_id"]),
                    values.get("evaluation_id"), str(values["observed_at"]),
                    str(values["decision"]), str(values["reason"]),
                    _json(values.get("details", {})),
                ),
            )

    def reserve_paper_trade(self, snapshot: Mapping[str, Any]) -> tuple[bool, sqlite3.Row | None, str]:
        """Atomically reserve bankroll and create one immutable entry snapshot."""

        portfolio_id = str(snapshot["portfolio_id"])
        event_id = str(snapshot["event_id"])
        strategy_type = str(snapshot["strategy_type"])
        stake = float(snapshot["stake_total"])
        with self._lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                existing = self.connection.execute(
                    """
                    SELECT * FROM paper_trades
                    WHERE portfolio_id = ? AND event_id = ? AND strategy_type = ?
                    LIMIT 1
                    """,
                    (portfolio_id, event_id, strategy_type),
                ).fetchone()
                if existing is not None:
                    self.connection.rollback()
                    return False, existing, "ALREADY_ENTERED"
                portfolio = self.connection.execute(
                    "SELECT status FROM paper_portfolios WHERE portfolio_id = ?",
                    (portfolio_id,),
                ).fetchone()
                if portfolio is None:
                    self.connection.rollback()
                    return False, None, "UNKNOWN_PORTFOLIO"
                if str(portfolio["status"]).upper() != "ACTIVE":
                    self.connection.rollback()
                    return False, None, "PORTFOLIO_NOT_ACTIVE"
                balance_row = self.connection.execute(
                    """
                    SELECT p.starting_bankroll + COALESCE(SUM(t.amount), 0) AS balance
                    FROM paper_portfolios p
                    LEFT JOIN paper_bankroll_transactions t
                      ON t.portfolio_id = p.portfolio_id
                    WHERE p.portfolio_id = ? GROUP BY p.portfolio_id
                    """,
                    (portfolio_id,),
                ).fetchone()
                balance_before = float(balance_row["balance"] or 0) if balance_row else 0.0
                if stake <= 0 or stake > balance_before + 1e-9:
                    self.connection.rollback()
                    return False, None, "INSUFFICIENT_BANKROLL"
                balance_after = balance_before - stake
                trade_columns = (
                    "paper_trade_id", "portfolio_id", "event_id", "competition_id",
                    "competition_name", "competition_country", "created_at",
                    "strategy_evaluation_id", "strategy_type", "strategy_version",
                    "normalizer_version", "home_team", "away_team", "ht_score_home",
                    "ht_score_away", "zero_market_id", "zero_outcome_id",
                    "zero_market_type", "zero_market_caption", "zero_outcome_caption",
                    "q_zero", "zero_quote_observed_at", "zero_quote_age_seconds",
                    "two_plus_market_id", "two_plus_outcome_id", "two_plus_market_type",
                    "two_plus_market_caption", "two_plus_outcome_caption", "q_two_plus",
                    "two_plus_quote_observed_at", "two_plus_quote_age_seconds", "stake_total",
                    "stake_zero", "stake_two_plus", "payout_zero", "payout_two_plus",
                    "p_zero", "p_one", "p_two_plus", "p1_max", "p1_tipico", "p1_buffer",
                    "win_roi", "entry_raw_payload_path", "bankroll_before", "bankroll_after", "rank", "status",
                    "entry_snapshot_json",
                )
                trade_params = tuple(snapshot.get(column) for column in trade_columns[:-1]) + (
                    _json(snapshot.get("entry_snapshot", dict(snapshot))),
                )
                self.connection.execute(
                    f"INSERT INTO paper_trades ({', '.join(trade_columns)}) VALUES ({', '.join('?' for _ in trade_columns)})",
                    trade_params,
                )
                self.connection.execute(
                    """
                    INSERT INTO paper_bankroll_transactions (
                        transaction_id, portfolio_id, paper_trade_id, created_at,
                        transaction_type, amount, balance_before, balance_after,
                        idempotency_key, note
                    ) VALUES (?, ?, ?, ?, 'STAKE_RESERVED', ?, ?, ?, ?, ?)
                    """,
                    (
                        str(snapshot["reservation_transaction_id"]), portfolio_id,
                        str(snapshot["paper_trade_id"]), str(snapshot["created_at"]),
                        -stake, balance_before, balance_after,
                        str(snapshot["reservation_idempotency_key"]),
                        "Paper-Trade Einsatz reserviert",
                    ),
                )
                self.connection.commit()
            except Exception:
                self.connection.rollback()
                raise
            row = self.connection.execute(
                "SELECT * FROM paper_trades WHERE paper_trade_id = ?",
                (str(snapshot["paper_trade_id"]),),
            ).fetchone()
        return True, row, "CREATED"

    def settle_paper_trade(
        self,
        paper_trade_id: str,
        settlement: Mapping[str, Any],
    ) -> tuple[bool, sqlite3.Row | None]:
        """Settle once; settlement columns are the only mutable trade fields."""

        trade_id = str(paper_trade_id)
        status = str(settlement["status"])
        with self._lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                trade = self.connection.execute(
                    "SELECT * FROM paper_trades WHERE paper_trade_id = ?",
                    (trade_id,),
                ).fetchone()
                if trade is None:
                    self.connection.rollback()
                    return False, None
                if str(trade["status"]).upper() != "OPEN":
                    self.connection.rollback()
                    return False, trade
                stake = float(trade["stake_total"] or 0)
                return_amount = float(settlement.get("return_amount") or 0)
                release = stake if status in {"VOID", "UNRESOLVED"} else 0.0
                ledger_amount = return_amount + release
                balance_row = self.connection.execute(
                    """
                    SELECT p.starting_bankroll + COALESCE(SUM(t.amount), 0) AS balance
                    FROM paper_portfolios p
                    LEFT JOIN paper_bankroll_transactions t
                      ON t.portfolio_id = p.portfolio_id
                    WHERE p.portfolio_id = ? GROUP BY p.portfolio_id
                    """,
                    (str(trade["portfolio_id"]),),
                ).fetchone()
                balance_before = float(balance_row["balance"] or 0) if balance_row else 0.0
                balance_after = balance_before + ledger_amount
                self.connection.execute(
                    """
                    INSERT INTO paper_bankroll_transactions (
                        transaction_id, portfolio_id, paper_trade_id, created_at,
                        transaction_type, amount, balance_before, balance_after,
                        idempotency_key, note
                    ) VALUES (?, ?, ?, ?, 'TRADE_SETTLED', ?, ?, ?, ?, ?)
                    """,
                    (
                        str(settlement["transaction_id"]), str(trade["portfolio_id"]),
                        trade_id, str(settlement["settled_at"]), ledger_amount,
                        balance_before, balance_after, str(settlement["idempotency_key"]),
                        str(settlement.get("note") or "Paper-Trade abgerechnet"),
                    ),
                )
                self.connection.execute(
                    """
                    UPDATE paper_trades SET
                        status = ?, settled_at = ?, final_score_home = ?,
                        final_score_away = ?, second_half_goals = ?,
                        settlement_reason = ?, return_amount = ?, pnl = ?
                    WHERE paper_trade_id = ? AND status = 'OPEN'
                    """,
                    (
                        status, str(settlement["settled_at"]),
                        settlement.get("final_score_home"), settlement.get("final_score_away"),
                        settlement.get("second_half_goals"), str(settlement.get("reason") or ""),
                        return_amount, float(settlement.get("pnl") or 0), trade_id,
                    ),
                )
                self.connection.commit()
            except Exception:
                self.connection.rollback()
                raise
            row = self.connection.execute(
                "SELECT * FROM paper_trades WHERE paper_trade_id = ?", (trade_id,)
            ).fetchone()
        return True, row

    def invalidate_paper_trade(
        self,
        paper_trade_id: str,
        *,
        reason: str,
        settled_at: str,
        transaction_id: str,
        idempotency_key: str,
    ) -> tuple[bool, sqlite3.Row | None]:
        return self.settle_paper_trade(
            paper_trade_id,
            {
                "status": "VOID",
                "settled_at": settled_at,
                "final_score_home": None,
                "final_score_away": None,
                "second_half_goals": None,
                "reason": reason,
                "return_amount": 0.0,
                "pnl": 0.0,
                "transaction_id": transaction_id,
                "idempotency_key": idempotency_key,
                "note": "Trade manuell invalidiert; Einsatz freigegeben",
            },
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
            current = self.connection.execute(
                "SELECT * FROM current_event_state WHERE event_id = ?",
                (str(event_id),),
            ).fetchone()
            if current is not None:
                return current
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
            "current_event_state",
            "markets",
            "outcomes",
            "odds_history",
            "competitions",
            "snapshots",
            "snapshot_outbox",
            "match_results",
            "market_presence",
            "canonical_outcomes",
            "current_canonical_outcomes",
            "strategy_evaluations",
            "current_strategy_evaluations",
            "paper_portfolios",
            "paper_portfolio_competitions",
            "paper_trades",
            "paper_bankroll_transactions",
            "paper_signal_log",
            "paper_runtime_settings",
            "paper_worker_runs",
            "matches",
            "match_provider_links",
            "teams",
            "team_provider_aliases",
            "competition_provider_aliases",
            "competition_provider_links",
            "fotmob_current_state",
            "fotmob_snapshots",
            "fotmob_snapshot_outbox",
            "match_data_quality",
            "fotmob_seasons",
            "fotmob_match_index",
            "fotmob_history_samples",
            "fotmob_historical_archive_index",
            "fotmob_fixture_index_runs",
            "fotmob_daily_index",
            "fotmob_daily_load_runs",
        }
        if table not in allowed:
            raise ValueError(f"Unsupported table: {table}")
        with self._lock:
            row = self.connection.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
            return int(row["count"]) if row else 0

    def collection_metrics_for_date(self, date_text: str | None = None) -> dict[str, Any]:
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
                SELECT COUNT(DISTINCT event_id) AS count
                FROM snapshots
                WHERE substr(observed_at, 1, 10) = ?
                  AND snapshot_type IN ('MINUTE_60', 'MINUTE_70', 'MINUTE_80',
                                        'MINUTE_85', 'MINUTE_90')
                  AND COALESCE(snapshot_quality, '') != 'FAILED'
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
            matches_today = self.connection.execute(
                "SELECT COUNT(*) AS count FROM match_results WHERE substr(finished_at, 1, 10) = ?",
                (day,),
            ).fetchone()
            paper_today = self.connection.execute(
                "SELECT COUNT(*) AS count FROM paper_trades WHERE substr(created_at, 1, 10) = ?",
                (day,),
            ).fetchone()
            standard_types = (
                "PRE_KICKOFF", "HALFTIME", "HT_STABLE", "MINUTE_60", "MINUTE_70",
                "MINUTE_80", "FIRST_H2_GOAL_REOPEN", "MINUTE_85", "MINUTE_90", "FINAL",
            )
            standard_placeholders = ", ".join("?" for _ in standard_types)
            total_snapshots = self.connection.execute(
                f"""
                SELECT COUNT(*) AS count FROM snapshots
                WHERE substr(observed_at, 1, 10) = ?
                  AND snapshot_type IN ({standard_placeholders})
                """,
                [day, *standard_types],
            ).fetchone()
            finished_snapshot_total = self.connection.execute(
                f"""
                SELECT COUNT(*) AS count
                FROM snapshots s JOIN match_results r ON r.event_id = s.event_id
                WHERE substr(r.finished_at, 1, 10) = ?
                  AND s.snapshot_type IN ({standard_placeholders})
                """,
                [day, *standard_types],
            ).fetchone()

        snapshot_counts = {str(row["snapshot_type"]): int(row["count"]) for row in snapshot_rows}
        coverage = {str(row["snapshot_type"]): int(row["count"]) for row in coverage_rows}
        return {
            "date": day,
            "football_events_seen": int(event_row["count"]) if event_row else 0,
            "competitions": int(competition_row["count"]) if competition_row else 0,
            "prematch_snapshots": snapshot_counts.get("PREMATCH", 0),
            "pre_kickoff_snapshots": snapshot_counts.get("PRE_KICKOFF", 0),
            "halftime_snapshots": snapshot_counts.get("HALFTIME", 0),
            "ht_stable_snapshots": snapshot_counts.get("HT_STABLE", 0),
            "minute_60_snapshots": snapshot_counts.get("MINUTE_60", 0),
            "minute_70_snapshots": snapshot_counts.get("MINUTE_70", 0),
            "minute_80_snapshots": snapshot_counts.get("MINUTE_80", 0),
            "goal_reopen_snapshots": snapshot_counts.get("FIRST_H2_GOAL_REOPEN", 0),
            "minute_85_snapshots": snapshot_counts.get("MINUTE_85", 0),
            "minute_90_snapshots": snapshot_counts.get("MINUTE_90", 0),
            "periodic_snapshots": sum(
                snapshot_counts.get(f"MINUTE_{minute}", 0)
                for minute in (60, 70, 80, 85, 90)
            ),
            "goal_triggers": snapshot_counts.get("FIRST_H2_GOAL_REOPEN", 0),
            "final_snapshots": snapshot_counts.get("FINAL", 0),
            "failed_snapshots": int(failed_snapshots["count"]) if failed_snapshots else 0,
            "events_with_prematch_snapshot": int(prematch_events["count"])
            if prematch_events
            else 0,
            "events_with_halftime_snapshot": coverage.get("HALFTIME", 0),
            "events_with_final_result": int(matches_today["count"]) if matches_today else 0,
            "events_with_core_live_tracking": int(core_events["count"]) if core_events else 0,
            "snapshots_today": int(total_snapshots["count"]) if total_snapshots else 0,
            "matches_today": int(matches_today["count"]) if matches_today else 0,
            "paper_trades_today": int(paper_today["count"]) if paper_today else 0,
            "average_snapshots_per_finished_match": (
                float(finished_snapshot_total["count"])
                / float(matches_today["count"])
                if matches_today and matches_today["count"]
                else 0.0
            ),
            "outbox_pending": self._scalar_count("snapshot_outbox", "exported = 0"),
            "last_parquet_export": self._max_value("snapshots", "exported_at"),
        }

    def _scalar_count(self, table: str, clause: str = "1 = 1") -> int:
        row = self.connection.execute(
            f"SELECT COUNT(*) AS count FROM {table} WHERE {clause}"
        ).fetchone()
        return int(row["count"] or 0) if row else 0

    def _max_value(self, table: str, column: str) -> str | None:
        row = self.connection.execute(
            f"SELECT MAX({column}) AS value FROM {table}"
        ).fetchone()
        return str(row["value"]) if row and row["value"] else None

    def list_events_for_inspector(self, limit: int = 200) -> list[sqlite3.Row]:
        with self._lock:
            return list(
                self.connection.execute(
                    """
                    SELECT e.event_id, e.competition_name, e.competition_country,
                           e.home_team, e.away_team,
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

        latest = self.current_event_state(event_id) or self.latest_event_state(event_id)
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
        return self.upsert_current_event_state(state)


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
