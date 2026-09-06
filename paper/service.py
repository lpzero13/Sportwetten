"""Persistent paper-trading orchestration, independent from Streamlit."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from config import Settings
from storage.database import Database

from .engine import settle_scores
from .models import PAPER_STRATEGY, PORTFOLIO_STATUSES, STRATEGY_FAMILIES, PaperPortfolio, SettlementResult, decimal_value


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

    @staticmethod
    def _validate_rules(values: Mapping[str, Any]) -> None:
        for key in ("starting_bankroll", "fixed_stake", "bankroll_percentage", "min_stake", "max_stake",
                    "minimum_win_roi", "minimum_p1_buffer", "maximum_tipico_p1", "minimum_q_zero",
                    "minimum_q_two_plus", "minimum_p1_break_even"):
            if values.get(key) is not None and decimal_value(values[key]) is None:
                raise ValueError(f"Ungültiger Zahlenwert: {key}")
        for key in ("maximum_tipico_p1", "minimum_p1_break_even"):
            if not 0 <= float(values.get(key, 0)) <= 1:
                raise ValueError(f"{key} muss zwischen 0 und 100 % liegen.")
        if not 0 <= int(values["entry_window_start_seconds"]) <= int(values["entry_window_end_seconds"]) <= 1800:
            raise ValueError("Das Einstiegsfenster muss aufsteigend zwischen 0 und 1800 Sekunden liegen.")
        if int(values["max_quote_age_seconds"]) < 1:
            raise ValueError("Das maximale Quotenalter muss mindestens eine Sekunde betragen.")
        if values.get("stake_mode") not in {"FIXED", "BANKROLL_PERCENTAGE"}:
            raise ValueError("Ungültiger Einsatzmodus")
        key = "fixed_stake" if values["stake_mode"] == "FIXED" else "bankroll_percentage"
        stake = decimal_value(values.get(key))
        if stake is None or stake <= 0 or key == "bankroll_percentage" and stake > 100:
            raise ValueError("Der Einsatz muss positiv sein; ein Bankroll-Anteil darf höchstens 100 % betragen.")
        if any(values.get(key) is not None and float(values[key]) < 0 for key in ("min_stake", "max_stake")):
            raise ValueError("Einsatzgrenzen dürfen nicht negativ sein.")
        if values.get("min_stake") is not None and values.get("max_stake") is not None and float(values["min_stake"]) > float(values["max_stake"]):
            raise ValueError("Der minimale Einsatz liegt über dem maximalen Einsatz.")

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
        family: str = "MARKET_ONLY",
        minimum_p1_break_even: float | Decimal = 0.0,
    ) -> PaperPortfolio:
        resolved_name = str(name).strip()
        if not resolved_name:
            raise ValueError("Portfolio name is required")
        if strategy_type != PAPER_STRATEGY:
            raise ValueError("V0.4 supports only ZERO_OR_2PLUS")
        if family not in STRATEGY_FAMILIES:
            raise ValueError("Unsupported strategy family")
        resolved_mode = str(stake_mode).upper().strip()
        if resolved_mode not in {"FIXED", "BANKROLL_PERCENTAGE"}:
            raise ValueError("Stake mode must be FIXED or BANKROLL_PERCENTAGE")
        start = decimal_value(starting_bankroll)
        if start is None or start <= 0:
            raise ValueError("Starting bankroll must be positive")
        start_seconds = int(entry_window_start_seconds)
        end_seconds = int(entry_window_end_seconds)
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
            "family": family,
            "minimum_p1_break_even": float(minimum_p1_break_even),
        }
        self._validate_rules(values)
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
        if "family" in values and values["family"] not in STRATEGY_FAMILIES:
            raise ValueError("Unsupported strategy family")
        current = self.portfolio(portfolio_id)
        if current is None:
            return None
        self._validate_rules({**current.config(), **values})
        rule_change = any(key not in {"name", "status", "updated_at"} for key in values) or selected_competition_ids is not None
        if rule_change:
            values["version"] = current.version + 1
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

    def process_signals(
        self, *, now: datetime | None = None, evaluation_limit: int = 500,
    ) -> dict[str, int]:
        from .entries import process_entries

        # Current HT state is bounded by time; never silently truncate games.
        return process_entries(self, now=now)

    def _settlement_for_trade(
        self,
        trade: Any,
        *,
        final_score_home: int | None,
        final_score_away: int | None,
        status: str | None,
        extra_time: bool | None,
        penalties: bool | None,
        regulation_home: int | None = None,
        regulation_away: int | None = None,
        regulation_confirmed: bool = False,
    ) -> SettlementResult:
        return settle_scores(
            halftime_home=_int(trade["ht_score_home"]),
            halftime_away=_int(trade["ht_score_away"]),
            final_home=final_score_home,
            final_away=final_score_away,
            status=status,
            extra_time=extra_time,
            penalties=penalties,
            regulation_home=regulation_home, regulation_away=regulation_away,
            regulation_confirmed=regulation_confirmed,
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
        regulation_home: int | None = None,
        regulation_away: int | None = None,
        regulation_confirmed: bool = False,
        evidence: Mapping[str, Any] | None = None,
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
            regulation_home=regulation_home, regulation_away=regulation_away,
            regulation_confirmed=regulation_confirmed,
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
        from .journal import PaperJournal
        PaperJournal(self.database).settlement_audit(paper_trade_id, payload, dict(evidence or {
            "final_home": final_score_home, "final_away": final_score_away,
            "status": status, "extra_time": extra_time, "penalties": penalties,
            "regulation_home": regulation_home, "regulation_away": regulation_away,
            "regulation_confirmed": regulation_confirmed,
        }))
        _, row = self.database.settle_paper_trade(paper_trade_id, payload)
        return row

    def settle_open_trades(
        self, *, resolver: Callable[[str], Mapping[str, Any] | None] | None = None,
        now: datetime | None = None,
    ) -> dict[str, int]:
        from .results import process_results

        return process_results(self, resolver=resolver, now=now)

    def worker_once(
        self,
        *,
        now: datetime | None = None,
        resolver: Callable[[str], Mapping[str, Any] | None] | None = None,
        include_settlement: bool = True,
    ) -> dict[str, int]:
        started = _now_iso()
        signal_result: dict[str, int] = {}
        settlement_result: dict[str, int] = {}
        errors = 0
        error_message: str | None = None
        try:
            signal_result = self.process_signals(now=now)
        except Exception as exc:  # service loop must survive one bad event
            errors = 1
            error_message = str(exc)
            self.logger.exception("Paper worker iteration failed")
        try:
            if include_settlement:
                settlement_result = self.settle_open_trades(resolver=resolver, now=now)
        except Exception as exc:
            errors += 1
            error_message = str(exc)
            self.logger.exception("Paper settlement iteration failed")
        errors += signal_result.get("entry_errors", 0) + settlement_result.get("settlement_errors", 0)
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
