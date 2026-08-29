"""Pure calculations used by the paper worker and unit tests."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from .models import PaperPortfolio, SettlementResult, SignalDecision, decimal_value


CENT = Decimal("0.01")
NORMAL_SETTLEMENT_STATUSES = {
    "FINISHED", "FINAL", "ENDED", "END", "COMPLETED", "SETTLED", "FULL_TIME",
    "NO_LONGER_LIVE_FINAL",
}
VOID_STATUSES = {"ABORTED", "CANCELLED", "CANCELED", "POSTPONED", "SUSPENDED", "VOID"}


def _cent(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def calculate_stake(
    portfolio: PaperPortfolio,
    available_bankroll: Decimal | float | int,
) -> tuple[Decimal | None, str]:
    """Calculate a cent-rounded stake while guaranteeing no overdraft."""

    available = decimal_value(available_bankroll, Decimal("0")) or Decimal("0")
    if available < 0:
        available = Decimal("0")
    if portfolio.stake_mode == "BANKROLL_PERCENTAGE":
        percentage = portfolio.bankroll_percentage or Decimal("0")
        requested = available * percentage / Decimal("100")
    else:
        requested = portfolio.fixed_stake or Decimal("0")
    if portfolio.min_stake is not None and requested < portfolio.min_stake:
        requested = portfolio.min_stake
    if portfolio.max_stake is not None and requested > portfolio.max_stake:
        requested = portfolio.max_stake
    requested = _cent(requested)
    if requested <= 0:
        return None, "INVALID_STAKE"
    if requested > available:
        return None, "INSUFFICIENT_BANKROLL"
    return requested, "OK"


def _age(value: Any, fallback: float | None = None) -> float | None:
    result = decimal_value(value)
    return float(result) if result is not None else fallback


def evaluate_signal(
    portfolio: PaperPortfolio,
    evaluation: Any,
    *,
    quote_age_zero_seconds: float | None = None,
    quote_age_two_plus_seconds: float | None = None,
    available_bankroll: Decimal | float | int | None = None,
) -> SignalDecision:
    """Apply a portfolio's immutable signal rules to one evaluation row."""

    def value(name: str, default: Any = None) -> Any:
        if isinstance(evaluation, dict):
            return evaluation.get(name, default)
        try:
            return evaluation[name]
        except (KeyError, IndexError, TypeError):
            return default

    if portfolio.strategy_type != "ZERO_OR_2PLUS":
        return SignalDecision(False, "UNSUPPORTED_STRATEGY")
    if str(value("status", "")).upper() != "OK":
        return SignalDecision(False, f"EVALUATION_{str(value('status', 'UNKNOWN')).upper()}")

    q_zero = decimal_value(value("q_zero"))
    q_two = decimal_value(value("q_two_plus"))
    if q_zero is None or q_zero < portfolio.minimum_q_zero:
        return SignalDecision(False, "MINIMUM_Q_ZERO_NOT_MET", details={"q_zero": q_zero})
    if q_two is None or q_two < portfolio.minimum_q_two_plus:
        return SignalDecision(False, "MINIMUM_Q_TWO_PLUS_NOT_MET", details={"q_two_plus": q_two})

    win_roi = decimal_value(value("win_roi"))
    if win_roi is None or win_roi < portfolio.minimum_win_roi:
        return SignalDecision(False, "MINIMUM_WIN_ROI_NOT_MET", details={"win_roi": win_roi})
    p1_buffer = decimal_value(value("p1_buffer"))
    if p1_buffer is None or p1_buffer < portfolio.minimum_p1_buffer:
        return SignalDecision(False, "MINIMUM_P1_BUFFER_NOT_MET", details={"p1_buffer": p1_buffer})
    p1_tipico = decimal_value(value("p1_tipico"))
    if p1_tipico is None or p1_tipico > portfolio.maximum_tipico_p1:
        return SignalDecision(False, "MAXIMUM_TIPICO_P1_EXCEEDED", details={"p1_tipico": p1_tipico})

    max_age = float(portfolio.max_quote_age_seconds)
    age_zero = quote_age_zero_seconds
    age_two = quote_age_two_plus_seconds
    if age_zero is None:
        age_zero = _age(value("zero_quote_age_seconds"))
    if age_two is None:
        age_two = _age(value("two_plus_quote_age_seconds"))
    if age_zero is None or age_two is None:
        return SignalDecision(False, "QUOTE_AGE_UNKNOWN")
    if age_zero > max_age or age_two > max_age:
        return SignalDecision(
            False,
            "QUOTE_TOO_OLD",
            quote_age_zero_seconds=age_zero,
            quote_age_two_plus_seconds=age_two,
        )

    stake: Decimal | None = None
    stake_reason = "NOT_CALCULATED"
    if available_bankroll is not None:
        stake, stake_reason = calculate_stake(portfolio, available_bankroll)
        if stake is None:
            return SignalDecision(
                False,
                stake_reason,
                quote_age_zero_seconds=age_zero,
                quote_age_two_plus_seconds=age_two,
            )
    return SignalDecision(
        True,
        "SIGNAL_ACCEPTED",
        stake=stake,
        quote_age_zero_seconds=age_zero,
        quote_age_two_plus_seconds=age_two,
        details={"stake_status": stake_reason},
    )


