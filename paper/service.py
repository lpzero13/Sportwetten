"""Persistent paper-trading orchestration, independent from Streamlit."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from config import Settings
from intelligence.strategy import calculate_zero_or_2plus
from storage.database import Database

from .engine import evaluate_signal, settle_scores
from .models import PAPER_STRATEGY, PORTFOLIO_STATUSES, PaperPortfolio, SettlementResult


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class PaperTradingService:
    """Owns portfolio rules, signal admission and restart-safe settlement."""

    def __init__(
        self,
        database: Database,
        settings: Settings | None = None,
        *,
        logger: logging.Logger | None = None,
        entry_raw_store: Callable[[str], str | None] | None = None,
    ) -> None:
        self.database = database
        self.settings = settings or Settings()
        self.logger = logger or logging.getLogger("tipico")
        self.entry_raw_store = entry_raw_store

    def is_enabled(self) -> bool:
        return self.database.get_paper_runtime_setting("enabled", "1") == "1"

    def set_enabled(self, enabled: bool) -> None:
        self.database.set_paper_runtime_setting(
            "enabled", "1" if enabled else "0", _now_iso()
        )

    def portfolios(self, *, include_archived: bool = True) -> list[PaperPortfolio]:
        return [
            PaperPortfolio.from_row(
                row,
                self.database.paper_portfolio_competition_ids(str(row["portfolio_id"])),
            )
            for row in self.database.paper_portfolio_rows(include_archived=include_archived)
        ]

    def portfolio(self, portfolio_id: str) -> PaperPortfolio | None:
        row = self.database.paper_portfolio_row(portfolio_id)
        if row is None:
            return None
        return PaperPortfolio.from_row(
            row, self.database.paper_portfolio_competition_ids(portfolio_id)
        )

    @staticmethod
    def _validated_status(status: str) -> str:
        resolved = str(status).upper().strip()
        if resolved not in PORTFOLIO_STATUSES:
            raise ValueError(f"Unsupported paper portfolio status: {status}")
        return resolved

    def create_portfolio(
        self,
        *,
        name: str,
        starting_bankroll: float | Decimal,
        currency: str = "EUR",
        strategy_type: str = PAPER_STRATEGY,
        stake_mode: str = "FIXED",
        fixed_stake: float | Decimal | None = 10.0,
        bankroll_percentage: float | Decimal | None = None,
        min_stake: float | Decimal | None = None,
        max_stake: float | Decimal | None = None,
        minimum_win_roi: float | Decimal = 0.0,
        minimum_p1_buffer: float | Decimal = 0.0,
        maximum_tipico_p1: float | Decimal = 1.0,
        minimum_q_zero: float | Decimal = 1.0,
        minimum_q_two_plus: float | Decimal = 1.0,
        max_quote_age_seconds: int = 10,
        entry_window_start_seconds: int = 0,
        entry_window_end_seconds: int = 120,
        allow_all_competitions: bool = True,
        selected_competition_ids: list[str] | tuple[str, ...] = (),
        status: str = "ACTIVE",
    ) -> PaperPortfolio:
        resolved_name = str(name).strip()
        if not resolved_name:
            raise ValueError("Portfolio name is required")
        if strategy_type != PAPER_STRATEGY:
            raise ValueError("V0.4 supports only ZERO_OR_2PLUS")
        resolved_mode = str(stake_mode).upper().strip()
        if resolved_mode not in {"FIXED", "BANKROLL_PERCENTAGE"}:
            raise ValueError("Stake mode must be FIXED or BANKROLL_PERCENTAGE")
        start = Decimal(str(starting_bankroll))
        if start <= 0:
            raise ValueError("Starting bankroll must be positive")
        start_seconds = max(0, int(entry_window_start_seconds))
        end_seconds = max(start_seconds, int(entry_window_end_seconds))
        now = _now_iso()
        portfolio_id = f"pf-{uuid.uuid4().hex[:12]}"
        values = {
            "portfolio_id": portfolio_id,
            "name": resolved_name,
            "created_at": now,
            "updated_at": now,
            "starting_bankroll": float(start),
            "currency": str(currency or "EUR").upper(),
            "strategy_type": strategy_type,
            "stake_mode": resolved_mode,
            "fixed_stake": _float(fixed_stake),
            "bankroll_percentage": _float(bankroll_percentage),
            "min_stake": _float(min_stake),
            "max_stake": _float(max_stake),
            "minimum_win_roi": float(minimum_win_roi),
            "minimum_p1_buffer": float(minimum_p1_buffer),
            "maximum_tipico_p1": float(maximum_tipico_p1),
            "minimum_q_zero": float(minimum_q_zero),
            "minimum_q_two_plus": float(minimum_q_two_plus),
            "max_quote_age_seconds": max(1, int(max_quote_age_seconds)),
            "entry_window_start_seconds": start_seconds,
            "entry_window_end_seconds": end_seconds,
            "allow_all_competitions": int(bool(allow_all_competitions)),
            "status": self._validated_status(status),
            "version": 1,
        }
        self.database.insert_paper_portfolio(values, selected_competition_ids)
        portfolio = self.portfolio(portfolio_id)
        if portfolio is None:
            raise RuntimeError("Could not load created paper portfolio")
        return portfolio

    def update_portfolio(
        self,
        portfolio_id: str,
        *,
        selected_competition_ids: list[str] | tuple[str, ...] | None = None,
        **values: Any,
    ) -> PaperPortfolio | None:
        if "status" in values:
            values["status"] = self._validated_status(values["status"])
        values["updated_at"] = _now_iso()
        row = self.database.update_paper_portfolio(
            portfolio_id, values, selected_competition_ids
        )
        if row is None:
            return None
        return self.portfolio(portfolio_id)

    def set_portfolio_status(self, portfolio_id: str, status: str) -> PaperPortfolio | None:
        return self.update_portfolio(portfolio_id, status=status)

    def record_manual_adjustment(
        self,
        portfolio_id: str,
        amount: float | Decimal,
        *,
        note: str = "Manuelle Paper-Bankroll-Anpassung",
    ) -> float:
        """Add a positive/negative manual ledger entry without touching trades."""

        resolved_amount = float(Decimal(str(amount)))
        with self.database._lock:  # noqa: SLF001 - atomic ledger operation
            self.database.connection.execute("BEGIN IMMEDIATE")
            try:
                portfolio = self.database.connection.execute(
                    "SELECT portfolio_id FROM paper_portfolios WHERE portfolio_id = ?",
                    (str(portfolio_id),),
                ).fetchone()
                if portfolio is None:
                    raise KeyError(f"Unknown paper portfolio: {portfolio_id}")
                row = self.database.connection.execute(
                    """
                    SELECT p.starting_bankroll + COALESCE(SUM(t.amount), 0) AS balance
                    FROM paper_portfolios p
                    LEFT JOIN paper_bankroll_transactions t
                      ON t.portfolio_id = p.portfolio_id
                    WHERE p.portfolio_id = ? GROUP BY p.portfolio_id
                    """,
                    (str(portfolio_id),),
                ).fetchone()
                before = float(row["balance"] or 0)
                after = before + resolved_amount
                key = f"manual:{portfolio_id}:{uuid.uuid4().hex}"
                self.database.connection.execute(
                    """
                    INSERT INTO paper_bankroll_transactions (
                        transaction_id, portfolio_id, paper_trade_id, created_at,
                        transaction_type, amount, balance_before, balance_after,
                        idempotency_key, note
                    ) VALUES (?, ?, NULL, ?, 'MANUAL_ADJUSTMENT', ?, ?, ?, ?, ?)
                    """,
                    (f"tx-{uuid.uuid4().hex}", str(portfolio_id), _now_iso(),
                     resolved_amount, before, after, key, note),
                )
                self.database.connection.commit()
                return after
            except Exception:
                self.database.connection.rollback()
                raise

    @staticmethod
    def _quote_row(
        rows: list[Any],
        quote: float | None,
    ) -> Any | None:
        if not rows:
            return None
        if quote is None:
            return rows[0]
        return min(rows, key=lambda row: abs(float(row["odds"]) - quote))

    def _entry_quotes(self, evaluation: Any) -> tuple[Any | None, Any | None]:
        event_id = str(evaluation["event_id"])
        observed_at = str(evaluation["observed_at"])
        zero_rows = self.database.current_canonical_quotes_for_evaluation(
            event_id,
            ("REMAINING_TOTAL_UNDER", "NEXT_GOAL_NONE", "MATCH_TOTAL_UNDER"),
        )
        if not zero_rows:
            zero_rows = self.database.canonical_quotes_for_evaluation(
            event_id,
            observed_at,
            ("REMAINING_TOTAL_UNDER", "NEXT_GOAL_NONE", "MATCH_TOTAL_UNDER"),
            )
        two_rows = self.database.current_canonical_quotes_for_evaluation(
            event_id,
            ("REMAINING_TOTAL_OVER", "MATCH_TOTAL_OVER"),
        )
        if not two_rows:
            two_rows = self.database.canonical_quotes_for_evaluation(
            event_id,
            observed_at,
            ("REMAINING_TOTAL_OVER", "MATCH_TOTAL_OVER"),
            )
        return (
            self._quote_row(zero_rows, _float(evaluation["q_zero"])),
            self._quote_row(two_rows, _float(evaluation["q_two_plus"])),
        )

    def _entry_window_age(self, evaluation: Any, now: datetime) -> float | None:
        anchor = _parse_iso(self.database.first_halftime_observed_at(str(evaluation["event_id"])))
        if anchor is None:
            # Direct/manual analyses may not have an event-state row. Their
            # evaluation timestamp is the only honest entry anchor available.
            anchor = _parse_iso(evaluation["observed_at"])
        if anchor is None:
            return None
        return (now - anchor).total_seconds()

    def _log_signal(
        self,
        portfolio: PaperPortfolio,
        evaluation: Any,
        decision: str,
        reason: str,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.database.log_paper_signal(
            {
                "portfolio_id": portfolio.portfolio_id,
                "event_id": str(evaluation["event_id"]),
                "evaluation_id": evaluation["evaluation_id"] or 0,
                "observed_at": str(evaluation["observed_at"]),
                "decision": decision,
                "reason": reason,
                "details": dict(details or {}),
            }
        )

    def process_signals(
        self,
        *,
        now: datetime | None = None,
        evaluation_limit: int = 500,
    ) -> dict[str, int]:
        """Evaluate recent HT rows and create at most one trade per event/portfolio."""

        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        result = {"evaluations_seen": 0, "signals_accepted": 0, "trades_created": 0, "rejected": 0}
        if not self.is_enabled():
            return result
        portfolios = [item for item in self.portfolios(include_archived=False) if item.status == "ACTIVE"]
        if not portfolios:
            return result
        max_window = max(item.entry_window_end_seconds for item in portfolios)
        since = (current - timedelta(seconds=max_window + 900)).isoformat()
        evaluations = self.database.recent_strategy_evaluations(
            strategy_type=PAPER_STRATEGY,
            since=since,
            limit=evaluation_limit,
        )
        # The default V0.4 entry policy is first-valid, not latest-valid.
        # The database query is newest-first for UI use, so restore chronology
        # before the idempotent entry check below.
        evaluations = sorted(
            evaluations,
            key=lambda row: (
                str(row["observed_at"]),
                int(row["evaluation_id"] or 0),
            ),
        )
        result["evaluations_seen"] = len(evaluations)
        for evaluation in evaluations:
            observed = _parse_iso(evaluation["observed_at"])
            if observed is None:
                continue
            for portfolio in portfolios:
                if (
                    not portfolio.allow_all_competitions
                    and str(evaluation["competition_id"] or "")
                    not in portfolio.selected_competition_ids
                ):
                    self._log_signal(portfolio, evaluation, "REJECTED", "COMPETITION_NOT_ALLOWED")
                    result["rejected"] += 1
                    continue
                age = self._entry_window_age(evaluation, current)
                if age is None:
                    self._log_signal(portfolio, evaluation, "REJECTED", "HALF_TIME_ANCHOR_UNKNOWN")
                    result["rejected"] += 1
                    continue
                if age < portfolio.entry_window_start_seconds:
                    self._log_signal(portfolio, evaluation, "REJECTED", "ENTRY_WINDOW_NOT_OPEN", {"age_seconds": age})
                    result["rejected"] += 1
                    continue
                if age > portfolio.entry_window_end_seconds:
                    self._log_signal(portfolio, evaluation, "REJECTED", "ENTRY_WINDOW_EXPIRED", {"age_seconds": age})
                    result["rejected"] += 1
                    continue
                zero_quote, two_quote = self._entry_quotes(evaluation)
                quote_age_zero = max(0.0, (current - (_parse_iso(zero_quote["observed_at"]) or current)).total_seconds()) if zero_quote else None
                quote_age_two = max(0.0, (current - (_parse_iso(two_quote["observed_at"]) or current)).total_seconds()) if two_quote else None
                available = self.database.paper_balance(portfolio.portfolio_id)
                decision = evaluate_signal(
                    portfolio,
                    evaluation,
                    quote_age_zero_seconds=quote_age_zero,
                    quote_age_two_plus_seconds=quote_age_two,
                    available_bankroll=available,
                )
                if not decision.accepted or decision.stake is None:
                    self._log_signal(portfolio, evaluation, "REJECTED", decision.reason, decision.details)
                    result["rejected"] += 1
                    continue
                result["signals_accepted"] += 1
                q_zero = _float(evaluation["q_zero"])
                q_two = _float(evaluation["q_two_plus"])
                strategy = calculate_zero_or_2plus(
                    q_zero, q_two, total_stake=float(decision.stake),
                    p1_tipico=_float(evaluation["p1_tipico"]),
                    source_zero=str(evaluation["source_zero"] or "Tipico"),
                    source_two_plus=str(evaluation["source_two_plus"] or "Tipico"),
                )
                entry_raw_path: str | None = None
                if self.settings.raw_paper_entry and self.entry_raw_store is not None:
                    try:
                        entry_raw_path = self.entry_raw_store(str(evaluation["event_id"]))
                    except Exception as exc:  # raw audit must not block the trade
                        self.logger.warning(
                            "Paper entry raw capture failed for %s: %s",
                            evaluation["event_id"],
                            exc,
                        )
                snapshot = {
                    "paper_trade_id": f"pt-{uuid.uuid4().hex[:16]}",
                    "portfolio_id": portfolio.portfolio_id,
                    "event_id": str(evaluation["event_id"]),
                    "competition_id": evaluation["competition_id"],
                    "competition_name": str(evaluation["competition_name"] or "Unbekannter Wettbewerb"),
                    "competition_country": evaluation["competition_country"],
                    "created_at": current.isoformat(),
                    "strategy_evaluation_id": (
                        int(evaluation["evaluation_id"])
                        if evaluation["evaluation_id"] is not None
                        else None
                    ),
                    "strategy_type": str(evaluation["strategy_type"]),
                    "strategy_version": str(evaluation["strategy_version"]),
                    "normalizer_version": str(evaluation["normalizer_version"]),
                    "home_team": str(evaluation["home_team"] or "Unbekannt"),
                    "away_team": str(evaluation["away_team"] or "Unbekannt"),
                    "ht_score_home": _int(evaluation["event_ht_score_home"]),
                    "ht_score_away": _int(evaluation["event_ht_score_away"]),
                    "zero_market_id": zero_quote["market_id"] if zero_quote else None,
                    "zero_outcome_id": zero_quote["outcome_id"] if zero_quote else None,
                    "zero_market_type": zero_quote["raw_market_type"] if zero_quote else None,
                    "zero_market_caption": zero_quote["raw_market_caption"] if zero_quote else None,
                    "zero_outcome_caption": zero_quote["raw_outcome_caption"] if zero_quote else None,
                    "q_zero": q_zero,
                    "zero_quote_observed_at": zero_quote["observed_at"] if zero_quote else evaluation["observed_at"],
                    "zero_quote_age_seconds": decision.quote_age_zero_seconds,
                    "two_plus_market_id": two_quote["market_id"] if two_quote else None,
                    "two_plus_outcome_id": two_quote["outcome_id"] if two_quote else None,
                    "two_plus_market_type": two_quote["raw_market_type"] if two_quote else None,
                    "two_plus_market_caption": two_quote["raw_market_caption"] if two_quote else None,
                    "two_plus_outcome_caption": two_quote["raw_outcome_caption"] if two_quote else None,
                    "q_two_plus": q_two,
                    "two_plus_quote_observed_at": two_quote["observed_at"] if two_quote else evaluation["observed_at"],
                    "two_plus_quote_age_seconds": decision.quote_age_two_plus_seconds,
                    "stake_total": strategy.total_stake,
                    "stake_zero": strategy.stake_zero,
                    "stake_two_plus": strategy.stake_two_plus,
                    "payout_zero": strategy.payout_zero,
                    "payout_two_plus": strategy.payout_two_plus,
                    "p_zero": _float(evaluation["p_zero"]),
                    "p_one": _float(evaluation["p_one"] or evaluation["p1_tipico"]),
                    "p_two_plus": _float(evaluation["p_two_plus"]),
                    "p1_max": strategy.p1_max,
                    "p1_tipico": strategy.p1_tipico,
                    "p1_buffer": strategy.p1_buffer,
                    "win_roi": strategy.win_roi,
                    "entry_raw_payload_path": entry_raw_path,
                    "bankroll_before": available,
                    "bankroll_after": available - float(decision.stake),
                    "rank": 1,
                    "status": "OPEN",
                    "reservation_transaction_id": f"tx-{uuid.uuid4().hex}",
                    "reservation_idempotency_key": f"reserve:{portfolio.portfolio_id}:{evaluation['event_id']}:{evaluation['strategy_type']}",
                }
                snapshot["entry_snapshot"] = {
                    key: value for key, value in snapshot.items()
                    if key not in {"reservation_transaction_id", "reservation_idempotency_key", "entry_snapshot"}
                }
                created, _, reason = self.database.reserve_paper_trade(snapshot)
                self._log_signal(
                    portfolio,
                    evaluation,
                    "ACCEPTED" if created else "SKIPPED",
                    reason,
                    {"stake": float(decision.stake), "age_seconds": age},
                )
                if created:
                    entry_evaluation_id = self.database.record_strategy_evaluation_event(
                        dict(evaluation),
                        trigger_type="PAPER_TRADE_ENTRY",
                        is_eligible=True,
                    )
                    self.database.attach_strategy_evaluation_to_paper_trade(
                        str(snapshot["paper_trade_id"]),
                        entry_evaluation_id,
                    )
                    self.database.mark_strategy_entry_evaluation(
                        str(evaluation["event_id"]),
                        str(evaluation["strategy_type"]),
                        entry_evaluation_id,
                        str(evaluation["observed_at"]),
                    )
                    result["trades_created"] += 1
        return result

    def _settlement_for_trade(
        self,
        trade: Any,
        *,
        final_score_home: int | None,
        final_score_away: int | None,
        status: str | None,
        extra_time: bool | None,
        penalties: bool | None,
    ) -> SettlementResult:
        return settle_scores(
            halftime_home=_int(trade["ht_score_home"]),
            halftime_away=_int(trade["ht_score_away"]),
            final_home=final_score_home,
            final_away=final_score_away,
            status=status,
            extra_time=extra_time,
            penalties=penalties,
        )

    def settle_trade(
        self,
        paper_trade_id: str,
        *,
        final_score_home: int | None,
        final_score_away: int | None,
        status: str = "FINISHED",
        extra_time: bool | None = False,
        penalties: bool | None = False,
        settled_at: str | None = None,
    ) -> Any:
        trade = self.database.paper_trade_row(paper_trade_id)
        if trade is None:
            raise KeyError(f"Unknown paper trade: {paper_trade_id}")
        result = self._settlement_for_trade(
            trade,
            final_score_home=final_score_home,
            final_score_away=final_score_away,
            status=status,
            extra_time=extra_time,
            penalties=penalties,
        )
        if result.status == "WIN_ZERO":
            return_amount = Decimal(str(trade["payout_zero"] or 0))
        elif result.status == "WIN_TWO_PLUS":
            return_amount = Decimal(str(trade["payout_two_plus"] or 0))
        else:
            return_amount = Decimal("0")
        pnl = Decimal("0") if result.status in {"VOID", "UNRESOLVED"} else return_amount - Decimal(str(trade["stake_total"] or 0))
        payload = {
            "status": result.status,
            "second_half_goals": result.second_half_goals,
            "reason": result.reason,
            "return_amount": float(return_amount),
            "pnl": float(pnl),
            "final_score_home": result.final_score_home,
            "final_score_away": result.final_score_away,
            "settled_at": settled_at or _now_iso(),
            "transaction_id": f"tx-{uuid.uuid4().hex}",
            "idempotency_key": f"settle:{paper_trade_id}",
        }
        _, row = self.database.settle_paper_trade(paper_trade_id, payload)
        return row

    def settle_open_trades(
        self,
        *,
        resolver: Callable[[str], Mapping[str, Any] | None] | None = None,
    ) -> dict[str, int]:
        result = {"open_seen": 0, "settled": 0, "unresolved": 0}
        for trade in self.database.paper_trade_rows(status="OPEN", limit=1000):
            result["open_seen"] += 1
            final = self.database.final_snapshot_for_event(str(trade["event_id"]))
            result_row = self.database.match_result_for_event(str(trade["event_id"]))
            values: Mapping[str, Any] | None = None
            if final is not None:
                values = {
                    "final_score_home": final["score_home"],
                    "final_score_away": final["score_away"],
                    "status": final["match_status"] or "FINISHED",
                    "extra_time": False,
                    "penalties": False,
                }
            elif result_row is not None:
                values = {
                    "final_score_home": result_row["ft_home"],
                    "final_score_away": result_row["ft_away"],
                    "status": result_row["final_status"] or "FINISHED",
                    "extra_time": result_row["extra_time"],
                    "penalties": result_row["penalties"],
                }
            elif resolver is not None:
                values = resolver(str(trade["event_id"]))
            if not values:
                continue
            row = self.settle_trade(
                str(trade["paper_trade_id"]),
                final_score_home=_int(values.get("final_score_home")),
                final_score_away=_int(values.get("final_score_away")),
                status=str(values.get("status") or "UNKNOWN"),
                extra_time=values.get("extra_time"),
                penalties=values.get("penalties"),
            )
            if row is None:
                continue
            if str(row["status"]) == "UNRESOLVED":
                result["unresolved"] += 1
            else:
                result["settled"] += 1
        return result

    def worker_once(
        self,
        *,
        now: datetime | None = None,
        resolver: Callable[[str], Mapping[str, Any] | None] | None = None,
    ) -> dict[str, int]:
        started = _now_iso()
        signal_result: dict[str, int] = {}
        settlement_result: dict[str, int] = {}
        errors = 0
        error_message: str | None = None
        try:
            signal_result = self.process_signals(now=now)
            settlement_result = self.settle_open_trades(resolver=resolver)
        except Exception as exc:  # service loop must survive one bad event
            errors = 1
            error_message = str(exc)
            self.logger.exception("Paper worker iteration failed")
        finished = _now_iso()
        with self.database._lock:  # noqa: SLF001 - worker heartbeat transaction
            with self.database.connection:
                self.database.connection.execute(
                    """
                    INSERT INTO paper_worker_runs (
                        started_at, finished_at, signals_seen, trades_created,
                        trades_settled, errors, status, error_message
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        started, finished, signal_result.get("evaluations_seen", 0),
                        signal_result.get("trades_created", 0),
                        settlement_result.get("settled", 0), errors,
                        "ERROR" if errors else "OK", error_message,
                    ),
                )
                self.database.connection.execute(
                    """
                    INSERT INTO paper_runtime_settings (setting_key, setting_value, updated_at)
                    VALUES ('worker_last_seen_at', ?, ?)
                    ON CONFLICT(setting_key) DO UPDATE SET
                        setting_value = excluded.setting_value,
                        updated_at = excluded.updated_at
                    """,
                    (finished, finished),
                )
        return {
            **signal_result,
            **settlement_result,
            "errors": errors,
        }
