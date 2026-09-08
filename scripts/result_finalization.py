#!/usr/bin/env python3
"""Audit and finalize historical Tipico results.

Examples::

    python scripts/result_finalization.py audit --root .
    python scripts/result_finalization.py reconcile --root . --dry-run
    python scripts/result_finalization.py reconcile --root . --apply --all-due
    python scripts/result_finalization.py recheck --root . --event-id EVENT_ID --apply

Dry-runs use a normal SQLite read-only connection so an active WAL remains
visible.  ``immutable=1`` is intentionally not used for a live database.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import Settings, configure_logging
from services.result_finalization import (
    RULE_VERSION,
    ResultFinalizationRunner,
    RECOVERY_VERSION,
    _as_dict,
    _event_tables,
    _persist_decision,
    audit_connection,
    acquire_run_lock,
    decide_event,
    release_run_lock,
    resume_full_result_recovery,
    start_full_result_recovery,
    status_full_result_recovery,
    write_recovery_reports,
    write_status_reports,
)
from storage.database import Database


def _resolve(root: Path, value: Path | None) -> Path:
    if value is None:
        return root / "data" / "tipico.db"
    path = value.expanduser()
    return (path if path.is_absolute() else root / path).resolve()


def _readonly_connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        f"file:{path.resolve()}?mode=ro",
        uri=True,
        timeout=30,
        check_same_thread=False,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("audit", "reconcile", "resume", "status", "report", "recheck"))
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--all-due", action="store_true")
    parser.add_argument("--full-inventory", action="store_true")
    parser.add_argument("--event-id", default=None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--mode", choices=("worker", "manual", "cached"), default=None)
    parser.add_argument("--refresh-index", action="store_true")
    parser.add_argument("--report-json", type=Path, default=None)
    return parser


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _audit_and_reports(connection: sqlite3.Connection, out_dir: Path, result: dict | None = None) -> dict:
    audit = audit_connection(connection)
    paths = write_status_reports(out_dir, audit, result)
    return {"audit": audit, "report_paths": paths}


def main() -> int:
    args = build_parser().parse_args()
    if args.apply and args.dry_run:
        raise SystemExit("--apply und --dry-run schließen sich aus.")
    if args.command == "recheck" and not args.event_id:
        raise SystemExit("recheck benötigt --event-id.")
    root = args.root.resolve()
    settings = Settings.from_env(root)
    logger = configure_logging(settings)
    db_path = _resolve(root, args.db)
    out_dir = (
        (args.out_dir if args.out_dir.is_absolute() else root / args.out_dir).resolve()
        if args.out_dir
        else root / "data" / "result_finalization"
    )
    report_json = (
        (args.report_json if args.report_json.is_absolute() else root / args.report_json).resolve()
        if args.report_json
        else out_dir / "latest.json"
    )
    database: Database | None = None
    connection: sqlite3.Connection | None = None
    run_id: str | None = None
    try:
        if args.command == "reconcile" and args.full_inventory:
            if args.all_due:
                raise SystemExit("--full-inventory und --all-due schließen sich aus.")
            if args.apply:
                database = Database(db_path)
                result = start_full_result_recovery(
                    database,
                    settings,
                    output_dir=out_dir,
                    apply=True,
                    workers=args.workers or 10,
                    mode=args.mode or "worker",
                    refresh_index=bool(args.refresh_index),
                )
            else:
                connection = _readonly_connection(db_path)
                result = start_full_result_recovery(
                    connection,
                    settings,
                    output_dir=out_dir,
                    apply=False,
                    workers=args.workers or 10,
                    mode=args.mode or "worker",
                    refresh_index=bool(args.refresh_index),
                )
                bundle = _audit_and_reports(connection, out_dir, result)
                result["report_paths"] = bundle["report_paths"]
            _write_json(report_json, result)
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
            return 2 if result.get("status") in {"BLOCKED_PROVIDER", "BLOCKED_BY_POLICY", "CACHED_ONLY", "ERROR"} else 0

        if args.command == "resume":
            if not args.run_id:
                raise SystemExit("resume benötigt --run-id.")
            database = Database(db_path)
            result = resume_full_result_recovery(
                database,
                settings,
                str(args.run_id),
                workers=args.workers or 10,
                mode=args.mode or "worker",
                refresh_index=bool(args.refresh_index),
            )
            _write_json(report_json, result)
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
            return 2 if result.get("status") in {"BLOCKED_PROVIDER", "BLOCKED_BY_POLICY", "CACHED_ONLY", "ERROR"} else 0

        if args.command == "status":
            if not args.run_id:
                raise SystemExit("status benötigt --run-id.")
            connection = _readonly_connection(db_path)
            result = status_full_result_recovery(connection, str(args.run_id))
            _write_json(report_json, result)
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
            return 0

        if args.command == "report":
            if not args.run_id:
                raise SystemExit("report benötigt --run-id.")
            connection = _readonly_connection(db_path)
            run_row = connection.execute(
                "SELECT report_directory FROM result_finalization_runs WHERE run_id = ?",
                (str(args.run_id),),
            ).fetchone()
            if run_row is None:
                raise SystemExit(f"Unbekannter Recovery-Lauf: {args.run_id}")
            report_dir = out_dir if args.out_dir else Path(run_row["report_directory"] or (out_dir / str(args.run_id)))
            paths = write_recovery_reports(report_dir, connection, str(args.run_id))
            result = {"status": "PASS", "recovery_version": RECOVERY_VERSION, "run_id": str(args.run_id), "report_paths": paths}
            _write_json(report_json, result)
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
            return 0

        if args.command == "audit":
            connection = _readonly_connection(db_path)
            bundle = _audit_and_reports(connection, out_dir)
            result = {
                "status": "PASS",
                "rule_version": RULE_VERSION,
                "mode": "audit",
                "database_path": str(db_path),
                "report_paths": bundle["report_paths"],
                "audit": {key: value for key, value in bundle["audit"].items() if key != "rows"},
            }
            _write_json(report_json, result)
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
            return 0

        apply = bool(args.apply)
        if apply:
            database = Database(db_path)
            run_id = acquire_run_lock(database, mode=args.command, started_at=datetime.now(timezone.utc).isoformat())
            runner = ResultFinalizationRunner(settings, database, logger=logger)
        else:
            connection = _readonly_connection(db_path)
            runner = ResultFinalizationRunner(settings, connection, logger=logger)

        if args.command == "recheck":
            event_row = runner.connection.execute(
                "SELECT * FROM events WHERE event_id = ?", (str(args.event_id),)
            ).fetchone()
            if event_row is None:
                raise SystemExit(f"Unbekanntes Event: {args.event_id}")
            decision = decide_event(runner.connection, _as_dict(event_row), tables=_event_tables(runner.connection))
            local = _persist_decision(
                database if database is not None else runner.connection,
                _as_dict(event_row),
                decision,
                apply=apply,
                checked_at=datetime.now(timezone.utc).isoformat(),
            )
            provider = ResultFinalizationRunner(settings, database or runner.connection, logger=logger).run(
                apply=apply, limit=1, workers=args.workers, mode=args.mode,
                refresh_index=args.refresh_index,
                event_id=str(args.event_id),
            )
            result = {
                # Preserve provider availability in the top-level outcome;
                # a cached or policy-blocked recheck is not a full PASS.
                "status": provider.get("status", "PARTIAL"),
                "rule_version": RULE_VERSION,
                "local": {"outcomes": [local]},
                "provider": provider,
            }
        else:
            if args.all_due and apply:
                # Continue in bounded, idempotent batches.  A provider miss
                # writes its next retry time, so the loop stops instead of
                # spinning over the same unresolved candidates.
                batches: list[dict] = []
                for _index in range(10_000):
                    batch = runner.run(
                        apply=True,
                        limit=args.limit,
                        workers=args.workers,
                        mode=args.mode,
                        refresh_index=args.refresh_index,
                        all_due=False,
                    )
                    batches.append(batch)
                    local_count = int(batch.get("local", {}).get("events_selected", 0) or 0)
                    provider_count = int(batch.get("provider", {}).get("events_selected", 0) or 0)
                    if local_count == 0 and provider_count == 0:
                        break
                    provider_result = batch.get("provider", {}) or {}
                    provider_blocked = provider_result.get("status") in {
                        "BLOCKED_BY_POLICY",
                        "CACHED_ONLY",
                    }
                    if provider_blocked and int(provider_result.get("applied", 0) or 0) == 0:
                        # Local status work may still count as applied, but it
                        # cannot make a provider-only retry progress.  Stop
                        # here instead of reselecting the same APPLIED FT-only
                        # rows for thousands of bounded batches.
                        break
                result = batches[-1] if batches else runner.run(
                    apply=True, limit=args.limit, workers=args.workers,
                    mode=args.mode, refresh_index=args.refresh_index,
                )
                result["all_due"] = True
                result["batches"] = len(batches)
                result["batch_summaries"] = [
                    {
                        "status": item.get("status"),
                        "local_selected": item.get("local", {}).get("events_selected", 0),
                        "provider_selected": item.get("provider", {}).get("events_selected", 0),
                        "applied": item.get("applied", 0),
                    }
                    for item in batches
                ]
                result["applied_total"] = sum(int(item.get("applied", 0) or 0) for item in batches)
            else:
                result = runner.run(
                    apply=apply,
                    limit=args.limit,
                    workers=args.workers,
                    mode=args.mode,
                    refresh_index=args.refresh_index,
                    all_due=False,
                )
        if apply:
            audit_connection_obj = database.connection
        else:
            audit_connection_obj = connection
        assert audit_connection_obj is not None
        bundle = _audit_and_reports(audit_connection_obj, out_dir, result)
        result["report_paths"] = bundle["report_paths"]
        result["audit_summary"] = {key: value for key, value in bundle["audit"].items() if key != "rows"}
        _write_json(report_json, result)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        if run_id and database is not None:
            release_run_lock(database, run_id, status="SUCCESS" if result.get("status") == "PASS" else "PARTIAL", summary=result)
            run_id = None
        provider_summary = result.get("provider", {}) or {}
        provider_counts = provider_summary.get("counts", {}) or {}
        degraded = result.get("status") in {
            "ERROR",
            "BLOCKED_BY_POLICY",
            "CACHED_ONLY",
        } or int(provider_counts.get("ERROR", 0) or 0) > 0
        return 2 if degraded else 0
    except Exception as exc:
        logger.exception("Result finalization failed")
        result = {"status": "ERROR", "rule_version": RULE_VERSION, "error": f"{type(exc).__name__}: {exc}"}
        _write_json(report_json, result)
        print(json.dumps(result, ensure_ascii=False, indent=2), file=sys.stderr)
        if run_id and database is not None:
            release_run_lock(database, run_id, status="ERROR", summary=result)
            run_id = None
        return 1
    finally:
        if database is not None:
            database.close()
        if connection is not None:
            connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