def settle_scores(
    *,
    halftime_home: int | None,
    halftime_away: int | None,
    final_home: int | None,
    final_away: int | None,
    status: str | None = "FINISHED",
    extra_time: bool | None = False,
    penalties: bool | None = False,
) -> SettlementResult:
    """Classify only goals scored after HT and protect against future leakage."""

    final_home_int = int(final_home) if final_home is not None else None
    final_away_int = int(final_away) if final_away is not None else None
    if str(status or "").strip().upper() in VOID_STATUSES:
        return SettlementResult("VOID", None, f"EVENT_{str(status).upper()}", final_score_home=final_home_int, final_score_away=final_away_int)
    if extra_time is True or penalties is True:
        return SettlementResult(
            "VOID", None, "EXTRA_TIME_OR_PENALTIES_NOT_IN_ENTRY_SCOPE",
            final_score_home=final_home_int, final_score_away=final_away_int,
        )
    if extra_time is None or penalties is None:
        return SettlementResult(
            "UNRESOLVED", None, "EXTRA_TIME_SCOPE_UNKNOWN",
            final_score_home=final_home_int, final_score_away=final_away_int,
        )
    if str(status or "").strip().upper() not in NORMAL_SETTLEMENT_STATUSES:
        return SettlementResult(
            "UNRESOLVED", None, "FINAL_RESULT_NOT_CONFIRMED",
            final_score_home=final_home_int, final_score_away=final_away_int,
        )
    if halftime_home is None or halftime_away is None or final_home_int is None or final_away_int is None:
        return SettlementResult(
            "UNRESOLVED", None, "MISSING_HALF_TIME_OR_FINAL_SCORE",
            final_score_home=final_home_int, final_score_away=final_away_int,
        )
    second_half_goals = final_home_int + final_away_int - int(halftime_home) - int(halftime_away)
    if second_half_goals < 0:
        return SettlementResult(
            "UNRESOLVED", None, "FINAL_SCORE_BELOW_HALF_TIME_SCORE",
            final_score_home=final_home_int, final_score_away=final_away_int,
        )
    if second_half_goals == 0:
        result = "WIN_ZERO"
    elif second_half_goals == 1:
        result = "LOSS_MIDDLE"
    else:
        result = "WIN_TWO_PLUS"
    return SettlementResult(
        result,
        second_half_goals,
        f"SECOND_HALF_GOALS_{second_half_goals}",
        final_score_home=final_home_int,
        final_score_away=final_away_int,
    )
