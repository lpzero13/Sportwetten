from __future__ import annotations

from datetime import datetime, timedelta, timezone

from intelligence.equivalence import resolve_equivalences
from intelligence.models import CanonicalOutcome
from intelligence.odds import select_best_odds
from intelligence.probability import ProbabilityEngine
from intelligence.rescue import calculate_rescue_profile
from intelligence.strategy import calculate_zero_or_2plus
from intelligence.normalizer import normalize_event_details
from models.event import LiveEvent
from models.market import EventDetails, Market, Outcome


NOW = datetime(2026, 8, 29, 14, 0, tzinfo=timezone.utc)


def make_event(
    *,
    score: tuple[int, int] = (0, 0),
    extra_time: bool | None = False,
    penalties: bool | None = False,
) -> LiveEvent:
    return LiveEvent(
        event_id="event-1",
        competition_id="league-1",
        competition_name="Testliga",
        sport="soccer",
        home_team="Heim",
        away_team="Gast",
        home_team_id="1",
        away_team_id="2",
        kickoff_time=None,
        status="break",
        period="HALF_TIME",
        display_minute="HZ",
        score_home=score[0],
        score_away=score[1],
        ht_score_home=score[0],
        ht_score_away=score[1],
        bet_markets_count=1,
        extra_time=extra_time,
        penalties=penalties,
    )


def make_details(markets: list[Market], event: LiveEvent | None = None) -> EventDetails:
    return EventDetails(
        event=event or make_event(),
        markets=markets,
        categories=[],
        raw_data={},
    )


def make_market(
    market_id: str,
    market_type: str,
    fixed_param: str,
    outcomes: list[tuple[str, str, float | None, str | None, bool]],
) -> Market:
    return Market(
        market_id=market_id,
        event_id="event-1",
        caption=market_type,
        short_caption="",
        type=market_type,
        fixed_param=fixed_param,
        standard=False,
        status="open",
        outcomes=[
            Outcome(
                outcome_id=outcome_id,
                market_id=market_id,
                caption=caption,
                choice_param=choice,
                odds=odds if available else None,
                status=status,
                is_available=available,
                quote_raw=str(odds) if odds is not None else None,
                quote_float_value=odds,
            )
            for outcome_id, caption, odds, choice, available in outcomes
            for status in [None]
        ],
    )


def canonical(
    *,
    outcome_id: str,
    market_id: str,
    canonical_type: str,
    line: float | None = None,
    odds: float | None = 2.0,
    observed_at: str = NOW.isoformat(),
    available: bool = True,
    settlement_scope: str = "REGULATION_NO_EXTRA_TIME",
) -> CanonicalOutcome:
    return CanonicalOutcome(
        event_id="event-1",
        market_id=market_id,
        outcome_id=outcome_id,
        canonical_type=canonical_type,
        scope="REMAINING",
        period="SECOND_HALF",
        side=None,
        line=line,
        team=None,
        odds=odds if available else None,
        status="open" if available else "paused",
        available=available,
        observed_at=observed_at,
        raw_market_type="test",
        raw_market_caption="Test",
        raw_fixed_param="",
        raw_choice_param=None,
        raw_outcome_caption=outcome_id,
        settlement_scope=settlement_scope,
    )


def test_normalizer_uses_type_and_choice_param() -> None:
    details = make_details(
        [
            make_market(
                "m-rest",
                "points-more-less-rest",
                "2:0.5",
                [
                    ("u", "-", 2.2, "-", True),
                    ("o", "+", 1.7, "+", True),
                ],
            ),
            make_market(
                "m-next",
                "next-point",
                "2",
                [
                    ("none", "X", 2.8, "X", True),
                ],
            ),
            make_market(
                "m-btts",
                "score-both",
                "",
                [
                    ("yes", "J", 1.8, "J", True),
                    ("no", "N", 2.0, "N", True),
                ],
            ),
            make_market(
                "m-unknown",
                "double-chance",
                "",
                [
                    ("dc", "1X", 1.2, "1X", True),
                ],
            ),
        ]
    )
    normalized = normalize_event_details(details, observed_at=NOW.isoformat())
    by_id = {item.outcome_id: item for item in normalized}
    assert by_id["u"].canonical_type == "REMAINING_TOTAL_UNDER"
    assert by_id["u"].line == 0.5
    assert by_id["o"].canonical_type == "REMAINING_TOTAL_OVER"
    assert by_id["none"].canonical_type == "NEXT_GOAL_NONE"
    assert by_id["yes"].canonical_type == "BTTS_YES"
    assert by_id["no"].canonical_type == "BTTS_NO"
    assert by_id["dc"].canonical_type == "UNKNOWN"


def test_dynamic_match_total_equivalence_uses_current_score() -> None:
    for score, zero_line, two_line in [
        ((0, 0), 0.5, 1.5),
        ((1, 0), 1.5, 2.5),
        ((2, 1), 3.5, 4.5),
    ]:
        event = make_event(score=score)
        outcomes = [
            canonical(
                outcome_id=f"u-{score}",
                market_id=f"zero-{score}",
                canonical_type="MATCH_TOTAL_UNDER",
                line=zero_line,
            ),
            canonical(
                outcome_id=f"o-{score}",
                market_id=f"two-{score}",
                canonical_type="MATCH_TOTAL_OVER",
                line=two_line,
            ),
        ]
        zero, two_plus = resolve_equivalences(event, outcomes, now=NOW)
        assert zero.status == "EQUIVALENT"
        assert two_plus.status == "EQUIVALENT"
        assert zero.candidates[0].line == zero_line
        assert two_plus.candidates[0].line == two_line


