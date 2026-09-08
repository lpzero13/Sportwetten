"""Resolve each match once, retry unknown results, and settle each ledger once."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import json
from typing import Any

from .engine import SettlementResult, settle_scores
from .journal import PaperJournal
from .market import timestamp


def classify(half_home: Any, half_away: Any, values: dict[str, Any]) -> Any:
    # V0.6.5 keeps FT visibility separate from H2 settlement permission.
    # A verified FT without a trustworthy HT, unknown scope or a conflict is
    # displayed and retained as evidence but must not settle a paper trade.
    result_status = str(values.get("result_status") or "").upper()
    if (
        ("result_use_ft" in values and values.get("result_use_ft") in (False, 0, "0"))
        or ("result_use_h2" in values and values.get("result_use_h2") in (False, 0, "0"))
        or result_status in {"CONFLICT", "PENDING", "PARTIAL", "UNAVAILABLE"}
    ):
        return SettlementResult(
            "UNRESOLVED",
            None,
            str(values.get("result_reason") or "H2_RESULT_NOT_RELEASED"),
            final_score_home=values.get("final_score_home"),
            final_score_away=values.get("final_score_away"),
        )
    return settle_scores(
        halftime_home=half_home, halftime_away=half_away,
        final_home=values.get("final_score_home"), final_away=values.get("final_score_away"),
        status=values.get("status", "UNKNOWN"), extra_time=values.get("extra_time"), penalties=values.get("penalties"),
        regulation_home=values.get("regulation_home"), regulation_away=values.get("regulation_away"),
        regulation_confirmed=values.get("regulation_confirmed") is True,
    )


def process_results(service: Any, resolver: Any = None, now: datetime | None = None) -> dict[str, int]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    db = service.database
    journal = PaperJournal(db)
    result = {"open_seen": 0, "settled": 0, "unresolved": 0, "settlement_errors": 0}
    with db._lock:
        trades = [dict(row) for row in db.connection.execute("SELECT * FROM paper_trades WHERE status='OPEN'")]
        # Retain outcomes for NO_BET as well as BET. One row/query per match,
        # regardless of how many portfolios evaluated the shared observation.
        observations = db.connection.execute(
            """SELECT o.payload_json FROM paper_observations o
               WHERE o.observation_id = (SELECT o2.observation_id FROM paper_observations o2
                 WHERE o2.event_id=o.event_id ORDER BY o2.observed_at, o2.observation_id LIMIT 1)"""
        ).fetchall()
        checks = {row["event_id"]: dict(row) for row in db.connection.execute("SELECT * FROM paper_result_checks")}
        live = {row["event_id"]: dict(row) for row in db.connection.execute("SELECT event_id, status, period, observed_at FROM current_event_state")}
    grouped = defaultdict(list)
    events = {}
    for row in observations:
        context = json.loads(row[0])
        events[context["event_id"]] = (context["event"].get("ht_score_home"), context["event"].get("ht_score_away"))
    for trade in trades:
        grouped[trade["event_id"]].append(trade)
        events[trade["event_id"]] = (trade["ht_score_home"], trade["ht_score_away"])
    result["open_seen"] = len(trades)
    network_calls = 0
    for event_id in sorted(events, key=lambda key: checks.get(key, {}).get("checked_at", "")):
        half_home, half_away = events[event_id]
        cached = checks.get(event_id)
        if cached and cached["status"] in {"RESOLVED", "VOID"} and not grouped[event_id]:
            continue
        try:
            values = json.loads(cached["evidence_json"]) if cached and cached["status"] in {"RESOLVED", "VOID"} else {}
            if not values:
                row = db.match_result_for_event(event_id)
                if row:
                    values = {"source": row["result_source"] or "TIPICO_ORIGINAL", "final_score_home": row["ft_home"],
                              "final_score_away": row["ft_away"], "status": row["final_status"],
                              "extra_time": row["extra_time"], "penalties": row["penalties"],
                              "ht_home": row["ht_home"], "ht_away": row["ht_away"],
                              "result_status": row["result_status"],
                              "result_use_ft": row["result_use_ft"],
                              "result_use_h2": row["result_use_h2"],
                              "result_reason": row["result_reason"],
                              "result_evidence_id": row["result_evidence_id"],
                              "result_revision": row["result_revision"],
                              "result_resolved_at": row["result_resolved_at"]}
                else:
                    row = db.final_snapshot_for_event(event_id)
                    if row:
                        values = {"source": "TIPICO_FINAL_SNAPSHOT", "final_score_home": row["score_home"],
                                  "final_score_away": row["score_away"], "status": row["match_status"],
                                  "extra_time": row["extra_time"], "penalties": row["penalties"],
                                  "ht_home": row["ht_score_home"], "ht_away": row["ht_score_away"],
                                  "result_source": "TIPICO_FINAL_SNAPSHOT",
                                  # A raw FINAL snapshot is not a finalized
                                  # result by itself.  The V0.6.5 worker must
                                  # validate it before paper settlement.
                                  "result_status": "PENDING",
                                  "result_use_ft": 0,
                                  "result_use_h2": 0,
                                  "result_reason": "FINAL_SNAPSHOT"}
            outcome = classify(half_home, half_away, values)
            due = not cached or timestamp(cached["next_check_at"]) <= now
            current = live.get(event_id)
            currently_playing = bool(current and timestamp(current["observed_at"]) and
                                     0 <= (now - timestamp(current["observed_at"])).total_seconds() < 60 and
                                     str(current["status"]).upper() in {"LIVE", "RUNNING", "BREAK", "HALFTIME"})
            if outcome.status == "UNRESOLVED" and currently_playing:
                if due:
                    journal.result(event_id, now, "PENDING", "WAITING_FOR_FULL_TIME", values)
                result["unresolved"] += len(grouped[event_id])
                continue
            if outcome.status == "UNRESOLVED" and resolver and due and network_calls >= 4:
                result["unresolved"] += len(grouped[event_id])
                continue
            if outcome.status == "UNRESOLVED" and resolver and due and network_calls < 4:
                network_calls += 1
                fetched = resolver(event_id)
                if fetched:
                    values = dict(fetched)
                    values.setdefault("source", "TIPICO_DETAIL")
                    outcome = classify(half_home, half_away, values)
            elif outcome.status == "UNRESOLVED" and cached and not due:
                result["unresolved"] += len(grouped[event_id])
                continue
            # Provider HT corrections must not silently rewrite frozen entry facts.
            conflict = any(values.get(key) is not None and values[key] != expected
                           for key, expected in (("ht_home", half_home), ("ht_away", half_away)))
            if conflict:
                values = {**values, "status": "HT_SCORE_CONFLICT"}
                outcome = classify(half_home, half_away, values)
            values["second_half_goals"] = outcome.second_half_goals
            values["target_h2_class"] = ({0: "H2_GOALS_0", 1: "H2_GOALS_1"}.get(outcome.second_half_goals, "H2_GOALS_2_PLUS")
                                          if outcome.second_half_goals is not None else None)
            check_status = "PENDING" if outcome.status == "UNRESOLVED" else "VOID" if outcome.status == "VOID" else "RESOLVED"
            # Do not postpone requests skipped because another match consumed
            # this iteration's budget; older due checks must get their turn.
            journal.result(event_id, now, check_status, "HT_SCORE_CONFLICT" if conflict else outcome.reason, values)
            for trade in grouped[event_id]:
                row = service.settle_trade(
                    trade["paper_trade_id"], final_score_home=values.get("final_score_home"),
                    final_score_away=values.get("final_score_away"), status=values.get("status", "UNKNOWN"),
                    extra_time=values.get("extra_time"), penalties=values.get("penalties"),
                    regulation_home=values.get("regulation_home"), regulation_away=values.get("regulation_away"),
                    regulation_confirmed=values.get("regulation_confirmed") is True,
                    settled_at=now.isoformat(), evidence=values,
                )
                result["unresolved" if row["status"] == "OPEN" else "settled"] += 1
        except Exception as exc:
            result["settlement_errors"] += 1
            service.logger.exception("Paper result failed for %s", event_id)
            journal.result(event_id, now, "PENDING", "RESULT_RESOLVER_ERROR", {"error": str(exc)})
    return result
