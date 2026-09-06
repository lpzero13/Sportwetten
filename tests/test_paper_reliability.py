from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor

import pytest

from paper.engine import settle_scores
from paper.journal import PaperJournal
from paper.market import capture_market
from paper.service import PaperTradingService
from storage.database import Database
from models.snapshot import Snapshot
from tests.test_paper_trading import OBSERVED, _seed_paper_database
from tests.test_market_intelligence import make_event, make_market, make_details
from intelligence.service import MarketIntelligenceService


NOW = datetime.fromisoformat(OBSERVED)


@pytest.fixture
def db(tmp_path):
    value = _seed_paper_database(tmp_path / "paper.db")
    yield value
    value.close()


def context(db):
    return PaperJournal(db).current(OBSERVED)[0]


def portfolio(db, **kwargs):
    return PaperTradingService(db).create_portfolio(name="Paper", starting_bankroll=100, **kwargs)


def test_source_to_decision_uses_same_pairs_and_does_not_invent_positive_edge(tmp_path):
    db = Database(tmp_path / "e2e.db")
    event = make_event(score=(1, 1))
    db.upsert_event(event, OBSERVED)
    details = make_details([
        make_market("m05", "points-more-less-rest", "2:0.5", [("u05", "-", 3.4, "-", True), ("o05", "+", 1.32, "+", True)]),
        make_market("m15", "points-more-less-rest", "2:1.5", [("u15", "-", 1.6, "-", True), ("o15", "+", 2.3, "+", True)]),
    ], event)
    MarketIntelligenceService(db).analyze(details, observed_at=OBSERVED, now=NOW)
    structure = portfolio(db, family="MARKET_STRUCTURE")
    market = portfolio(db, family="MARKET_ONLY")
    result = PaperTradingService(db).process_signals(now=NOW)
    assert result["trades_created"] == 1
    assert len(db.paper_trade_rows(structure.portfolio_id)) == 1
    assert not db.paper_trade_rows(market.portfolio_id)
    rows = PaperJournal(db).decision_rows(market.portfolio_id)
    assert rows[0]["reason"] == "MINIMUM_P1_BUFFER_NOT_MET"
    observed = json.loads(rows[0]["payload_json"])
    assert observed["probability"]["execution_reference_overlap"] is True
    assert observed["probability"]["p1"] == pytest.approx(.3100825727944373)
    assert observed["probability"]["p1_power"] is not None
    assert db.connection.execute("SELECT COUNT(*) FROM paper_observations").fetchone()[0] == 1
    db.close()


def test_structure_accepts_missing_probability_market_strategy_rejects(db):
    value = context(db)
    value["probability"].update(status="MISSING_MARKETS", p0=None, p1=None, p2_plus=None)
    PaperJournal(db).publish(value)
    structure = portfolio(db, family="MARKET_STRUCTURE")
    market = portfolio(db, family="MARKET_ONLY")
    result = PaperTradingService(db).process_signals(now=NOW)
    assert result["trades_created"] == 1
    assert db.paper_trade_rows(structure.portfolio_id)[0]["p_one"] is None
    assert PaperJournal(db).decision_rows(market.portfolio_id)[0]["reason"] == "MARKET_PROBABILITY_UNAVAILABLE"


@pytest.mark.parametrize("mutate,reason", [
    (lambda c: c["zero"].update(line=1.5), "MARKET_SEMANTICS_MISMATCH"),
    (lambda c: c["zero"].update(observed_at="2026-08-29T09:59:59+00:00"), "QUOTE_SNAPSHOT_MISMATCH"),
    (lambda c: c["two_plus"].update(status="suspended"), "MARKET_NOT_OPEN"),
    (lambda c: c["event"].update(period="SECOND_HALF", display_minute="60"), "NOT_HALFTIME"),
    (lambda c: c["event"].update(ht_score_home=None), "HALFTIME_SCORE_MISSING"),
    (lambda c: c["event"].update(score_home=2), "HALFTIME_SCORE_CONFLICT"),
    (lambda c: c["event"].update(extra_time=None), "SETTLEMENT_SCOPE_UNKNOWN"),
])
def test_invalid_observation_never_opens_even_if_numeric_odds_match(db, mutate, reason):
    p = portfolio(db, family="MARKET_STRUCTURE")
    value = context(db)
    mutate(value)
    PaperJournal(db).publish(value)
    result = PaperTradingService(db).process_signals(now=NOW)
    assert result["trades_created"] == 0
    reasons = {row["reason"] for row in PaperJournal(db).decision_rows(p.portfolio_id)}
    assert reason in reasons or reason == "HALFTIME_SCORE_CONFLICT" and "SCORE_CHANGED_SINCE_OBSERVATION" in reasons
    assert db.paper_balance(p.portfolio_id) == 100


