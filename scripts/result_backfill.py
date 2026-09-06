#!/usr/bin/env python3
"""Backfill missing Tipico final results from verified FotMob details.

The default is a database read-only dry-run.  Use ``--apply`` explicitly on
the container (normally through ``wetten-result-backfill.service``) to append
FotMob evidence and fill only missing/incomplete ``match_results`` rows.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import replace
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import Settings, configure_logging
from services.result_backfill import ResultBackfillRunner, write_backfill_report
from storage.database import Database


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Alternative SQLite-Datei; ohne Angabe wird data/tipico.db verwendet.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="FotMob-Evidenz und validierte fehlende Ergebnisse in die DB schreiben.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Explizit schreibgeschützter Lauf (Standard).",
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument(
        "--mode",
        choices=("worker", "manual", "cached"),
        default=None,
        help="Netzwerk-Gate; cached verwendet ausschließlich den lokalen Tagesindex.",
    )
    parser.add_argument(
        "--refresh-index",
        action="store_true",
        help="Fehlende FotMob-Tagesindex-Tage vor dem Matching laden.",
    )
    parser.add_argument(
        "--allow-unknown-scope",
        action="store_true",
        help="Ergebnisse ohne expliziten Regular-Time-Nachweis nur als angenommen übernehmen.",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="JSON-Report; Standard: data/result_backfill/latest.json unter --root.",
    )
    parser.add_argument("--no-report", action="store_true")
    return parser


def _readonly_connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        f"file:{path.resolve()}?mode=ro&immutable=1",
        uri=True,
        timeout=30,
    )
    connection.row_factory = sqlite3.Row
    return connection


def _resolve_under_root(root: Path, value: Path) -> Path:
    expanded = value.expanduser()
    return (expanded if expanded.is_absolute() else root / expanded).resolve()


def main() -> int:
    args = build_parser().parse_args()
    if args.apply and args.dry_run:
        raise SystemExit("--apply und --dry-run schließen sich aus.")
    root = args.root.resolve()
    settings = Settings.from_env(root)
    if args.allow_unknown_scope:
        settings = replace(settings, result_backfill_allow_unknown_scope=True)
    logger = configure_logging(settings)
    report_path = (
        _resolve_under_root(root, args.report)
        if args.report is not None
        else root / "data" / "result_backfill" / "latest.json"
    )
    db_path = (
        _resolve_under_root(root, args.db)
        if args.db is not None
        else settings.database_path
    )
    database: Database | None = None
    connection: sqlite3.Connection | None = None
    try:
        if args.apply:
            database = Database(db_path)
            runner = ResultBackfillRunner(settings, database, logger=logger)
        else:
            connection = _readonly_connection(db_path)
            runner = ResultBackfillRunner(
                settings,
                connection,
                logger=logger,
                read_only=True,
            )
        result = runner.run(
            apply=bool(args.apply),
            limit=args.limit,
            workers=args.workers,
            mode=args.mode,
            refresh_index=bool(args.refresh_index),
        )
        result["database_path"] = str(db_path)
        result["report_path"] = str(report_path)
        if not args.no_report:
            write_backfill_report(result, report_path)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0 if result.get("status") not in {"ERROR", "BLOCKED_BY_POLICY"} else 2
    except Exception as exc:
        logger.exception("Result backfill failed")
        result = {"status": "ERROR", "error": f"{type(exc).__name__}: {exc}"}
        if not args.no_report:
            write_backfill_report(result, report_path)
        print(json.dumps(result, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    finally:
        if database is not None:
            database.close()
        if connection is not None:
            connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
