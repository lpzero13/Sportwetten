"""Small, framework-independent models for paper trading."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


PAPER_STRATEGY = "ZERO_OR_2PLUS"
PORTFOLIO_STATUSES = {"ACTIVE", "PAUSED", "ARCHIVED"}
STAKE_MODES = {"FIXED", "BANKROLL_PERCENTAGE"}


def decimal_value(value: Any, default: Decimal | None = None) -> Decimal | None:
    if value is None or value == "":
        return default
    try:
        return Decimal(str(value))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True, slots=True)
class PaperPortfolio:
    portfolio_id: str
    name: str
    created_at: str
    updated_at: str
    starting_bankroll: Decimal
    currency: str = "EUR"
    strategy_type: str = PAPER_STRATEGY
    stake_mode: str = "FIXED"
    fixed_stake: Decimal | None = Decimal("10.00")
    bankroll_percentage: Decimal | None = None
    min_stake: Decimal | None = None
    max_stake: Decimal | None = None
    minimum_win_roi: Decimal = Decimal("0")
    minimum_p1_buffer: Decimal = Decimal("0")
    maximum_tipico_p1: Decimal = Decimal("1")
    minimum_q_zero: Decimal = Decimal("1")
    minimum_q_two_plus: Decimal = Decimal("1")
    max_quote_age_seconds: int = 10
    entry_window_start_seconds: int = 0
    entry_window_end_seconds: int = 120
    allow_all_competitions: bool = True
    selected_competition_ids: tuple[str, ...] = field(default_factory=tuple)
    status: str = "ACTIVE"
    version: int = 1

    @classmethod
    def from_row(
        cls,
        row: Any,
        selected_competition_ids: tuple[str, ...] = (),
    ) -> "PaperPortfolio":
        return cls(
            portfolio_id=str(row["portfolio_id"]),
            name=str(row["name"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            starting_bankroll=decimal_value(row["starting_bankroll"], Decimal("0")) or Decimal("0"),
            currency=str(row["currency"] or "EUR"),
            strategy_type=str(row["strategy_type"] or PAPER_STRATEGY),
            stake_mode=str(row["stake_mode"] or "FIXED"),
            fixed_stake=decimal_value(row["fixed_stake"]),
            bankroll_percentage=decimal_value(row["bankroll_percentage"]),
            min_stake=decimal_value(row["min_stake"]),
            max_stake=decimal_value(row["max_stake"]),
            minimum_win_roi=decimal_value(row["minimum_win_roi"], Decimal("0")) or Decimal("0"),
            minimum_p1_buffer=decimal_value(row["minimum_p1_buffer"], Decimal("0")) or Decimal("0"),
            maximum_tipico_p1=decimal_value(row["maximum_tipico_p1"], Decimal("1")) or Decimal("1"),
            minimum_q_zero=decimal_value(row["minimum_q_zero"], Decimal("1")) or Decimal("1"),
            minimum_q_two_plus=decimal_value(row["minimum_q_two_plus"], Decimal("1")) or Decimal("1"),
            max_quote_age_seconds=int(row["max_quote_age_seconds"] or 10),
            entry_window_start_seconds=int(row["entry_window_start_seconds"] or 0),
            entry_window_end_seconds=int(row["entry_window_end_seconds"] or 120),
            allow_all_competitions=bool(row["allow_all_competitions"]),
            selected_competition_ids=tuple(selected_competition_ids),
            status=str(row["status"] or "ACTIVE"),
            version=int(row["version"] or 1),
        )


@dataclass(frozen=True, slots=True)
class SignalDecision:
    accepted: bool
    reason: str
    stake: Decimal | None = None
    quote_age_zero_seconds: float | None = None
    quote_age_two_plus_seconds: float | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SettlementResult:
    status: str
    second_half_goals: int | None
    reason: str
    return_amount: Decimal = Decimal("0")
    pnl: Decimal = Decimal("0")
    final_score_home: int | None = None
    final_score_away: int | None = None
