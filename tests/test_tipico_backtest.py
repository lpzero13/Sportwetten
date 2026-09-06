from __future__ import annotations

import csv
import json
import sqlite3

import pytest

from tipico_research.runner import paper_dry_run, run_study
from tipico_research.source import TipicoSource
from tipico_research.variants import registered_variants


def _source_db(path, *, p1: float = 0.4358974358974359, event_id: str = "e1"):
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE events (
            event_id TEXT PRIMARY KEY, competition_id TEXT, competition_name TEXT,
            competition_country TEXT, home_team TEXT, away_team TEXT, kickoff_time TEXT
        );
        CREATE TABLE snapshots (
            snapshot_id INTEGER PRIMARY KEY, event_id TEXT, observed_at TEXT,
            snapshot_type TEXT, match_status TEXT, display_time TEXT,
            score_home INTEGER, score_away INTEGER, ht_score_home INTEGER,
            ht_score_away INTEGER, extra_time INTEGER, penalties INTEGER,
            q_zero_best REAL, q_zero_source_type TEXT, q_zero_market_id TEXT,
            q_zero_outcome_id TEXT, q_two_plus_best REAL, q_two_plus_source_type TEXT,
            q_two_plus_market_id TEXT, q_two_plus_outcome_id TEXT,
            remaining_under_05 REAL, remaining_over_05 REAL,
            remaining_under_15 REAL, remaining_over_15 REAL,
            p0_market REAL, p1_market REAL, p2plus_market REAL,
            p1_break_even REAL, p1_buffer REAL, win_roi REAL,
            competition_id TEXT, competition_name TEXT, competition_country TEXT,
            home_team TEXT, away_team TEXT, kickoff_time TEXT,
            snapshot_quality TEXT, normalizer_version TEXT, strategy_version TEXT,
            relevant_markets_json TEXT, archive_path TEXT, payload_hash TEXT
        );
        CREATE TABLE match_results (
            event_id TEXT PRIMARY KEY, competition_id TEXT, competition_name TEXT,
            competition_country TEXT, home_team TEXT, away_team TEXT,
            kickoff_at TEXT, ht_home INTEGER, ht_away INTEGER, ft_home INTEGER,
            ft_away INTEGER, first_half_goals INTEGER, second_half_goals INTEGER,
            second_half_goal_class TEXT, final_status TEXT, finished_at TEXT,
            extra_time INTEGER, penalties INTEGER
        );
        CREATE TABLE odds_history (id INTEGER);
        CREATE TABLE canonical_outcomes (id INTEGER);
        """
    )
    markets = [
        {"canonical_type": "REMAINING_TOTAL_UNDER", "line": 0.5, "market_id": "m05", "odds": 4.0, "outcome_id": "u05", "period": "SECOND_HALF", "scope": "REMAINING"},
        {"canonical_type": "REMAINING_TOTAL_OVER", "line": 0.5, "market_id": "m05", "odds": 1.2, "outcome_id": "o05", "period": "SECOND_HALF", "scope": "REMAINING"},
        {"canonical_type": "REMAINING_TOTAL_UNDER", "line": 1.5, "market_id": "m15", "odds": 1.5, "outcome_id": "u15", "period": "SECOND_HALF", "scope": "REMAINING"},
        {"canonical_type": "REMAINING_TOTAL_OVER", "line": 1.5, "market_id": "m15", "odds": 3.0, "outcome_id": "o15", "period": "SECOND_HALF", "scope": "REMAINING"},
    ]
    connection.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?)", (event_id, "c1", "League", "Germany", "Home", "Away", "2026-09-01T18:00:00+00:00"))
    snapshot_values = (1, event_id, "2026-09-01T18:45:00+00:00", "HT_STABLE", "break", "HZ", 0, 0, 0, 0, 0, 0, 4.0, "points-more-less-rest", "m05", "u05", 3.0, "points-more-less-rest", "m15", "o15", 4.0, 1.2, 1.5, 3.0, 0.2307692308, p1, 0.3333333333, 0.4166666667, 0.4166666667 - p1, 0.7142857143, "c1", "League", "Germany", "Home", "Away", "2026-09-01T18:00:00+00:00", "COMPLETE", "v0.3.1", "ZERO_OR_2PLUS_v1", json.dumps(markets), None, "hash")
    connection.execute(
        f"INSERT INTO snapshots VALUES ({','.join('?' for _ in snapshot_values)})",
        snapshot_values,
    )
    connection.execute(
        "INSERT INTO match_results VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (event_id, "c1", "League", "Germany", "Home", "Away", "2026-09-01T18:00:00+00:00", 0, 0, 0, 0, 0, 0, "0", "finished", "2026-09-01T19:45:00+00:00", 0, 0),
    )
    connection.commit()
    connection.close()


def test_source_is_read_only_and_reconstructs_p1(tmp_path):
    path = tmp_path / "tipico.db"
    _source_db(path)
    with TipicoSource(path) as source:
        rows = source.observations()
        assert source.quick_check() == "ok"
        assert rows[0]["p1_recomputed"] == pytest.approx(rows[0]["p1_market"])
        assert rows[0]["entry_eligible"] is True
        assert rows[0]["evidence_level"] == "A_VERIFIED_REPLAY"
        with pytest.raises(sqlite3.OperationalError):
            source.connection.execute("CREATE TABLE forbidden (id INTEGER)")


def test_registered_boundaries_are_deterministic():
    variants = {item.variant_id: item for item in registered_variants()}
    row = {"entry_eligible": True, "p1_market": 0.25, "p1_break_even": 0.3, "p1_buffer": -0.05, "ht_goals": 1}
    assert variants["P12"].applies(row)
    assert not variants["P11"].applies(row)
    assert variants["G12"].applies(row)
    assert variants["H11"].applies(row)


def test_run_study_writes_replay_artifacts(tmp_path):
    source = tmp_path / "tipico.db"
    output = tmp_path / "research"
    _source_db(source)
    result = run_study(source, output, run_id="tipico-v063-test", bootstrap_iterations=20)
    run = output / result["run_id"]
    required = {
        "RUN_MANIFEST.json", "TIPICO_DATA_AUDIT.md", "TIPICO_COVERAGE.csv",
        "TIPICO_REJECTED_OBSERVATIONS.csv", "TIPICO_BACKTEST_DATASET.parquet",
        "TIPICO_P1_CALIBRATION.csv", "TIPICO_PATTERN_BUCKETS.csv",
        "TIPICO_VARIANT_RESULTS.csv", "TIPICO_BACKTEST_TRADES.parquet",
        "TIPICO_BACKTEST_STATUS.md", "PAPER_CANDIDATES.json",
        "HERMES_TIPICO_RUNBOOK.md",
    }
    assert required <= {item.name for item in run.iterdir()}
    manifest = json.loads((run / "RUN_MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["dataset"]["observations"] == 1
    assert manifest["dataset"]["resolved"] == 1
    rows = list(csv.DictReader((run / "TIPICO_VARIANT_RESULTS.csv").open(encoding="utf-8")))
    r00 = next(row for row in rows if row["variant_id"] == "R00")
    assert r00["different_games"] == "1"
    assert float(r00["pnl"]) > 0


def test_cost_and_quote_haircut_are_applied_once(tmp_path):
    source = tmp_path / "tipico.db"
    output = tmp_path / "research"
    _source_db(source)
    result = run_study(
        source, output, run_id="tipico-v063-stress", cost=1.0,
        quote_haircut=0.1, bootstrap_iterations=5,
    )
    import pyarrow.parquet as parquet

    trades = parquet.read_table(
        output / result["run_id"] / "TIPICO_BACKTEST_TRADES.parquet"
    ).to_pylist()
    r00 = next(row for row in trades if row["variant_id"] == "R00")
    # q0=4.00 -> 3.70 after a 10% net-quote haircut.  The extra cost is
    # charged exactly once after the payout.
    assert r00["q_zero"] == pytest.approx(3.7)
    assert r00["cost"] == pytest.approx(1.0)
    assert r00["pnl"] == pytest.approx(4.95, abs=0.01)


def test_paper_dry_run_reuses_entry_decision_without_persistence(tmp_path):
    source = tmp_path / "tipico.db"
    output = tmp_path / "research"
    _source_db(source)
    result = run_study(source, output, run_id="tipico-v063-dry-run", bootstrap_iterations=5)
    run_path = output / result["run_id"]
    before = sorted(path.name for path in run_path.iterdir())
    dry = paper_dry_run(run_path=run_path, event_id="e1", variant_id="P15")
    after = sorted(path.name for path in run_path.iterdir())
    assert dry["decision"]["decision"] == "BET"
    assert dry["decision"]["config_hash"]
    assert dry["persisted"] is False
    assert dry["trade_created"] is False
    assert before == after


def test_paper_dry_run_rejects_non_paper_variant(tmp_path):
    source = tmp_path / "tipico.db"
    output = tmp_path / "research"
    _source_db(source)
    result = run_study(source, output, run_id="tipico-v063-dry-invalid", bootstrap_iterations=5)
    dry = paper_dry_run(run_path=output / result["run_id"], event_id="e1", variant_id="R00")
    assert dry["decision"]["decision"] == "INVALID"
    assert dry["decision"]["reason"] == "WET_FORM_NOT_PAPER_SUPPORTED"
