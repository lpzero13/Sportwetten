"""Consume current HT observations once per strategy, with exact entry evidence."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import uuid
from typing import Any

from intelligence.strategy import calculate_zero_or_2plus
from .engine import evaluate_signal
from .journal import PaperJournal
from .market import digest, is_halftime, timestamp, validate_entry


def process_entries(service: Any, now: datetime | None = None) -> dict[str, int]:
    realtime = now is None
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    db = service.database
    journal = PaperJournal(db)
    result = {"evaluations_seen": 0, "signals_accepted": 0, "trades_created": 0, "rejected": 0, "entry_errors": 0}
    if not service.is_enabled():
        return result
    portfolios = [p for p in service.portfolios(include_archived=False) if p.status == "ACTIVE"]
    if not portfolios:
        return result
    since = (now - timedelta(seconds=max(p.entry_window_end_seconds for p in portfolios) + 900)).isoformat()
    contexts = journal.current(since)
    result["evaluations_seen"] = len(contexts)
    with db._lock:
        existing = {(r["portfolio_id"], r["event_id"]): dict(r) for r in db.connection.execute(
            "SELECT * FROM paper_trades WHERE created_at >= ?", (since,)
        )}
        event_states = {str(r["event_id"]): dict(r) for r in db.connection.execute(
            "SELECT * FROM current_event_state WHERE observed_at >= ?", (since,)
        )}
    for context in contexts:
        event = context["event"]
        event_id = context["event_id"]
        raw_path = None
        raw_status = None
        for portfolio in portfolios:
            if realtime:
                now = datetime.now(timezone.utc)
            if (portfolio.portfolio_id, event_id) in existing:
                # Recover a missing decision after a crash between reservation
                # and the journal append, using the ORIGINAL entry evidence.
                import json
                trade = existing[(portfolio.portfolio_id, event_id)]
                entry = json.loads(trade["entry_snapshot_json"])
                if entry.get("market_context") and entry.get("config_hash") == portfolio.config_hash:
                    journal.decision(portfolio, entry["market_context"], now=trade["created_at"],
                                     decision="BET", reason="ENTRY_CREATED", details={"trade_id": trade["paper_trade_id"]})
                continue
            try:
                reason, age = validate_entry(context, now, portfolio.max_quote_age_seconds)
                latest = event_states.get(event_id)
                if latest and timestamp(latest["observed_at"]) and timestamp(context["observed_at"]) and timestamp(latest["observed_at"]) >= timestamp(context["observed_at"]):
                    if not is_halftime(latest):
                        reason = "NOT_HALFTIME"
                    elif (latest.get("score_home"), latest.get("score_away")) != (event["score_home"], event["score_away"]):
                        reason = "SCORE_CHANGED_SINCE_OBSERVATION"
                anchor = timestamp(context.get("halftime_observed_at"))
                window_age = (now - anchor).total_seconds() if anchor else None
                if reason == "OK":
                    if window_age is None:
                        reason = "HALF_TIME_ANCHOR_UNKNOWN"
                    elif window_age < portfolio.entry_window_start_seconds:
                        reason = "ENTRY_WINDOW_NOT_OPEN"
                    elif window_age > portfolio.entry_window_end_seconds:
                        reason = "ENTRY_WINDOW_EXPIRED"
                    elif not portfolio.allow_all_competitions and str(event.get("competition_id")) not in portfolio.selected_competition_ids:
                        reason = "COMPETITION_NOT_ALLOWED"
                probability = context["probability"]
                p1 = probability.get("p1") if probability["status"] == "OK" else None
                zero, two = context.get("zero"), context.get("two_plus")
                available = db.paper_balance(portfolio.portfolio_id)
                strategy = calculate_zero_or_2plus(
                    zero["odds"] if zero else None, two["odds"] if two else None,
                    total_stake=10, p1_tipico=p1,
                )
                decision = evaluate_signal(portfolio, asdict(strategy), quote_age_zero_seconds=age,
                                           quote_age_two_plus_seconds=age, available_bankroll=available)
                if reason == "OK" and not decision.accepted:
                    reason = decision.reason
                if reason != "OK":
                    journal.decision(portfolio, context, now=now.isoformat(), decision="NO_BET", reason=reason,
                                     details={"quote_age_seconds": age, "window_age_seconds": window_age,
                                              "p1_break_even": strategy.p1_max, "market_p1": p1,
                                              "market_probability_status": probability["status"]})
                    result["rejected"] += 1
                    continue
                result["signals_accepted"] += 1
                strategy = calculate_zero_or_2plus(zero["odds"], two["odds"], total_stake=float(decision.stake), p1_tipico=p1)
                if raw_status is None:
                    try:
                        raw_path, raw_status = journal.archive_entry_raw(context, service.settings)
                    except Exception as exc:
                        raw_status = "RAW_STORE_FAILED"
                        service.logger.warning("Paper raw archive failed for %s: %s", event_id, exc)
                if realtime:
                    now = datetime.now(timezone.utc)
                    reason, age = validate_entry(context, now, portfolio.max_quote_age_seconds)
                    if reason != "OK":
                        journal.decision(portfolio, context, now=now.isoformat(), decision="NO_BET", reason=reason)
                        result["rejected"] += 1
                        continue
                trade_id = f"pt-{uuid.uuid4().hex[:16]}"
                values = {
                    "paper_trade_id": trade_id, "portfolio_id": portfolio.portfolio_id, "event_id": event_id,
                    "created_at": now.isoformat(), "strategy_evaluation_id": None,
                    "strategy_type": portfolio.strategy_type, "strategy_version": context["strategy_version"],
                    "normalizer_version": context["normalizer_version"], "family": portfolio.family,
                    "observation_id": digest(context), "portfolio_version": portfolio.version,
                    "config_hash": portfolio.config_hash, "q_zero": zero["odds"], "q_two_plus": two["odds"],
                    "stake_total": strategy.total_stake, "stake_zero": strategy.stake_zero,
                    "stake_two_plus": strategy.stake_two_plus, "payout_zero": strategy.payout_zero,
                    "payout_two_plus": strategy.payout_two_plus, "p_zero": probability.get("p0"),
                    "p_one": p1, "p_two_plus": probability.get("p2_plus"), "p1_max": strategy.p1_max,
                    "p1_tipico": p1, "p1_buffer": strategy.p1_buffer, "win_roi": strategy.win_roi,
                    "bankroll_before": available, "bankroll_after": available - strategy.total_stake,
                    "rank": 1, "status": "OPEN", "entry_raw_payload_path": raw_path,
                    "reservation_transaction_id": f"tx-{uuid.uuid4().hex}",
                    "reservation_idempotency_key": f"reserve:{portfolio.portfolio_id}:{event_id}:{portfolio.strategy_type}",
                }
                for key in ("competition_id", "competition_name", "competition_country", "home_team", "away_team", "ht_score_home", "ht_score_away"):
                    values[key] = event.get(key)
                for prefix, quote in (("zero", zero), ("two_plus", two)):
                    for target, source in (("market_id", "market_id"), ("outcome_id", "outcome_id"),
                                           ("market_type", "raw_market_type"), ("market_caption", "raw_market_caption"),
                                           ("outcome_caption", "raw_outcome_caption"), ("quote_observed_at", "observed_at")):
                        values[f"{prefix}_{target}"] = quote[source]
                    values[f"{prefix}_quote_age_seconds"] = age
                values["entry_snapshot"] = {**values, "market_context": context, "strategy_config": portfolio.config(), "raw_capture_status": raw_status}
                created, row, admission = db.reserve_paper_trade(values)
                journal.decision(portfolio, context, now=now.isoformat(), decision="BET" if created else "NO_BET",
                                 reason="ENTRY_CREATED" if created else admission, details={"trade_id": trade_id if created else None})
                if created:
                    existing[(portfolio.portfolio_id, event_id)] = dict(row)
                    result["trades_created"] += 1
            except Exception as exc:
                result["entry_errors"] += 1
                service.logger.exception("Paper entry failed for %s / %s", event_id, portfolio.portfolio_id)
                journal.decision(portfolio, context, now=now.isoformat(), decision="ERROR", reason="ENTRY_PROCESSING_ERROR",
                                 details={"error": str(exc)})
    return result
