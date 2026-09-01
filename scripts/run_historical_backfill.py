"""Run and compactly record a two-year all-leagues FotMob backfill."""

from __future__ import annotations

import argparse
import json
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from config import Settings
from fotmob.history_pipeline import FotMobHistoryPipeline
from storage.database import Database


UTC = timezone.utc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--from-date", required=True, help="Startdatum YYYY-MM-DD")
    parser.add_argument("--to-date", required=True, help="Enddatum YYYY-MM-DD")
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--baseline-sqlite-total", type=int)
    parser.add_argument("--baseline-archive-bytes", type=int)
    parser.add_argument("--baseline-archive-files", type=int)
    parser.add_argument("--baseline-daily-rows", type=int)
    parser.add_argument("--baseline-unique-matches", type=int)
    parser.add_argument("--baseline-hz-matches", type=int)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("outputs/HISTORICAL_BACKFILL_RUN.json"),
    )
    return parser.parse_args()


def size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def count_files(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return 1
    return sum(1 for item in path.rglob("*") if item.is_file())


def compact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): compact(item)
            for key, item in value.items()
            if key not in {"canonical_files", "historical_files", "successful_ids", "daily_feed"}
        }
    if isinstance(value, (list, tuple)):
        return [compact(item) for item in value[:25]]
    return value


def range_counts(database: Database, start: str, end: str) -> dict[str, Any]:
    connection = database.connection
    target_ids = """
        SELECT DISTINCT fotmob_match_id
        FROM fotmob_daily_index
        WHERE provider = 'FOTMOB' AND observation_date BETWEEN ? AND ?
    """
    row = connection.execute(
        """
        SELECT COUNT(*) AS rows, COUNT(DISTINCT fotmob_match_id) AS matches,
               COUNT(DISTINCT observation_date) AS days
        FROM fotmob_daily_index
        WHERE provider = 'FOTMOB' AND observation_date BETWEEN ? AND ?
        """,
        (start, end),
    ).fetchone()
    status_rows = connection.execute(
        f"""
        SELECT COALESCE(i.detail_status, 'UNKNOWN') AS status, COUNT(*) AS n
        FROM fotmob_match_index i
        INNER JOIN ({target_ids}) d ON d.fotmob_match_id = i.fotmob_match_id
        WHERE i.provider = 'FOTMOB'
        GROUP BY i.detail_status
        ORDER BY i.detail_status
        """,
        (start, end),
    ).fetchall()
    return {
        "daily_index_rows": int(row[0] or 0),
        "unique_matches": int(row[1] or 0),
        "days": int(row[2] or 0),
        "detail_status": {str(item[0]): int(item[1]) for item in status_rows},
    }


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    start = date.fromisoformat(args.from_date).isoformat()
    end = date.fromisoformat(args.to_date).isoformat()
    output = args.output if args.output.is_absolute() else root / args.output
    output.parent.mkdir(parents=True, exist_ok=True)

    settings = Settings.from_env(root)
    database = Database(settings.database_path)
    pipeline = FotMobHistoryPipeline(settings, database)
    archive_root = getattr(settings, "fotmob_archive_path", settings.archive_path / "fotmob")
    tracked_files = [
        root / "data" / "tipico.db",
        root / "data" / "tipico.db-wal",
        root / "data" / "tipico.db-shm",
    ]
    before = {
        "files": {str(path.relative_to(root)): size(path) for path in tracked_files},
        "archive_bytes": size(Path(archive_root)),
        "archive_files": count_files(Path(archive_root)),
        "range": range_counts(database, start, end),
    }
    started_at = datetime.now(UTC)
    stopwatch = time.monotonic()
    try:
        result = pipeline.load_date_range(
            start,
            end,
            league_id=None,
            fetch_details=True,
            workers=max(1, int(args.workers)),
            execution_mode="manual",
        )
        elapsed_seconds = time.monotonic() - stopwatch
        ended_at = datetime.now(UTC)
        after = {
            "files": {str(path.relative_to(root)): size(path) for path in tracked_files},
            "archive_bytes": size(Path(archive_root)),
            "archive_files": count_files(Path(archive_root)),
            "range": range_counts(database, start, end),
        }
        compact_result = compact(result)
        before_sqlite_total = (
            int(args.baseline_sqlite_total)
            if args.baseline_sqlite_total is not None
            else sum(before["files"].values())
        )
        before_archive_bytes = (
            int(args.baseline_archive_bytes)
            if args.baseline_archive_bytes is not None
            else int(before["archive_bytes"])
        )
        before_archive_files = (
            int(args.baseline_archive_files)
            if args.baseline_archive_files is not None
            else int(before["archive_files"])
        )
        before["sqlite_total_bytes"] = before_sqlite_total
        before["archive_bytes"] = before_archive_bytes
        before["archive_files"] = before_archive_files
        if args.baseline_daily_rows is not None:
            before["range"]["daily_index_rows"] = int(args.baseline_daily_rows)
        if args.baseline_unique_matches is not None:
            before["range"]["unique_matches"] = int(args.baseline_unique_matches)
        if args.baseline_hz_matches is not None:
            before["range"]["detail_status"] = {
                **before["range"].get("detail_status", {}),
                "FETCHED": int(args.baseline_hz_matches),
            }
        payload = {
            "status": str(result.get("status") or "ERROR"),
            "scope": "ALL_LEAGUES",
            "from_date": start,
            "to_date": end,
            "workers": max(1, int(args.workers)),
            "started_at_utc": started_at.isoformat(),
            "ended_at_utc": ended_at.isoformat(),
            "elapsed_seconds": round(elapsed_seconds, 3),
            "before": before,
            "after": after,
            "delta": {
                "sqlite_bytes": sum(after["files"].values()) - before_sqlite_total,
                "archive_bytes": after["archive_bytes"] - before_archive_bytes,
                "archive_files": after["archive_files"] - before_archive_files,
            },
            "result": compact_result,
        }
    except Exception as exc:
        elapsed_seconds = time.monotonic() - stopwatch
        payload = {
            "status": "ERROR",
            "scope": "ALL_LEAGUES",
            "from_date": start,
            "to_date": end,
            "workers": max(1, int(args.workers)),
            "started_at_utc": started_at.isoformat(),
            "elapsed_seconds": round(elapsed_seconds, 3),
            "error": str(exc),
        }
        raise
    finally:
        database.close()

    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0 if payload["status"] not in {"ERROR", "BLOCKED_BY_POLICY"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
