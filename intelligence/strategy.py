"""Deterministic ZERO_OR_2PLUS stake and scenario calculations."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from .models import StrategyResult


STRATEGY_TYPE = "ZERO_OR_2PLUS"
STRATEGY_VERSION = "ZERO_OR_2PLUS_v1"
CENT = Decimal("0.01")


def _decimal(value: float | int | Decimal) -> Decimal:
    return Decimal(str(value))


def _round_cent(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _best_cent_split(
    total_stake: Decimal,
    q_zero: Decimal,
    q_two_plus: Decimal,
    exact_zero_stake: Decimal,
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """Choose the nearest cent split with the smallest payout mismatch."""

    base = _round_cent(exact_zero_stake)
    candidates: list[tuple[Decimal, Decimal, Decimal, Decimal]] = []
    for offset in (-1, 0, 1):
        stake_zero = base + Decimal(offset) * CENT
        if stake_zero < 0 or stake_zero > total_stake:
            continue
        stake_two = total_stake - stake_zero
        payout_zero = _round_cent(stake_zero * q_zero)
        payout_two = _round_cent(stake_two * q_two_plus)
        candidates.append(
            (
                abs(payout_zero - payout_two),
                stake_zero,
                payout_zero,
                payout_two,
            )
        )
    if not candidates:
        stake_zero = _round_cent(exact_zero_stake)
        stake_two = total_stake - stake_zero
        return (
            stake_zero,
            stake_two,
            _round_cent(stake_zero * q_zero),
            _round_cent(stake_two * q_two_plus),
        )
    _, stake_zero, payout_zero, payout_two = min(
        candidates,
        key=lambda item: (item[0], abs(item[1] - exact_zero_stake)),
    )
    return stake_zero, total_stake - stake_zero, payout_zero, payout_two


def calculate_zero_or_2plus(
    q_zero: float | None,
    q_two_plus: float | None,
    *,
    total_stake: float = 30.0,
    p1_tipico: float | None = None,
    source_zero: str | None = None,
    source_two_plus: str | None = None,
) -> StrategyResult:
    stake = _decimal(total_stake)
    if q_zero is None or q_two_plus is None or q_zero <= 1 or q_two_plus <= 1:
        return StrategyResult(
            strategy_type=STRATEGY_TYPE,
            strategy_version=STRATEGY_VERSION,
            status="MISSING_QUOTES",
            label="UNVOLLSTÄNDIG",
            total_stake=float(stake),
            q_zero=q_zero,
            q_two_plus=q_two_plus,
            source_zero=source_zero,
            source_two_plus=source_two_plus,
            p1_tipico=p1_tipico,
            loss_exact_one=-float(stake),
            warnings=["Frische, offene Quoten für 0 und 2+ sind erforderlich."],
        )

    zero = _decimal(q_zero)
    two_plus = _decimal(q_two_plus)
    reciprocal_sum = (Decimal(1) / zero) + (Decimal(1) / two_plus)
    p1_max = Decimal(1) - reciprocal_sum
    payout_before_rounding = stake / reciprocal_sum
    exact_zero_stake = payout_before_rounding / zero
    stake_zero, stake_two, payout_zero, payout_two = _best_cent_split(
        stake,
        zero,
        two_plus,
        exact_zero_stake,
    )
    payout_difference = abs(payout_zero - payout_two)
    status = "OK" if reciprocal_sum < 1 else "NO_POSITIVE_COVERED_PAYOUT"
    if status == "OK" and p1_tipico is not None:
        if p1_tipico > float(p1_max):
            label = "UNATTRAKTIV"
        elif float(p1_max) - p1_tipico < 0.03:
            label = "NEUTRAL"
        else:
            label = "INTERESSANT"
    elif status == "OK":
        label = "NEUTRAL"
    else:
        label = "UNATTRAKTIV"
    return StrategyResult(
        strategy_type=STRATEGY_TYPE,
        strategy_version=STRATEGY_VERSION,
        status=status,
        label=label,
        total_stake=float(stake),
        q_zero=float(zero),
        q_two_plus=float(two_plus),
        source_zero=source_zero,
        source_two_plus=source_two_plus,
        payout_before_rounding=float(payout_before_rounding),
        stake_zero=float(stake_zero),
        stake_two_plus=float(stake_two),
        payout_zero=float(payout_zero),
        payout_two_plus=float(payout_two),
        payout_difference=float(payout_difference),
        covered_profit=float(payout_before_rounding - stake),
        win_roi=float((payout_before_rounding - stake) / stake) if stake else None,
        loss_exact_one=-float(stake),
        p1_max=float(p1_max),
        p1_tipico=p1_tipico,
        p1_buffer=(float(p1_max) - p1_tipico) if p1_tipico is not None else None,
        warnings=(
            ["Der Kehrwertsumme >= 1 fehlt ein positiver gedeckter Auszahlungspuffer."]
            if status != "OK"
            else []
        ),
    )
