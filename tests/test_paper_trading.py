from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path

from intelligence.models import CanonicalOutcome
from models.event import LiveEvent
from models.event_state import EventState
from paper.engine import calculate_stake, evaluate_signal, settle_scores
from paper.models import PaperPortfolio
from paper.service import PaperTradingService
from storage.database import Database


OBSERVED = "2026-08-29T10:00:00+00:00"


def portfolio(**changes: object) -> PaperPortfolio:
    values: dict[str, object] = {
        "portfolio_id": "pf-test",
        "name": "Test",
        "created_at": OBSERVED,
        "updated_at": OBSERVED,
        "starting_bankroll": Decimal("100"),
        "fixed_stake": Decimal("10"),
    }
    values.update(changes)
    return PaperPortfolio(**values)  # type: ignore[arg-type]


def test_stake_modes_respect_min_max_and_never_overdraw() -> None:
    stake, status = calculate_stake(portfolio(), Decimal("100"))
    assert (stake, status) == (Decimal("10.00"), "OK")
    stake, status = calculate_stake(
        portfolio(stake_mode="BANKROLL_PERCENTAGE", fixed_stake=None, bankroll_percentage=Decimal("20"), max_stake=Decimal("12")),
        Decimal("100"),
    )
    assert (stake, status) == (Decimal("12.00"), "OK")
    stake, status = calculate_stake(portfolio(fixed_stake=Decimal("10")), Decimal("5"))
    assert (stake, status) == (None, "INSUFFICIENT_BANKROLL")


def test_signal_filters_cover_strategy_thresholds_and_quote_age() -> None:
    row = {
        "status": "OK", "q_zero": 1.8, "q_two_plus": 2.0,
        "win_roi": 0.08, "p1_buffer": 0.10, "p1_tipico": 0.30,
        "zero_quote_age_seconds": 2, "two_plus_quote_age_seconds": 3,
    }
    accepted = evaluate_signal(portfolio(minimum_win_roi=Decimal(".05")), row, available_bankroll=100)
    assert accepted.accepted is True
    rejected = evaluate_signal(portfolio(max_quote_age_seconds=2), row, available_bankroll=100)
    assert rejected.reason == "QUOTE_TOO_OLD"


def test_settlement_classifies_zero_middle_two_plus_and_scope() -> None:
    assert settle_scores(halftime_home=1, halftime_away=1, final_home=1, final_away=1).status == "WIN_ZERO"
    assert settle_scores(halftime_home=1, halftime_away=1, final_home=2, final_away=1).status == "LOSS_MIDDLE"
    assert settle_scores(halftime_home=1, halftime_away=1, final_home=2, final_away=2).status == "WIN_TWO_PLUS"
    assert settle_scores(halftime_home=1, halftime_away=1, final_home=2, final_away=2, extra_time=True).status == "VOID"
    assert settle_scores(halftime_home=1, halftime_away=1, final_home=2, final_away=2, extra_time=None).status == "UNRESOLVED"


def _event() -> LiveEvent:
    return LiveEvent(
        event_id="event-paper",
        competition_id="league-de",
        competition_name="Bundesliga",
        competition_country="Deutschland",
        sport="soccer",
        home_team="Heim",
        away_team="Gast",
        home_team_id="1",
        away_team_id="2",
        kickoff_time=None,
        status="break",
        period="HALF_TIME",
        display_minute="HZ",
        score_home=1,
        score_away=1,
        ht_score_home=1,
        ht_score_away=1,
        bet_markets_count=2,
    )


def _seed_paper_database(path: Path) -> Database:
    database = Database(path)
    event = _event()
    database.upsert_event(event, OBSERVED)
    database.record_event_state_if_changed(
        EventState(
            event_id=event.event_id, observed_at=OBSERVED, status="break",
            period="HALF_TIME", display_time="HZ", section_number=1,
            score_home=1, score_away=1, ht_score_home=1, ht_score_away=1,
            red_cards_home=0, red_cards_away=0,
        )
    )
    for outcome_id, market_id, canonical_type, odds in (
        ("zero", "m-zero", "REMAINING_TOTAL_UNDER", 2.2),
        ("two", "m-two", "REMAINING_TOTAL_OVER", 2.1),
    ):
        database.save_canonical_outcomes(
            [
                CanonicalOutcome(
                    event_id=event.event_id, market_id=market_id, outcome_id=outcome_id,
                    canonical_type=canonical_type, scope="REMAINING", period="SECOND_HALF",
                    side=None, line=.5 if outcome_id == "zero" else 1.5, team=None,
                    odds=odds, status="open", available=True, observed_at=OBSERVED,
                    raw_market_type="test-market", raw_market_caption="Test",
                    raw_fixed_param="", raw_choice_param=None,
                    raw_outcome_caption=outcome_id,
                )
            ]
        )
    database.record_strategy_evaluation_if_changed(
        event_id=event.event_id, observed_at=OBSERVED, strategy_type="ZERO_OR_2PLUS",
        strategy_version="ZERO_OR_2PLUS_v1", normalizer_version="v0.3.1", status="OK",
        total_stake=30, q_zero=2.2, q_two_plus=2.1, source_zero="zero", source_two_plus="two",
        stake_zero=15, stake_two_plus=15, payout_zero=33, payout_two_plus=31.5,
        payout_difference=1.5, covered_profit=1.5, win_roi=.05, p1_max=.5,
        p1_tipico=.2, p1_buffer=.3, p_zero=.45, p_one=.2, p_two_plus=.35,
    )
    return database


def test_paper_entry_is_idempotent_and_entry_snapshot_stays_immutable(tmp_path: Path) -> None:
    database = _seed_paper_database(tmp_path / "tipico.db")
    service = PaperTradingService(database)
    created = service.create_portfolio(name="Validation", starting_bankroll=100)
    now = datetime.fromisoformat(OBSERVED)
    assert service.process_signals(now=now)["trades_created"] == 1
    assert service.process_signals(now=now)["trades_created"] == 0
    trade = database.paper_trade_rows(created.portfolio_id)[0]
    original_quote = trade["q_zero"]
    settled = service.settle_trade(
        trade["paper_trade_id"], final_score_home=2, final_score_away=2, settled_at=OBSERVED
    )
    assert settled["status"] == "WIN_TWO_PLUS"
    assert settled["q_zero"] == original_quote
    assert settled["second_half_goals"] == 2
    assert database.paper_balance(created.portfolio_id) > 100
    assert len(database.paper_trade_rows(created.portfolio_id)) == 1
    database.close()