def test_equivalence_is_unverified_when_extra_time_scope_is_unknown() -> None:
    event = make_event(extra_time=None)
    outcome = canonical(
        outcome_id="u",
        market_id="m",
        canonical_type="REMAINING_TOTAL_UNDER",
        line=0.5,
    )
    zero, _ = resolve_equivalences(event, [outcome], now=NOW)
    assert zero.status == "EQUIVALENCE_UNVERIFIED"
    assert zero.best_odds is None


def test_best_odds_excludes_paused_and_stale_quotes() -> None:
    stale_time = (NOW - timedelta(seconds=20)).isoformat()
    candidates = [
        canonical(
            outcome_id="stale",
            market_id="m1",
            canonical_type="NEXT_GOAL_NONE",
            odds=9.0,
            observed_at=stale_time,
        ),
        canonical(
            outcome_id="paused",
            market_id="m2",
            canonical_type="NEXT_GOAL_NONE",
            odds=12.0,
            available=False,
        ),
        canonical(
            outcome_id="fresh",
            market_id="m3",
            canonical_type="NEXT_GOAL_NONE",
            odds=3.5,
        ),
    ]
    result = select_best_odds("ZERO_REMAINING_GOALS", candidates, now=NOW)
    assert result.status == "OK"
    assert result.selected is not None
    assert result.selected.outcome_id == "fresh"
    assert [item.outcome_id for item in result.stale_candidates] == ["stale"]


def test_probability_distribution_and_inconsistency_are_explicit() -> None:
    outcomes = [
        canonical(
            outcome_id="u0",
            market_id="line05",
            canonical_type="REMAINING_TOTAL_UNDER",
            line=0.5,
            odds=2.0,
        ),
        canonical(
            outcome_id="o0",
            market_id="line05",
            canonical_type="REMAINING_TOTAL_OVER",
            line=0.5,
            odds=2.0,
        ),
        canonical(
            outcome_id="u1",
            market_id="line15",
            canonical_type="REMAINING_TOTAL_UNDER",
            line=1.5,
            odds=1.5,
        ),
        canonical(
            outcome_id="o1",
            market_id="line15",
            canonical_type="REMAINING_TOTAL_OVER",
            line=1.5,
            odds=3.0,
        ),
    ]
    result = ProbabilityEngine().calculate(make_event(), outcomes, now=NOW)
    assert result.status == "OK"
    assert result.p0 == 0.5
    assert round(result.p1 or 0, 6) == round(1 / 6, 6)
    assert round(result.p2_plus or 0, 6) == round(1 / 3, 6)

    inconsistent = [
        canonical(
            outcome_id="u0",
            market_id="i05",
            canonical_type="REMAINING_TOTAL_UNDER",
            line=0.5,
            odds=1.1,
        ),
        canonical(
            outcome_id="o0",
            market_id="i05",
            canonical_type="REMAINING_TOTAL_OVER",
            line=0.5,
            odds=10.0,
        ),
        canonical(
            outcome_id="u1",
            market_id="i15",
            canonical_type="REMAINING_TOTAL_UNDER",
            line=1.5,
            odds=10.0,
        ),
        canonical(
            outcome_id="o1",
            market_id="i15",
            canonical_type="REMAINING_TOTAL_OVER",
            line=1.5,
            odds=1.1,
        ),
    ]
    inconsistent_result = ProbabilityEngine().calculate(
        make_event(),
        inconsistent,
        now=NOW,
    )
    assert inconsistent_result.status == "INCONSISTENT_MARKETS"
    assert (inconsistent_result.p1 or 0) < 0


def test_strategy_formula_and_cent_rounding() -> None:
    result = calculate_zero_or_2plus(13.0, 1.27, total_stake=30.0)
    assert result.status == "OK"
    assert result.stake_zero == 2.67
    assert result.stake_two_plus == 27.33
    assert result.payout_zero == result.payout_two_plus == 34.71
    assert round(result.p1_max or 0, 6) == round(
        1 - (1 / 13 + 1 / 1.27),
        6,
    )
    assert result.loss_exact_one == -30.0

    no_cover = calculate_zero_or_2plus(1.5, 2.0, total_stake=30.0)
    assert no_cover.status == "NO_POSITIVE_COVERED_PAYOUT"


def test_rescue_profile_equalizes_the_two_post_goal_scenarios() -> None:
    result = calculate_rescue_profile(
        original_total_stake=30,
        original_zero_stake=2.67,
        original_zero_odds=13,
        original_two_plus_stake=27.33,
        original_two_plus_odds=1.27,
        hedge_odds=2.0,
        hedge_stake=0,
    )
    assert result.status == "OK"
    assert result.pnl_no_more_goal == -30
    assert result.pnl_another_goal > result.pnl_no_more_goal
    assert result.equalizing_hedge_stake is not None
    assert round(
        result.equalized_pnl or 0,
        8,
    ) == round(
        result.pnl_no_more_goal
        + (result.equalizing_hedge_stake or 0) * (result.hedge_odds - 1),
        8,
    )
