"""Deterministic replay calculations and uncertainty helpers."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import math
import random
from typing import Any, Iterable

from intelligence.strategy import calculate_zero_or_2plus


def wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> tuple[float | None, float | None]:
    if total <= 0:
        return None, None
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    radius = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denominator
    return max(0.0, centre - radius), min(1.0, centre + radius)


def _stress_quote(value: float | None, haircut: float) -> float | None:
    if value is None:
        return None
    return 1.0 + (float(value) - 1.0) * (1.0 - haircut)


def simulate_trade(row: dict[str, Any], variant: Any, *, stake: float = 10.0,
                   cost: float = 0.0, quote_haircut: float = 0.0) -> dict[str, Any]:
    q0 = _stress_quote(row.get("q_zero"), quote_haircut)
    q2 = _stress_quote(row.get("q_two_plus"), quote_haircut)
    outcome = row.get("outcome_class")
    payout = 0.0
    stake_zero = 0.0
    stake_two = 0.0
    win_roi = None
    if variant.bet_form == "SINGLE_ZERO":
        stake_zero = stake
        payout = round(stake * (q0 or 0.0), 2) if outcome == "H2_GOALS_0" else 0.0
        win_roi = q0 - 1.0 if q0 is not None else None
    elif variant.bet_form == "SINGLE_TWO_PLUS":
        stake_two = stake
        payout = round(stake * (q2 or 0.0), 2) if outcome == "H2_GOALS_2_PLUS" else 0.0
        win_roi = q2 - 1.0 if q2 is not None else None
    else:
        strategy = calculate_zero_or_2plus(q0, q2, total_stake=stake, p1_tipico=row.get("p1_market"))
        stake_zero = float(strategy.stake_zero or 0.0)
        stake_two = float(strategy.stake_two_plus or 0.0)
        win_roi = strategy.win_roi
        if outcome == "H2_GOALS_0":
            payout = float(strategy.payout_zero or 0.0)
        elif outcome == "H2_GOALS_2_PLUS":
            payout = float(strategy.payout_two_plus or 0.0)
    pnl = payout - stake - cost
    covered_hit = (
        outcome == "H2_GOALS_0" if variant.bet_form == "SINGLE_ZERO" else
        outcome == "H2_GOALS_2_PLUS" if variant.bet_form == "SINGLE_TWO_PLUS" else
        outcome in {"H2_GOALS_0", "H2_GOALS_2_PLUS"}
    )
    return {
        "variant_id": variant.variant_id,
        "variant_label": variant.label,
        "event_id": row.get("event_id"),
        "snapshot_id": row.get("snapshot_id"),
        "observed_at_utc": row.get("observed_at_utc"),
        "observed_date_utc": row.get("observed_date_utc"),
        "competition_country": row.get("competition_country"),
        "competition_name": row.get("competition_name"),
        "home_team": row.get("home_team"),
        "away_team": row.get("away_team"),
        "ht_score": f"{row.get('ht_score_home', '-')}:{row.get('ht_score_away', '-')}" if row.get("ht_score_home") is not None else None,
        "q_zero": q0,
        "q_two_plus": q2,
        "p1_market": row.get("p1_market"),
        "p1_break_even": row.get("p1_break_even"),
        "p1_buffer": row.get("p1_buffer"),
        "win_roi": win_roi,
        "outcome_class": outcome,
        "h2_goals": row.get("h2_goals"),
        "stake_total": stake,
        "stake_zero": round(stake_zero, 2),
        "stake_two_plus": round(stake_two, 2),
        "payout": round(payout, 2),
        "cost": cost,
        "pnl": round(pnl, 2),
        "roi": pnl / stake if stake else None,
        "covered_hit": bool(covered_hit),
        "profitable_trade": pnl > 0,
        "quote_haircut": quote_haircut,
        "entry_policy": "HT_STABLE_ONCE",
        "evidence_level": row.get("evidence_level"),
    }


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def block_bootstrap_roi(trades: list[dict[str, Any]], *, seed: int = 6301,
                        iterations: int = 500) -> tuple[float | None, float | None]:
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        by_day[str(trade.get("observed_date_utc") or "UNKNOWN")].append(trade)
    days = sorted(by_day)
    if len(days) < 2:
        return None, None
    rng = random.Random(seed)
    rois: list[float] = []
    for _ in range(max(1, iterations)):
        sample = [days[rng.randrange(len(days))] for _ in days]
        selected = [trade for day in sample for trade in by_day[day]]
        stake = sum(float(item.get("stake_total") or 0) for item in selected)
        pnl = sum(float(item.get("pnl") or 0) for item in selected)
        if stake:
            rois.append(pnl / stake)
    return _percentile(rois, 0.025), _percentile(rois, 0.975)


def max_drawdown(trades: Iterable[dict[str, Any]]) -> float:
    balance = 0.0
    peak = 0.0
    drawdown = 0.0
    for trade in sorted(trades, key=lambda item: (str(item.get("observed_at_utc") or ""), str(item.get("event_id") or ""))):
        balance += float(trade.get("pnl") or 0)
        peak = max(peak, balance)
        drawdown = max(drawdown, peak - balance)
    return round(drawdown, 2)


def longest_loss_streak(trades: Iterable[dict[str, Any]]) -> int:
    current = longest = 0
    for trade in sorted(trades, key=lambda item: (str(item.get("observed_at_utc") or ""), str(item.get("event_id") or ""))):
        if float(trade.get("pnl") or 0) < 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def _numeric_values(rows: Iterable[dict[str, Any]], key: str) -> list[float]:
    values: list[float] = []
    for row in rows:
        value = row.get(key)
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            values.append(number)
    return values


def _median(rows: Iterable[dict[str, Any]], key: str) -> float | None:
    return _percentile(_numeric_values(rows, key), 0.5)


def summarise_trades(trades: list[dict[str, Any]], *, population: int,
                     unresolved: int, no_bet: int, seed: int = 6301,
                     bootstrap_iterations: int = 500,
                     candidate_rows: list[dict[str, Any]] | None = None,
                     void_trades: int = 0) -> dict[str, Any]:
    """Return transparent replay metrics for resolved trades and candidates.

    ``trades`` contains only settled rows.  ``candidate_rows`` is the set of
    historical entry decisions, including unresolved candidates, so the
    denominator cannot silently change when result coverage improves.
    """

    candidates = candidate_rows if candidate_rows is not None else trades
    count = len(trades)
    entry_count = len(candidates)
    stake = sum(float(item.get("stake_total") or 0) for item in trades)
    pnl = sum(float(item.get("pnl") or 0) for item in trades)
    outcomes = {name: sum(item.get("outcome_class") == name for item in trades) for name in (
        "H2_GOALS_0", "H2_GOALS_1", "H2_GOALS_2_PLUS",
    )}
    p1_count = outcomes["H2_GOALS_1"]
    p1_low, p1_high = wilson_interval(p1_count, count)
    expected_p1 = sum(float(item.get("p1_market") or 0) for item in trades) / count if count else None
    roi_low, roi_high = block_bootstrap_roi(trades, seed=seed, iterations=bootstrap_iterations)
    candidate_events = {item.get("event_id") for item in candidates if item.get("event_id") is not None}
    resolved_events = {item.get("event_id") for item in trades if item.get("event_id") is not None}
    candidate_days = {item.get("observed_date_utc") for item in candidates if item.get("observed_date_utc") is not None}
    resolved_days = {item.get("observed_date_utc") for item in trades if item.get("observed_date_utc") is not None}
    candidate_countries = {item.get("competition_country") for item in candidates}
    candidate_competitions = {
        (item.get("competition_country"), item.get("competition_name")) for item in candidates
    }
    gross_profit = sum(max(float(item.get("pnl") or 0), 0.0) for item in trades)
    gross_loss = sum(min(float(item.get("pnl") or 0), 0.0) for item in trades)
    if count and gross_loss < 0:
        profit_factor: float | str | None = gross_profit / abs(gross_loss)
    elif count:
        profit_factor = "NOT_FINITE"
    else:
        profit_factor = None
    temporal_status = "OK" if len(resolved_days) >= 14 else "TEMPORAL_EVIDENCE_INSUFFICIENT"
    evidence = {
        level: sum(item.get("evidence_level") == level for item in candidates)
        for level in ("A_VERIFIED_REPLAY", "B_LEGACY_EXPLORATORY", "C_UNUSABLE_OR_PENDING")
    }
    return {
        "population_entry_eligible": population,
        "no_bet": no_bet,
        "entry_candidates": entry_count,
        "entries": entry_count,
        "resolved_trades": count,
        "unresolved": unresolved,
        "void_trades": void_trades,
        "different_games": len(candidate_events),
        "resolved_different_games": len(resolved_events),
        "days": len(candidate_days),
        "resolved_days": len(resolved_days),
        "countries": len(candidate_countries),
        "competitions": len(candidate_competitions),
        "h2_goals_0": outcomes["H2_GOALS_0"],
        "h2_goals_1": outcomes["H2_GOALS_1"],
        "h2_goals_2_plus": outcomes["H2_GOALS_2_PLUS"],
        "observed_p1": p1_count / count if count else None,
        "observed_p1_low_95": p1_low,
        "observed_p1_high_95": p1_high,
        "expected_p1_mean": expected_p1,
        "p1_observed_minus_market": (p1_count / count - expected_p1) if count and expected_p1 is not None else None,
        "covered_hit_rate": sum(bool(item.get("covered_hit")) for item in trades) / count if count else None,
        "profitable_trade_rate": sum(bool(item.get("profitable_trade")) for item in trades) / count if count else None,
        "total_stake": round(stake, 2),
        "total_payout": round(sum(float(item.get("payout") or 0) for item in trades), 2),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "pnl": round(pnl, 2),
        "roi": pnl / stake if stake else None,
        "roi_bootstrap_low_95": roi_low,
        "roi_bootstrap_high_95": roi_high,
        "bootstrap_method": "UTC_DAY_BLOCK_RESAMPLING",
        "bootstrap_blocks": len(resolved_days),
        "temporal_evidence_status": temporal_status,
        "max_drawdown": max_drawdown(trades),
        "longest_loss_streak": longest_loss_streak(trades),
        "profitable_trades": sum(float(item.get("pnl") or 0) > 0 for item in trades),
        "losing_trades": sum(float(item.get("pnl") or 0) < 0 for item in trades),
        "zero_pnl_trades": sum(float(item.get("pnl") or 0) == 0 for item in trades),
        "profit_factor": profit_factor,
        "median_q_zero": _median(trades, "q_zero"),
        "median_q_two_plus": _median(trades, "q_two_plus"),
        "median_p1_break_even": _median(trades, "p1_break_even"),
        "median_p1_buffer": _median(trades, "p1_buffer"),
        "median_win_roi": _median(trades, "win_roi"),
        "median_payout": _median(trades, "payout"),
        "evidence_levels": evidence,
        "minimum_sample_status": "VERY_LOW_SAMPLE" if len(resolved_events) < 30 else "OK",
    }
