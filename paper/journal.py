"""Shared market evidence and append-only decisions, separate from bankrolls."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from typing import Any

from .market import digest, encode, is_halftime


class PaperJournal:
    def __init__(self, database: Any) -> None:
        self.database = database

    def publish(self, context: dict[str, Any], *, raw_payload: dict[str, Any] | None = None) -> None:
        db = self.database
        with db._lock, db.connection:
            previous = db.connection.execute(
                "SELECT payload_json FROM paper_market_state WHERE event_id = ?", (context["event_id"],)
            ).fetchone()
            old = json.loads(previous[0]) if previous else {}
            if is_halftime(context["event"]):
                context["halftime_observed_at"] = old.get("halftime_observed_at") or db.first_halftime_observed_at(context["event_id"]) or context["observed_at"]
            # Retain a latest state for observed HT events; do not grow a second
            # complete feed catalog or retain historical live quotes here.
            if not is_halftime(context["event"]) and previous is None:
                return
            db.connection.execute(
                """INSERT INTO paper_market_state (event_id, observed_at, payload_json, raw_payload_json) VALUES (?, ?, ?, ?)
                   ON CONFLICT(event_id) DO UPDATE SET observed_at=excluded.observed_at,
                   payload_json=excluded.payload_json, raw_payload_json=excluded.raw_payload_json
                   WHERE excluded.observed_at >= paper_market_state.observed_at""",
                (context["event_id"], context["observed_at"], encode(context), encode(raw_payload) if raw_payload else None),
            )

    def archive_entry_raw(self, context: dict[str, Any], settings: Any) -> tuple[str | None, str]:
        if not settings.raw_paper_entry:
            return None, "DISABLED_BY_CONFIG"
        with self.database._lock:
            row = self.database.connection.execute(
                "SELECT raw_payload_json, payload_json FROM paper_market_state WHERE event_id=? AND observed_at=?",
                (context["event_id"], context["observed_at"]),
            ).fetchone()
        if not row or not row[0] or digest(json.loads(row[1])) != digest(context):
            return None, "EXACT_RAW_NOT_AVAILABLE"
        from storage.raw_storage import RawStorage
        store = RawStorage(settings.raw_storage_path, enabled=True, compression=settings.raw_compression)
        result = store.store("paper_entries", context["event_id"], json.loads(row[0]), observed_at=context["observed_at"])
        path = result.path or store.path_for_hash("paper_entries", context["event_id"], result.content_hash, observed_at=context["observed_at"])
        return str(path) if path else None, "ARCHIVED" if path else "RAW_STORE_FAILED"

    def current(self, since: str) -> list[dict[str, Any]]:
        with self.database._lock:
            rows = self.database.connection.execute(
                "SELECT payload_json FROM paper_market_state WHERE observed_at >= ? ORDER BY observed_at, event_id",
                (since,),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def decision(self, portfolio: Any, context: dict[str, Any], *, now: str,
                 decision: str, reason: str, details: dict[str, Any] | None = None) -> None:
        observation_id = digest(context)
        decision_id = digest([portfolio.portfolio_id, portfolio.version, observation_id, decision, reason])
        with self.database._lock, self.database.connection:
            self.database.connection.execute(
                "INSERT OR IGNORE INTO paper_observations VALUES (?, ?, ?, ?)",
                (observation_id, context["event_id"], context["observed_at"], encode(context)),
            )
            self.database.connection.execute(
                "INSERT OR IGNORE INTO paper_decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (decision_id, portfolio.portfolio_id, portfolio.version, portfolio.config_hash,
                 context["event_id"], observation_id, now, decision, reason,
                 encode(portfolio.config()), encode(details or {})),
            )

    def settlement_audit(self, trade_id: str, payload: dict[str, Any], evidence: dict[str, Any]) -> None:
        identity = digest([trade_id, payload["status"], payload["reason"], evidence])
        with self.database._lock, self.database.connection:
            self.database.connection.execute(
                "INSERT OR IGNORE INTO paper_settlement_audit VALUES (?, ?, ?, ?, ?, ?)",
                (identity, trade_id, payload["settled_at"], payload["status"], payload["reason"], encode(evidence)),
            )

    def result(self, event_id: str, now: datetime, status: str, reason: str, evidence: dict[str, Any]) -> None:
        with self.database._lock, self.database.connection:
            self.database.connection.execute(
                """INSERT INTO paper_result_checks VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(event_id) DO UPDATE SET checked_at=excluded.checked_at,
                   next_check_at=excluded.next_check_at, status=excluded.status,
                   reason=excluded.reason, evidence_json=excluded.evidence_json""",
                (event_id, now.isoformat(), (now + timedelta(seconds=120)).isoformat(), status, reason, encode(evidence)),
            )

    def decision_rows(self, portfolio_id: str, limit: int = 300) -> list[dict[str, Any]]:
        with self.database._lock:
            rows = self.database.connection.execute(
                """SELECT d.*, o.payload_json, r.status AS result_status,
                          r.reason AS result_reason, r.evidence_json AS result_json
                   FROM paper_decisions d JOIN paper_observations o USING(observation_id)
                   LEFT JOIN paper_result_checks r ON r.event_id = d.event_id
                   WHERE portfolio_id = ? ORDER BY decided_at DESC LIMIT ?""",
                (portfolio_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def health(self) -> dict[str, Any]:
        db = self.database
        now = datetime.now(timezone.utc)
        with db._lock:
            last = db.connection.execute("SELECT * FROM paper_worker_runs ORDER BY run_id DESC LIMIT 1").fetchone()
            active = db.connection.execute("SELECT COUNT(*) FROM paper_portfolios WHERE status='ACTIVE'").fetchone()[0]
            observations = db.connection.execute("SELECT MAX(observed_at) FROM paper_market_state").fetchone()[0]
            pending = db.connection.execute("SELECT COUNT(*) FROM paper_trades WHERE status='OPEN'").fetchone()[0]
            legacy = db.connection.execute("SELECT COUNT(*) FROM paper_trades WHERE status='UNRESOLVED'").fetchone()[0]
        return {"enabled": db.get_paper_runtime_setting("enabled", "1") == "1",
                "active_portfolios": active, "last_worker": dict(last) if last else None,
                "last_market_observation": observations, "open_trades": pending,
                "legacy_unresolved": legacy, "checked_at": now.isoformat()}