def test_quote_expiry_and_entry_window_are_visible_and_do_not_reset_on_refresh(db):
    p = portfolio(db, entry_window_end_seconds=5)
    service = PaperTradingService(db)
    assert service.process_signals(now=NOW + timedelta(seconds=20))["trades_created"] == 0
    assert PaperJournal(db).decision_rows(p.portfolio_id)[0]["reason"] == "QUOTE_TOO_OLD"
    value = context(db)
    later = NOW + timedelta(seconds=30)
    value["observed_at"] = later.isoformat()
    for key in ("zero", "two_plus"):
        value[key]["observed_at"] = later.isoformat()
    PaperJournal(db).publish(value)
    assert service.process_signals(now=later)["trades_created"] == 0
    assert PaperJournal(db).decision_rows(p.portfolio_id)[0]["reason"] == "ENTRY_WINDOW_EXPIRED"


@pytest.mark.parametrize("ft,expected", [((1, 1), "WIN_ZERO"), ((2, 1), "LOSS_MIDDLE"), ((2, 2), "WIN_TWO_PLUS")])
def test_restart_pending_result_then_final_credits_once(db, ft, expected):
    p = portfolio(db)
    service = PaperTradingService(db)
    service.process_signals(now=NOW)
    trade = db.paper_trade_rows(p.portfolio_id)[0]
    evidence = trade["entry_snapshot_json"]
    pending = service.settle_trade(trade["paper_trade_id"], final_score_home=None, final_score_away=None,
                                   extra_time=None, penalties=None)
    assert pending["status"] == "OPEN"
    assert pending["settled_at"] is None and pending["pnl"] is None
    assert db.paper_balance(p.portfolio_id) == 90
    restarted = Database(db.path)
    try:
        worker = PaperTradingService(restarted)
        assert worker.process_signals(now=NOW)["trades_created"] == 0
        resolver = lambda event_id: {"final_score_home": ft[0], "final_score_away": ft[1], "status": "FINISHED", "extra_time": False, "penalties": False}
        result = worker.settle_open_trades(resolver=resolver, now=NOW + timedelta(hours=1))
        assert result["settled"] == 1
        settled = restarted.paper_trade_row(trade["paper_trade_id"])
        assert settled["status"] == expected
        assert settled["entry_snapshot_json"] == evidence
        balance = restarted.paper_balance(p.portfolio_id)
        worker.settle_open_trades(resolver=resolver, now=NOW + timedelta(hours=1, minutes=3))
        assert restarted.paper_balance(p.portfolio_id) == balance
        assert restarted.connection.execute("SELECT COUNT(*) FROM paper_bankroll_transactions WHERE transaction_type='TRADE_SETTLED'").fetchone()[0] == 1
    finally:
        restarted.close()


def test_extra_time_requires_explicit_regulation_score_and_checks_each_team():
    args = dict(halftime_home=1, halftime_away=1, final_home=4, final_away=2, extra_time=1, penalties=0)
    assert settle_scores(**args).status == "UNRESOLVED"
    result = settle_scores(**args, regulation_home=2, regulation_away=1, regulation_confirmed=True)
    assert result.status == "LOSS_MIDDLE" and result.second_half_goals == 1
    assert settle_scores(halftime_home=2, halftime_away=0, final_home=1, final_away=3).status == "UNRESOLVED"
    assert settle_scores(halftime_home=0, halftime_away=0, final_home=1.2, final_away=0).status == "UNRESOLVED"
    assert settle_scores(halftime_home=0, halftime_away=0, final_home=0, final_away=0, status="SUSPENDED").status == "UNRESOLVED"


