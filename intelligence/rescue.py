"""Scenario-only hedge arithmetic for an already existing ZERO_OR_2PLUS position."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class RescueResult:
    status: str
    original_total_stake: float
    original_zero_stake: float
    original_zero_odds: float
    original_two_plus_stake: float
    original_two_plus_odds: float
    hedge_odds: float
    hedge_stake: float
    pnl_no_more_goal: float
    pnl_another_goal: float
    equalizing_hedge_stake: float | None
    equalized_pnl: float | None


def calculate_rescue_profile(
    *,
    original_total_stake: float,
    original_zero_stake: float,
    original_zero_odds: float,
    original_two_plus_stake: float,
    original_two_plus_odds: float,
    hedge_odds: float,
    hedge_stake: float = 0.0,
) -> RescueResult:
    """Return both post-one-goal scenarios; never labels a hedge as advice."""

    values = (
        original_total_stake,
        original_zero_stake,
        original_zero_odds,
        original_two_plus_stake,
        original_two_plus_odds,
        hedge_odds,
        hedge_stake,
    )
    if any(value < 0 for value in values) or hedge_odds <= 1:
        return RescueResult(
            status="INVALID_INPUT",
            original_total_stake=original_total_stake,
            original_zero_stake=original_zero_stake,
            original_zero_odds=original_zero_odds,
            original_two_plus_stake=original_two_plus_stake,
            original_two_plus_odds=original_two_plus_odds,
            hedge_odds=hedge_odds,
            hedge_stake=hedge_stake,
            pnl_no_more_goal=0.0,
            pnl_another_goal=0.0,
            equalizing_hedge_stake=None,
            equalized_pnl=None,
        )

    # After exactly one HZ2 goal the original 0-bet is lost. The original
    # 2+-bet wins only if another goal follows.
    no_more_base = -original_total_stake
    another_base = original_two_plus_stake * original_two_plus_odds - original_total_stake
    pnl_no_more = no_more_base + hedge_stake * (hedge_odds - 1)
    pnl_another = another_base - hedge_stake
    equalizing = (another_base - no_more_base) / hedge_odds
    if equalizing < 0:
        equalizing = None
        equalized_pnl = None
        status = "NO_NON_NEGATIVE_EQUALIZING_HEDGE"
    else:
        equalized_pnl = no_more_base + equalizing * (hedge_odds - 1)
        status = "OK"
    return RescueResult(
        status=status,
        original_total_stake=original_total_stake,
        original_zero_stake=original_zero_stake,
        original_zero_odds=original_zero_odds,
        original_two_plus_stake=original_two_plus_stake,
        original_two_plus_odds=original_two_plus_odds,
        hedge_odds=hedge_odds,
        hedge_stake=hedge_stake,
        pnl_no_more_goal=pnl_no_more,
        pnl_another_goal=pnl_another,
        equalizing_hedge_stake=equalizing,
        equalized_pnl=equalized_pnl,
    )
