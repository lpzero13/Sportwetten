"""Command line interface for the Tipico-only research lab."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .runner import (
    build_dataset,
    export_candidates,
    latest_run,
    paper_dry_run,
    read_run,
    run_audit,
    run_study,
)


def default_source(root: Path) -> Path:
    configured = __import__("os").getenv("TIPICO_BACKTEST_SOURCE_DB")
    if configured:
        return Path(configured).expanduser()
    copied = root / "Tipico DB" / "tipico.db"
    return copied if copied.is_file() else root / "data" / "tipico.db"


def _common(parser: argparse.ArgumentParser, root: Path) -> None:
    parser.add_argument("--source", type=Path, default=default_source(root), help="Tipico SQLite copy")
    parser.add_argument("--output", type=Path, default=root / "research" / "output" / "tipico_backtest")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--from-date", default=None, help="inclusive UTC date, YYYY-MM-DD")
    parser.add_argument("--to-date", default=None, help="inclusive UTC date, YYYY-MM-DD")
    parser.add_argument("--cutoff-utc", default=None, help="latest allowed observation timestamp")


def _study_args(parser: argparse.ArgumentParser, root: Path) -> None:
    _common(parser, root)
    parser.add_argument("--stake", type=float, default=10.0)
    parser.add_argument("--cost", type=float, default=0.0, help="additional cost per settled trade")
    parser.add_argument("--quote-haircut", type=float, default=0.0, help="stress reduction of net quote gain, 0.. <1")
    parser.add_argument("--seed", type=int, default=6301)
    parser.add_argument("--bootstrap-iterations", type=int, default=500)


def _json_print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Tipico-only Backtest & Pattern Lab")
    sub = parser.add_subparsers(dest="command", required=True)

    audit = sub.add_parser("audit", help="source audit without replay")
    _common(audit, root)
    dataset = sub.add_parser("build-dataset", help="write versioned observations and labels")
    _common(dataset, root)
    study = sub.add_parser("run-study", help="run the fixed variants and reports")
    _study_args(study, root)

    report = sub.add_parser("report", help="print an existing status report")
    report.add_argument("--run", type=Path, default=None)
    report.add_argument("--output", type=Path, default=root / "research" / "output" / "tipico_backtest")

    export = sub.add_parser("export-candidates", help="locate the non-activating candidate export")
    export.add_argument("--run", type=Path, default=None)
    export.add_argument("--output", type=Path, default=root / "research" / "output" / "tipico_backtest")

    dry = sub.add_parser("paper-dry-run", help="evaluate a frozen entry evidence without persistence")
    dry.add_argument("--evidence", type=Path, default=None, help="JSON or Parquet entry evidence")
    dry.add_argument("--run", type=Path, default=None, help="completed study containing TIPICO_BACKTEST_DATASET.parquet")
    dry.add_argument("--event-id", default=None)
    dry.add_argument("--variant", dest="variant_id", default="R00")

    args = parser.parse_args(argv)
    try:
        if args.command == "audit":
            result = run_audit(
                args.source, args.output, run_id=args.run_id,
                from_date=args.from_date, to_date=args.to_date, cutoff_utc=args.cutoff_utc,
            )
            _json_print(result)
            return 0
        if args.command == "build-dataset":
            result = build_dataset(
                args.source, args.output, run_id=args.run_id,
                from_date=args.from_date, to_date=args.to_date, cutoff_utc=args.cutoff_utc,
            )
            _json_print(result)
            return 0
        if args.command == "run-study":
            result = run_study(
                args.source, args.output, run_id=args.run_id, stake=args.stake,
                cost=args.cost, quote_haircut=args.quote_haircut, seed=args.seed,
                bootstrap_iterations=args.bootstrap_iterations,
                from_date=args.from_date, to_date=args.to_date, cutoff_utc=args.cutoff_utc,
            )
            _json_print(result)
            return 0
        if args.command == "report":
            path = args.run or latest_run(args.output)
            if not path:
                print("No Tipico research run found", file=sys.stderr)
                return 1
            print(read_run(path)["status"])
            return 0
        if args.command == "export-candidates":
            path = args.run or latest_run(args.output)
            if not path:
                print("No Tipico research run found", file=sys.stderr)
                return 1
            print(export_candidates(path))
            return 0
        if args.command == "paper-dry-run":
            _json_print(paper_dry_run(
                evidence_path=args.evidence, run_path=args.run,
                event_id=args.event_id, variant_id=args.variant_id,
            ))
            return 0
    except (FileNotFoundError, FileExistsError, RuntimeError, ValueError, OSError) as exc:
        print(f"Tipico research error: {exc}", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