def test_no_bet_games_get_outcomes_and_one_network_request_for_many_strategies(db):
    service = PaperTradingService(db)
    for i in range(20):
        portfolio(db, minimum_p1_break_even=.3 if i % 2 else 0)
    service.process_signals(now=NOW)
    calls = []
    def resolver(event_id):
        calls.append(event_id)
        return {"final_score_home": 2, "final_score_away": 1, "status": "FINISHED", "extra_time": 0, "penalties": 0}
    result = service.settle_open_trades(resolver=resolver, now=NOW + timedelta(hours=1))
    assert calls == ["event-paper"]
    assert result["settled"] == 10
    rows = db.connection.execute("SELECT decision FROM paper_decisions").fetchall()
    assert sum(r[0] == "NO_BET" for r in rows) == 10
    assert json.loads(db.connection.execute("SELECT evidence_json FROM paper_result_checks").fetchone()[0])["target_h2_class"] == "H2_GOALS_1"


def test_real_sqlite_scope_flags_and_unknown_final_snapshot(db):
    p = portfolio(db)
    service = PaperTradingService(db)
    service.process_signals(now=NOW)
    db.create_snapshot(Snapshot(event_id="event-paper", observed_at=OBSERVED, snapshot_type="FINAL",
                              match_status="FINISHED", score_home=4, score_away=1))
    service.settle_open_trades(now=NOW + timedelta(hours=1))
    assert db.paper_trade_rows(p.portfolio_id)[0]["status"] == "OPEN"


def test_strategy_edit_keeps_entry_config_and_reserved_bankroll(db):
    p = portfolio(db)
    service = PaperTradingService(db)
    service.process_signals(now=NOW)
    trade = db.paper_trade_rows(p.portfolio_id)[0]
    modified = service.update_portfolio(p.portfolio_id, minimum_p1_buffer=.4)
    assert modified.version == p.version + 1
    assert modified.config_hash != p.config_hash
    assert db.paper_trade_rows(p.portfolio_id)[0]["config_hash"] == p.config_hash
    assert db.paper_trade_rows(p.portfolio_id)[0]["entry_snapshot_json"] == trade["entry_snapshot_json"]
    assert service.process_signals(now=NOW)["trades_created"] == 0


def test_two_workers_reserve_one_trade_and_decision(db):
    p = portfolio(db)
    def run():
        connection = Database(db.path)
        try:
            return PaperTradingService(connection).process_signals(now=NOW)
        finally:
            connection.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(), range(2)))
    assert sum(r["trades_created"] for r in results) == 1
    assert db.paper_balance(p.portfolio_id) == 90
    assert db.connection.execute("SELECT COUNT(*) FROM paper_decisions WHERE decision='BET'").fetchone()[0] == 1


def test_settlement_failure_does_not_skip_next_game_or_entry_phase(db):
    p = portfolio(db)
    service = PaperTradingService(db)
    def resolver(_):
        raise RuntimeError("provider unavailable")
    result = service.worker_once(now=NOW, resolver=resolver)
    assert result["trades_created"] == 1
    assert result["errors"] == 1
    assert db.paper_trade_rows(p.portfolio_id)[0]["status"] == "OPEN"
    assert PaperJournal(db).health()["last_worker"]["status"] == "ERROR"


def test_paper_dashboard_renders_saved_decision_and_reacts_to_controls(db):
    from streamlit.testing.v1 import AppTest
    service = PaperTradingService(db)
    p = portfolio(db)
    service.process_signals(now=NOW)
    app = AppTest.from_string(f'''
from storage.database import Database
from paper.service import PaperTradingService
from ui.paper_trading import render_paper_trading
db = Database({str(db.path)!r})
try:
    render_paper_trading(PaperTradingService(db), db, mobile=False)
finally:
    db.close()
''').run(timeout=30)
    assert not app.exception
    assert any("Entscheidung und Spielablauf" in element.label for element in app.selectbox)
    app.checkbox(key="paper-global-enabled").uncheck().run()
    assert not app.exception
    assert service.is_enabled() is False
