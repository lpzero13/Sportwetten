"""Read-only analytics for paper portfolios."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any, Iterable


SETTLED = {"WIN_ZERO", "WIN_TWO_PLUS", "LOSS_MIDDLE"}
WINS = {"WIN_ZERO", "WIN_TWO_PLUS"}


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _pct(value: float | None) -> float | None:
    return None if value is None else value * 100.0


def _row_dict(row: Any) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()} if hasattr(row, "keys") else dict(row)


def _streaks(rows: list[dict[str, Any]]) -> tuple[int, int]:
    longest_win = longest_loss = current_win = current_loss = 0
    for row in rows:
        status = str(row.get("status") or "")
        if status in WINS:
            current_win += 1
            current_loss = 0
        elif status == "LOSS_MIDDLE":
            current_loss += 1
            current_win = 0
        else:
            current_win = current_loss = 0
        longest_win = max(longest_win, current_win)
        longest_loss = max(longest_loss, current_loss)
    return longest_win, longest_loss


def portfolio_analytics(
    portfolio: Any,
    trade_rows: Iterable[Any],
    *,
    available_bankroll: float,
) -> dict[str, Any]:
    rows = [_row_dict(row) for row in trade_rows]
    open_rows = [row for row in rows if str(row.get("status") or "") == "OPEN"]
    settled_rows = [row for row in rows if str(row.get("status") or "") in SETTLED]
    wins = [row for row in settled_rows if str(row.get("status")) in WINS]
    losses = [row for row in settled_rows if str(row.get("status")) == "LOSS_MIDDLE"]
    void_rows = [row for row in rows if str(row.get("status") or "") == "VOID"]
    unresolved_rows = [row for row in rows if str(row.get("status") or "") == "UNRESOLVED"]
    pnl = sum(_number(row.get("pnl")) for row in settled_rows)
    settled_stake = sum(_number(row.get("stake_total")) for row in settled_rows)
    exposure = sum(_number(row.get("stake_total")) for row in open_rows)
    gross_profit = sum(max(0.0, _number(row.get("pnl"))) for row in settled_rows)
    gross_loss = sum(min(0.0, _number(row.get("pnl"))) for row in settled_rows)
    capital = float(available_bankroll) + exposure

    curve = [float(getattr(portfolio, "starting_bankroll", 0))]
    running = curve[0]
    peak = running
    max_drawdown = 0.0
    max_drawdown_pct = 0.0
    for row in sorted(settled_rows, key=lambda item: (str(item.get("settled_at") or ""), str(item.get("paper_trade_id")))):
        running += _number(row.get("pnl"))
        curve.append(running)
        peak = max(peak, running)
        drawdown = peak - running
        max_drawdown = max(max_drawdown, drawdown)
        if peak > 0:
            max_drawdown_pct = max(max_drawdown_pct, drawdown / peak * 100.0)
    longest_win, longest_loss = _streaks(
        sorted(settled_rows, key=lambda item: (str(item.get("settled_at") or ""), str(item.get("paper_trade_id"))))
    )
    return {
        "portfolio_id": str(getattr(portfolio, "portfolio_id", "")),
        "capital": capital,
        "available": float(available_bankroll),
        "exposure": exposure,
        "pnl": pnl,
        "roi": pnl / settled_stake if settled_stake else None,
        "total_trades": len(rows),
        "settled_trades": len(settled_rows),
        "open_trades": len(open_rows),
        "wins": len(wins),
        "losses": len(losses),
        "void": len(void_rows),
        "unresolved": len(unresolved_rows),
        "hit_rate": len(wins) / len(settled_rows) if settled_rows else None,
        "average_win": sum(_number(row.get("pnl")) for row in wins) / len(wins) if wins else None,
        "average_loss": sum(_number(row.get("pnl")) for row in losses) / len(losses) if losses else None,
        "profit_factor": gross_profit / abs(gross_loss) if gross_loss else None,
        "settled_stake": settled_stake,
        "max_drawdown": max_drawdown,
        "max_drawdown_pct": max_drawdown_pct / 100.0,
        "longest_win_streak": longest_win,
        "longest_loss_streak": longest_loss,
        "bankroll_curve": curve,
    }


def _bucket(value: Any, width: float = 0.05) -> str:
    number = _number(value)
    lower = int(number / width) * width
    upper = lower + width
    return f"{lower * 100:.0f}–{upper * 100:.0f}%"


def calibration_rows(trade_rows: Iterable[Any]) -> list[dict[str, Any]]:
    """Compare stored Tipico P1 buckets with the actual exactly-one result."""

    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in trade_rows:
        item = _row_dict(row)
        if str(item.get("status") or "") not in SETTLED or item.get("p1_tipico") is None:
            continue
        buckets[_bucket(item["p1_tipico"])].append(item)
    result: list[dict[str, Any]] = []
    for label, rows in buckets.items():
        exactly_one = sum(1 for row in rows if str(row.get("status")) == "LOSS_MIDDLE")
        observed = exactly_one / len(rows) if rows else None
        expected = sum(_number(row.get("p1_tipico")) for row in rows) / len(rows) if rows else None
        result.append({
            "P1-Bucket": label,
            "Trades": len(rows),
            "Tipico P1 Ø": expected,
            "Tatsächlich 1 Tor": observed,
            "Abweichung": (observed - expected) if observed is not None and expected is not None else None,
        })
    return sorted(result, key=lambda row: row["P1-Bucket"])


def grouped_trade_rows(trade_rows: Iterable[Any], dimension: str) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for raw in trade_rows:
        row = _row_dict(raw)
        if dimension == "competition":
            key = " / ".join(filter(None, [str(row.get("competition_name") or "—"), str(row.get("competition_country") or "—")]))
        elif dimension == "p1":
            key = _bucket(row.get("p1_tipico"))
        elif dimension == "p1_buffer":
            key = _bucket(row.get("p1_buffer"))
        elif dimension == "win_roi":
            key = _bucket(row.get("win_roi"))
        else:
            key = "—"
        groups[key].append(row)
    result: list[dict[str, Any]] = []
    for key, rows in sorted(groups.items()):
        settled = [row for row in rows if str(row.get("status")) in SETTLED]
        pnl = sum(_number(row.get("pnl")) for row in settled)
        stake = sum(_number(row.get("stake_total")) for row in settled)
        result.append({
            "Gruppe": key,
            "Trades": len(rows),
            "Abgerechnet": len(settled),
            "P/L": pnl,
            "ROI": pnl / stake if stake else None,
            "Trefferquote": sum(1 for row in settled if str(row.get("status")) in WINS) / len(settled) if settled else None,
        })
    return result
