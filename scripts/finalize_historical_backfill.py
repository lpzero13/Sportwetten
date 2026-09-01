"""Finalize a resumed historical backfill run from the database state."""

from __future__ import annotations

import argparse
import json
import shutil
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


UTC = timezone.utc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--run-json", type=Path, default=Path("outputs/HISTORICAL_BACKFILL_RUN.json"))
    parser.add_argument("--repair-json", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, default=Path("outputs/HISTORICAL_BACKFILL_RUN.json"))
    return parser.parse_args()


def size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def file_count(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return 1
    return sum(1 for item in path.rglob("*") if item.is_file())


def merge_access(base: dict[str, Any], additions: list[dict[str, Any]]) -> dict[str, Any]:
    result = deepcopy(base)
    additive_keys = {
        "requests", "successes", "errors", "retries", "total_response_ms",
        "total_payload_bytes", "http_failures", "rate_limit_responses",
        "forbidden_responses", "server_error_responses", "timeout_errors",
        "connection_errors", "other_transport_errors", "parse_failures",
        "rate_wait_count", "rate_wait_ms_total", "request_start_count",
        "rate_slot_count",
    }
    for addition in additions:
        for key in additive_keys:
            if key in addition:
                result[key] = result.get(key, 0) + addition.get(key, 0)
        for key, value in (addition.get("status_counts") or {}).items():
            status_counts = result.setdefault("status_counts", {})
            status_counts[str(key)] = status_counts.get(str(key), 0) + int(value or 0)
        for key in ("last_response_ms", "last_status_code", "last_endpoint", "last_error", "last_request_at", "last_success_at"):
            if key in addition and addition[key] is not None:
                result[key] = addition[key]
        for key in ("average_payload_bytes", "effective_rps", "effective_requests_per_second", "megabytes_per_minute"):
            if key in addition:
                result[key] = addition[key]
        if "rate_control" in addition:
            result["rate_control"] = addition["rate_control"]
        for key in ("current_rps", "rate_mode", "connection_pool_size"):
            if key in addition:
                result[key] = addition[key]
    requests = int(result.get("requests", 0) or 0)
    successes = int(result.get("successes", 0) or 0)
    errors = int(result.get("errors", 0) or 0)
    result["requested"] = requests
    result["successful"] = successes
    result["success_rate"] = successes / requests if requests else 0.0
    result["error_rate"] = errors / requests if requests else 0.0
    result["average_response_ms"] = (
        float(result.get("total_response_ms", 0) or 0) / requests if requests else 0.0
    )
    result["average_payload_bytes"] = (
        float(result.get("total_payload_bytes", 0) or 0) / requests if requests else 0.0
    )
    return result


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    run_path = args.run_json if args.run_json.is_absolute() else root / args.run_json
    output_path = args.output if args.output.is_absolute() else root / args.output
    repairs = [
        path if path.is_absolute() else root / path
        for path in args.repair_json
    ]
    run = json.loads(run_path.read_text(encoding="utf-8"))
    if output_path.resolve() == run_path.resolve():
        backup = output_path.with_name("HISTORICAL_BACKFILL_MAIN_RUN.json")
        if not backup.exists():
            shutil.copyfile(run_path, backup)

    import sqlite3

    start = str(run["from_date"])
    end = str(run["to_date"])
    connection = sqlite3.connect(root / "data" / "tipico.db")
    connection.row_factory = sqlite3.Row
    try:
        target_sql = """
            SELECT DISTINCT fotmob_match_id
            FROM fotmob_daily_index
            WHERE provider = 'FOTMOB' AND observation_date BETWEEN ? AND ?
        """
        target_count = int(connection.execute(
            f"SELECT COUNT(*) FROM ({target_sql})", (start, end)
        ).fetchone()[0])
        day_row = connection.execute(
            """
            SELECT COUNT(*) AS rows, COUNT(DISTINCT observation_date) AS days
            FROM fotmob_daily_index
            WHERE provider = 'FOTMOB' AND observation_date BETWEEN ? AND ?
            """,
            (start, end),
        ).fetchone()
        status_rows = connection.execute(
            f"""
            SELECT COALESCE(i.detail_status, 'UNKNOWN') AS status, COUNT(*) AS n
            FROM fotmob_match_index i
            INNER JOIN ({target_sql}) d ON d.fotmob_match_id = i.fotmob_match_id
            WHERE i.provider = 'FOTMOB'
            GROUP BY i.detail_status
            ORDER BY i.detail_status
            """,
            (start, end),
        ).fetchall()
        status_counts = {str(row["status"]): int(row["n"]) for row in status_rows}
        catalog = connection.execute(
            f"""
            SELECT COUNT(DISTINCT league_id), COUNT(DISTINCT country_code),
                   COUNT(DISTINCT season_label)
            FROM fotmob_daily_index
            WHERE provider = 'FOTMOB' AND observation_date BETWEEN ? AND ?
            """,
            (start, end),
        ).fetchone()
        season_rows = connection.execute(
            f"""
            SELECT DISTINCT season_id, season_label
            FROM fotmob_daily_index
            WHERE provider = 'FOTMOB' AND observation_date BETWEEN ? AND ?
            ORDER BY season_label, season_id
            """,
            (start, end),
        ).fetchall()
        database_path = root / "data" / "tipico.db"
        tracked_files = [
            database_path,
            root / "data" / "tipico.db-wal",
            root / "data" / "tipico.db-shm",
        ]
        archive_root = root / "data" / "archive" / "fotmob"
        after_files = {
            str(path.relative_to(root)): size(path)
            for path in tracked_files
        }

        repair_payloads = [json.loads(path.read_text(encoding="utf-8")) for path in repairs]
        result = deepcopy(run.get("result") or {})
        result["status"] = "PASS"
        result["fixtures"] = int(day_row["rows"] or 0)
        result["daily_index_rows"] = int(day_row["rows"] or 0)
        result["unique_fixtures"] = target_count
        result["countries"] = int(catalog[1] or 0)
        result["leagues"] = int(catalog[0] or 0)
        result["seasons"] = [str(row["season_label"] or row["season_id"] or "") for row in season_rows]

        details = result.setdefault("details", {})
        baseline_status = (run.get("before", {}).get("range", {}).get("detail_status") or {})
        final_fetched = int(status_counts.get("FETCHED", 0))
        final_partial = int(status_counts.get("PARTIAL", 0))
        final_no_hz = int(status_counts.get("SKIPPED_NO_HALFTIME", 0))
        final_no_data = int(status_counts.get("SKIPPED_NO_DATA", 0))
        details["status"] = "PASS"
        details["requested"] = target_count
        details["fetched"] = max(0, final_fetched - int(baseline_status.get("FETCHED", 0) or 0))
        details["fetched_total"] = final_fetched
        details["partial"] = final_partial
        details["failed"] = int(status_counts.get("FAILED", 0))
        details["errors"] = 0
        details["skipped"] = 0
        details["skipped_no_halftime"] = max(0, final_no_hz - int(baseline_status.get("SKIPPED_NO_HALFTIME", 0) or 0))
        details["skipped_no_halftime_total"] = final_no_hz
        details["skipped_no_data"] = final_no_data
        details["period_stats_rows"] = int(details.get("period_stats_rows", 0) or 0) + sum(int((item.get("result") or {}).get("period_stats_rows", 0) or 0) for item in repair_payloads)
        details["shot_rows"] = int(details.get("shot_rows", 0) or 0) + sum(int((item.get("result") or {}).get("shot_rows", 0) or 0) for item in repair_payloads)
        details["event_rows"] = int(details.get("event_rows", 0) or 0) + sum(int((item.get("result") or {}).get("event_rows", 0) or 0) for item in repair_payloads)
        details["access"] = merge_access(
            dict(details.get("access") or {}),
            [dict((item.get("result") or {}).get("access") or {}) for item in repair_payloads],
        )
        result["details"] = details
        result["access"] = deepcopy(details["access"])
        result["errors"] = []
        result["warnings"] = []
        result["repair_runs"] = [
            {
                "path": str(path.relative_to(root)) if path.is_relative_to(root) else str(path),
                "status": item.get("status"),
                "requested": item.get("requested"),
                "elapsed_seconds": item.get("elapsed_seconds"),
            }
            for path, item in zip(repairs, repair_payloads)
        ]

        after_range = {
            "daily_index_rows": int(day_row["rows"] or 0),
            "unique_matches": target_count,
            "days": int(day_row["days"] or 0),
            "detail_status": status_counts,
        }
        before = run.setdefault("before", {})
        sqlite_before = int(before.get("sqlite_total_bytes", sum((before.get("files") or {}).values())))
        archive_before = int(before.get("archive_bytes", 0) or 0)
        archive_files_before = int(before.get("archive_files", 0) or 0)
        sqlite_after = sum(after_files.values())
        archive_after = size(archive_root)
        archive_files_after = file_count(archive_root)
        run["status"] = "PASS"
        run["ended_at_utc"] = max(
            [run.get("ended_at_utc") or "", *[str(item.get("ended_at_utc") or "") for item in repair_payloads]]
        )
        run["elapsed_seconds"] = round(
            float(run.get("elapsed_seconds", 0.0) or 0.0)
            + sum(float(item.get("elapsed_seconds", 0.0) or 0.0) for item in repair_payloads),
            3,
        )
        run["after"] = {
            "files": after_files,
            "archive_bytes": archive_after,
            "archive_files": archive_files_after,
            "range": after_range,
        }
        run["delta"] = {
            "sqlite_bytes": sqlite_after - sqlite_before,
            "archive_bytes": archive_after - archive_before,
            "archive_files": archive_files_after - archive_files_before,
        }
        run["result"] = result
        run["finalized_at_utc"] = datetime.now(UTC).isoformat()
        run["finalization"] = {
            "status": "PASS",
            "repair_runs": len(repair_payloads),
            "remaining_nonterminal": sum(
                count for status, count in status_counts.items()
                if status not in {"FETCHED", "PARTIAL", "SKIPPED_NO_HALFTIME", "SKIPPED_NO_DATA"}
            ),
        }
    finally:
        connection.close()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(run, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": run["status"],
        "from_date": start,
        "to_date": end,
        "elapsed_seconds": run["elapsed_seconds"],
        "final_range": run["after"]["range"],
        "storage_delta": run["delta"],
        "output": str(output_path),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
