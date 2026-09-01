"""Retry unfinished FotMob details for an already indexed date range."""

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
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("outputs/HISTORICAL_DETAIL_REPAIR.json"),
    )
    return parser.parse_args()


def size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def compact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): compact(item)
            for key, item in value.items()
            if key not in {"canonical_files", "historical_files", "successful_ids"}
        }
    if isinstance(value, (list, tuple)):
        return [compact(item) for item in value[:25]]
    return value


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
    archive_root = Path(getattr(settings, "fotmob_archive_path", settings.archive_path / "fotmob"))
    started_at = datetime.now(UTC)
    stopwatch = time.monotonic()
    try:
        rows = database.connection.execute(
            """
            SELECT DISTINCT i.fotmob_match_id
            FROM fotmob_match_index i
            INNER JOIN fotmob_daily_index d
                ON d.provider = i.provider
               AND d.fotmob_match_id = i.fotmob_match_id
            WHERE i.provider = 'FOTMOB'
              AND d.observation_date BETWEEN ? AND ?
              AND i.detail_status = 'NOT_FETCHED'
            ORDER BY i.fotmob_match_id
            """,
            (start, end),
        ).fetchall()
        ids = [str(row[0]) for row in rows]
        before_archive = size(archive_root)
        result = pipeline.fetch_details_for_ids(
            ids,
            workers=max(1, int(args.workers)),
            retry_failed=True,
            refresh_existing=False,
            require_halftime_stats=True,
            execution_mode="manual",
        )
        elapsed_seconds = time.monotonic() - stopwatch
        ended_at = datetime.now(UTC)
        remaining = database.connection.execute(
            """
            SELECT COALESCE(i.detail_status, 'UNKNOWN') AS status, COUNT(*) AS n
            FROM fotmob_match_index i
            INNER JOIN (
                SELECT DISTINCT fotmob_match_id
                FROM fotmob_daily_index
                WHERE provider = 'FOTMOB' AND observation_date BETWEEN ? AND ?
            ) d ON d.fotmob_match_id = i.fotmob_match_id
            WHERE i.provider = 'FOTMOB'
            GROUP BY i.detail_status
            ORDER BY i.detail_status
            """,
            (start, end),
        ).fetchall()
        payload = {
            "status": str(result.get("status") or "ERROR"),
            "from_date": start,
            "to_date": end,
            "workers": max(1, int(args.workers)),
            "started_at_utc": started_at.isoformat(),
            "ended_at_utc": ended_at.isoformat(),
            "elapsed_seconds": round(elapsed_seconds, 3),
            "requested": len(ids),
            "archive_bytes_before": before_archive,
            "archive_bytes_after": size(archive_root),
            "result": compact(result),
            "remaining_detail_status": {str(row[0]): int(row[1]) for row in remaining},
        }
    finally:
        database.close()

    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0 if payload["status"] != "ERROR" else 1


if __name__ == "__main__":
    raise SystemExit(main())
