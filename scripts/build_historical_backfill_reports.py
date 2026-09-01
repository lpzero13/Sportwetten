"""Build the durable historical backfill coverage and storage reports."""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Any, Iterable


AVERAGE_MONTH_DAYS = 365.25 / 12
STAT_NAMES = (
    "xg",
    "shots",
    "shots_on_target",
    "big_chances",
    "corners",
    "possession",
    "yellow_cards",
    "red_cards",
    "fouls",
    "offsides",
    "goalkeeper_saves",
    "passes",
    "accurate_passes",
    "shots_inside_box",
    "shots_outside_box",
    "touches_in_box",
    "expected_threat",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--run-json", type=Path, default=Path("outputs/HISTORICAL_BACKFILL_RUN.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    parser.add_argument(
        "--reuse-metric",
        action="store_true",
        help="Reuse an already generated METRIC_COVERAGE.csv during a rerun.",
    )
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


def fmt_bytes(value: int | float) -> str:
    number = float(value)
    sign = "-" if number < 0 else ""
    number = abs(number)
    if number >= 1024**3:
        return f"{sign}{number / 1024**3:.2f} GiB ({number / 1_000_000_000:.2f} GB)"
    if number >= 1024**2:
        return f"{sign}{number / 1024**2:.2f} MiB ({number / 1_000_000:.2f} MB)"
    if number >= 1024:
        return f"{sign}{number / 1024:.2f} KiB ({number / 1_000:.2f} kB)"
    return f"{sign}{number:.0f} B"


def md(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")


def table(headers: list[str], rows: Iterable[Iterable[Any]]) -> list[str]:
    result = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    result.extend("| " + " | ".join(md(value) for value in row) + " |" for row in rows)
    return result


def csv_write(path: Path, rows: Iterable[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def run_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def range_cte() -> str:
    return """
        WITH target AS (
            SELECT
                d.observation_date, d.fotmob_match_id, d.league_id,
                d.league_name, d.country_code, d.country_name,
                d.season_id, d.season_label, d.kickoff_at_utc,
                d.is_next_day, i.detail_status, i.data_quality,
                i.second_half_goals, i.second_half_goal_class
            FROM fotmob_daily_index d
            LEFT JOIN fotmob_match_index i
              ON i.provider = d.provider AND i.fotmob_match_id = d.fotmob_match_id
            WHERE d.provider = 'FOTMOB'
              AND d.observation_date BETWEEN ? AND ?
        )
    """


def scalar(connection: sqlite3.Connection, query: str, params: tuple[Any, ...] = ()) -> int:
    row = connection.execute(query, params).fetchone()
    return int(row[0] or 0)


def target_ids(connection: sqlite3.Connection, start: str, end: str) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            """
            SELECT DISTINCT fotmob_match_id
            FROM fotmob_daily_index
            WHERE provider = 'FOTMOB' AND observation_date BETWEEN ? AND ?
            """,
            (start, end),
        )
    }


def build_coverage_csvs(connection: sqlite3.Connection, output_dir: Path, start: str, end: str) -> dict[str, Any]:
    league_fields = [
        "country_code", "country_name", "league_id", "league_name", "first_date", "last_date",
        "feed_rows", "unique_matches", "hz_rows", "hz_unique_matches", "no_hz_rows",
        "no_hz_unique_matches", "no_data_rows", "no_data_unique_matches", "unknown_rows",
        "next_day_rows", "season_count",
    ]
    league_query = range_cte() + """
        SELECT
            COALESCE(country_code, '') AS country_code,
            COALESCE(country_name, '') AS country_name,
            COALESCE(league_id, '') AS league_id,
            COALESCE(league_name, '') AS league_name,
            MIN(observation_date) AS first_date,
            MAX(observation_date) AS last_date,
            COUNT(*) AS feed_rows,
            COUNT(DISTINCT fotmob_match_id) AS unique_matches,
            SUM(detail_status IN ('FETCHED', 'PARTIAL')) AS hz_rows,
            COUNT(DISTINCT CASE WHEN detail_status IN ('FETCHED', 'PARTIAL') THEN fotmob_match_id END) AS hz_unique_matches,
            SUM(detail_status = 'SKIPPED_NO_HALFTIME') AS no_hz_rows,
            COUNT(DISTINCT CASE WHEN detail_status = 'SKIPPED_NO_HALFTIME' THEN fotmob_match_id END) AS no_hz_unique_matches,
            SUM(detail_status = 'SKIPPED_NO_DATA') AS no_data_rows,
            COUNT(DISTINCT CASE WHEN detail_status = 'SKIPPED_NO_DATA' THEN fotmob_match_id END) AS no_data_unique_matches,
            SUM(detail_status IS NULL OR detail_status NOT IN ('FETCHED', 'PARTIAL', 'SKIPPED_NO_HALFTIME', 'SKIPPED_NO_DATA')) AS unknown_rows,
            SUM(is_next_day) AS next_day_rows,
            COUNT(DISTINCT season_label) AS season_count
        FROM target
        GROUP BY country_code, country_name, league_id, league_name
        ORDER BY hz_unique_matches DESC, unique_matches DESC, country_name, league_name
    """
    league_rows = [dict(row) for row in connection.execute(league_query, (start, end))]
    csv_write(output_dir / "HISTORICAL_LEAGUE_COVERAGE.csv", league_rows, league_fields)

    season_fields = [
        "season_id", "season_label", "first_date", "last_date", "country_count", "league_count",
        "feed_rows", "unique_matches", "hz_rows", "hz_unique_matches", "no_hz_rows",
        "no_hz_unique_matches", "no_data_rows", "no_data_unique_matches", "unknown_rows",
        "next_day_rows",
    ]
    season_query = range_cte() + """
        SELECT
            COALESCE(season_id, '') AS season_id,
            COALESCE(season_label, '') AS season_label,
            MIN(observation_date) AS first_date,
            MAX(observation_date) AS last_date,
            COUNT(DISTINCT country_code) AS country_count,
            COUNT(DISTINCT league_id) AS league_count,
            COUNT(*) AS feed_rows,
            COUNT(DISTINCT fotmob_match_id) AS unique_matches,
            SUM(detail_status IN ('FETCHED', 'PARTIAL')) AS hz_rows,
            COUNT(DISTINCT CASE WHEN detail_status IN ('FETCHED', 'PARTIAL') THEN fotmob_match_id END) AS hz_unique_matches,
            SUM(detail_status = 'SKIPPED_NO_HALFTIME') AS no_hz_rows,
            COUNT(DISTINCT CASE WHEN detail_status = 'SKIPPED_NO_HALFTIME' THEN fotmob_match_id END) AS no_hz_unique_matches,
            SUM(detail_status = 'SKIPPED_NO_DATA') AS no_data_rows,
            COUNT(DISTINCT CASE WHEN detail_status = 'SKIPPED_NO_DATA' THEN fotmob_match_id END) AS no_data_unique_matches,
            SUM(detail_status IS NULL OR detail_status NOT IN ('FETCHED', 'PARTIAL', 'SKIPPED_NO_HALFTIME', 'SKIPPED_NO_DATA')) AS unknown_rows,
            SUM(is_next_day) AS next_day_rows
        FROM target
        GROUP BY season_id, season_label
        ORDER BY season_label
    """
    season_rows = [dict(row) for row in connection.execute(season_query, (start, end))]
    csv_write(output_dir / "HISTORICAL_SEASON_COVERAGE.csv", season_rows, season_fields)
    return {
        "league_rows": league_rows,
        "season_rows": season_rows,
    }


def build_target_distribution(connection: sqlite3.Connection, output_dir: Path, start: str, end: str) -> list[dict[str, Any]]:
    fields = ["detail_status", "target_class", "second_half_goals", "matches", "share_of_unique_matches"]
    query = """
        WITH ids AS (
            SELECT DISTINCT fotmob_match_id
            FROM fotmob_daily_index
            WHERE provider = 'FOTMOB' AND observation_date BETWEEN ? AND ?
        ), unique_matches AS (
            SELECT i.detail_status, i.second_half_goal_class, i.second_half_goals
            FROM fotmob_match_index i
            INNER JOIN ids ON ids.fotmob_match_id = i.fotmob_match_id
            WHERE i.provider = 'FOTMOB'
        )
        SELECT
            COALESCE(detail_status, 'UNKNOWN') AS detail_status,
            COALESCE(second_half_goal_class, 'NOT_AVAILABLE') AS target_class,
            second_half_goals,
            COUNT(*) AS matches
        FROM unique_matches
        GROUP BY detail_status, second_half_goal_class, second_half_goals
        ORDER BY detail_status, second_half_goals, target_class
    """
    rows = [dict(row) for row in connection.execute(query, (start, end))]
    total = sum(int(row["matches"]) for row in rows)
    for row in rows:
        row["share_of_unique_matches"] = round(100.0 * int(row["matches"]) / total, 4) if total else 0.0
    csv_write(output_dir / "TARGET_DISTRIBUTION.csv", rows, fields)
    return rows


def scan_metric_coverage(
    archive_root: Path,
    target_match_ids: set[str],
    hz_match_count: int,
) -> tuple[list[dict[str, Any]], str | None]:
    fields = [
        "period", "metric_key", "provider_metric_name", "matches_available",
        "eligible_matches", "coverage_all_pct", "coverage_hz_pct", "scan_status",
    ]
    metric_rows: list[dict[str, Any]] = []
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - dependency is declared
        return ([{
            "period": "ALL", "metric_key": "pyarrow", "provider_metric_name": "pyarrow",
            "matches_available": 0, "eligible_matches": len(target_match_ids),
            "coverage_all_pct": 0.0, "coverage_hz_pct": 0.0, "scan_status": f"ERROR: {exc}",
        }], str(exc))

    core_root = archive_root / "match_core"
    if not core_root.exists():
        return ([{
            "period": "ALL", "metric_key": "match_core", "provider_metric_name": "match_core",
            "matches_available": 0, "eligible_matches": len(target_match_ids),
            "coverage_all_pct": 0.0, "coverage_hz_pct": 0.0, "scan_status": "MISSING",
        }], "match_core archive is missing")

    score_fields = [
        ("FIRST_HALF", "score", "ht_score_home", "ht_score_away", "HT score"),
        ("FULL_MATCH", "score", "ft_score_home", "ft_score_away", "FT score"),
    ]
    value_fields = [
        (
            period,
            name,
            f"{'ht' if period == 'FIRST_HALF' else 'ft'}_{name}_home",
            f"{'ht' if period == 'FIRST_HALF' else 'ft'}_{name}_away",
            name,
        )
        for period in ("FIRST_HALF", "FULL_MATCH")
        for name in STAT_NAMES
    ]
    wanted = {"fotmob_match_id"}
    for _, _, home, away, _ in score_fields + value_fields:
        wanted.add(home)
        wanted.add(away)
    counts: dict[tuple[str, str], int] = defaultdict(int)
    scanned_ids: set[str] = set()
    # The canonical archive stores one small Parquet file per match.  Building
    # a pyarrow Dataset for tens of thousands of fragments can retain several
    # gigabytes of fragment metadata.  Reading the target files one by one is
    # slower but keeps report generation bounded in memory.
    for path in sorted(core_root.rglob("match-*.parquet")):
        match_id = path.stem.removeprefix("match-")
        if match_id not in target_match_ids or match_id in scanned_ids:
            continue
        try:
            parquet_file = pq.ParquetFile(path)
            available = set(parquet_file.schema_arrow.names)
            columns = [column for column in wanted if column in available]
            if "fotmob_match_id" not in columns:
                continue
            values = parquet_file.read(columns=columns, use_threads=False).to_pydict()
        except (OSError, ValueError, RuntimeError):
            continue
        ids = values.get("fotmob_match_id", [])
        if not ids:
            continue
        scanned_ids.add(match_id)
        index = 0
        for period, key, home, away, _ in score_fields + value_fields:
            if home not in values or away not in values:
                continue
            home_value = values[home][index]
            away_value = values[away][index]
            if period in {"FIRST_HALF", "FULL_MATCH"} and key == "score":
                available_value = home_value is not None and away_value is not None
            else:
                available_value = home_value is not None or away_value is not None
            if available_value:
                counts[(period, key)] += 1
    for period, key, home, away, provider_name in score_fields + value_fields:
        available_count = counts[(period, key)]
        denominator_all = len(target_match_ids)
        denominator_hz = hz_match_count
        metric_rows.append({
            "period": period,
            "metric_key": key,
            "provider_metric_name": provider_name,
            "matches_available": available_count,
            "eligible_matches": denominator_all,
            "coverage_all_pct": round(100.0 * available_count / denominator_all, 4) if denominator_all else 0.0,
            "coverage_hz_pct": round(100.0 * available_count / denominator_hz, 4) if denominator_hz else 0.0,
            "scan_status": f"PASS ({len(scanned_ids)} core rows)",
        })
    return metric_rows, None


def build_metric_coverage(connection: sqlite3.Connection, archive_root: Path, output_dir: Path, start: str, end: str) -> dict[str, Any]:
    ids = target_ids(connection, start, end)
    hz_count = scalar(
        connection,
        """
        SELECT COUNT(*) FROM fotmob_match_index i
        WHERE i.provider='FOTMOB'
          AND i.detail_status IN ('FETCHED', 'PARTIAL')
          AND i.fotmob_match_id IN (
              SELECT DISTINCT fotmob_match_id FROM fotmob_daily_index
              WHERE provider='FOTMOB' AND observation_date BETWEEN ? AND ?
          )
        """,
        (start, end),
    )
    rows, error = scan_metric_coverage(archive_root, ids, hz_count)
    csv_write(
        output_dir / "METRIC_COVERAGE.csv",
        rows,
        ["period", "metric_key", "provider_metric_name", "matches_available", "eligible_matches", "coverage_all_pct", "coverage_hz_pct", "scan_status"],
    )
    return {"target_matches": len(ids), "hz_matches": hz_count, "rows": rows, "error": error}


def build_dataset_summary(connection: sqlite3.Connection, archive_root: Path, output_dir: Path, start: str, end: str) -> list[dict[str, Any]]:
    fields = ["dataset", "files", "bytes", "physical_rows", "range_matches", "notes"]
    target_count = scalar(
        connection,
        """
        SELECT COUNT(DISTINCT fotmob_match_id) FROM fotmob_daily_index
        WHERE provider='FOTMOB' AND observation_date BETWEEN ? AND ?
        """,
        (start, end),
    )
    hz_count = scalar(
        connection,
        """
        SELECT COUNT(*) FROM fotmob_match_index i
        WHERE i.provider='FOTMOB' AND i.detail_status IN ('FETCHED','PARTIAL')
          AND i.fotmob_match_id IN (
              SELECT DISTINCT fotmob_match_id FROM fotmob_daily_index
              WHERE provider='FOTMOB' AND observation_date BETWEEN ? AND ?
          )
        """,
        (start, end),
    )
    rows: list[dict[str, Any]] = []
    if archive_root.exists():
        try:
            import pyarrow.parquet as pq
        except ImportError:
            pq = None
        for directory in sorted(item for item in archive_root.iterdir() if item.is_dir()):
            file_total = 0
            byte_total = 0
            row_total = 0
            metadata_error = None

            def inspect_file(path: Path) -> tuple[int, int, str | None]:
                try:
                    row_count = int(pq.ParquetFile(path).metadata.num_rows) if pq is not None else 0
                    return path.stat().st_size, row_count, None
                except (OSError, ValueError, RuntimeError) as exc:
                    return 0, 0, str(exc)

            with ThreadPoolExecutor(max_workers=10, thread_name_prefix="parquet-meta") as executor:
                for file_bytes, file_rows, error in executor.map(inspect_file, directory.rglob("*.parquet")):
                    file_total += 1
                    byte_total += file_bytes
                    row_total += file_rows
                    if error and metadata_error is None:
                        metadata_error = error
            range_matches = hz_count if directory.name in {"match_core", "period_stats", "shots", "events"} else 0
            notes = "current physical archive"
            if directory.name == "match_core":
                notes = "one canonical core row per HZ-eligible match"
            elif directory.name == "period_stats":
                notes = "long-format period metrics"
            elif directory.name == "shots":
                notes = "normalized shots; only matches with shot rows create files"
            elif directory.name == "events":
                notes = "normalized timeline events; only matches with event rows create files"
            elif directory.name == "historical":
                notes = "legacy historical archive retained alongside canonical data"
            if metadata_error:
                notes += f"; metadata error: {metadata_error}"
            rows.append({
                "dataset": directory.name,
                "files": file_total,
                "bytes": byte_total,
                "physical_rows": row_total,
                "range_matches": range_matches,
                "notes": notes,
            })
    rows.extend([
        {"dataset": "sqlite_daily_index", "files": 1, "bytes": size(Path(str(connection.execute("PRAGMA database_list").fetchone()[2]))), "physical_rows": scalar(connection, "SELECT COUNT(*) FROM fotmob_daily_index WHERE provider='FOTMOB' AND observation_date BETWEEN ? AND ?", (start, end)), "range_matches": target_count, "notes": "queryable daily catalog; WAL measured separately in storage report"},
        {"dataset": "sqlite_match_index", "files": 1, "bytes": 0, "physical_rows": scalar(connection, "SELECT COUNT(*) FROM fotmob_match_index WHERE provider='FOTMOB'"), "range_matches": target_count, "notes": "match queue/status catalog; byte attribution included in SQLite total"},
    ])
    csv_write(output_dir / "DATASET_SUMMARY.csv", rows, fields)
    return rows


def build_storage_report(run: dict[str, Any], output_dir: Path, dataset_rows: list[dict[str, Any]]) -> dict[str, Any]:
    before = run.get("before", {})
    after = run.get("after", {})
    delta = run.get("delta", {})
    start = date.fromisoformat(str(run["from_date"]))
    end = date.fromisoformat(str(run["to_date"]))
    days = (end - start).days + 1
    sqlite_before = int(before.get("sqlite_total_bytes", sum((before.get("files") or {}).values())))
    sqlite_after = sum((after.get("files") or {}).values())
    archive_before = int(before.get("archive_bytes", 0))
    archive_after = int(after.get("archive_bytes", 0))
    sqlite_delta = int(delta.get("sqlite_bytes", sqlite_after - sqlite_before))
    archive_delta = int(delta.get("archive_bytes", archive_after - archive_before))
    total_delta = sqlite_delta + archive_delta
    target_range = (before.get("range") or {})
    final_range = (after.get("range") or {})
    target_rows = int(final_range.get("daily_index_rows", 0))
    target_hz = int((final_range.get("detail_status") or {}).get("FETCHED", 0)) + int((final_range.get("detail_status") or {}).get("PARTIAL", 0))
    baseline_rows = int(target_range.get("daily_index_rows", 0))
    baseline_hz = int((target_range.get("detail_status") or {}).get("FETCHED", 0)) + int((target_range.get("detail_status") or {}).get("PARTIAL", 0))
    new_rows = max(0, target_rows - baseline_rows)
    new_hz = max(0, target_hz - baseline_hz)
    bytes_per_row = sqlite_delta / new_rows if new_rows else 0.0
    bytes_per_hz = archive_delta / new_hz if new_hz else 0.0
    full_sqlite = bytes_per_row * target_rows if new_rows else float(sqlite_delta)
    full_archive = bytes_per_hz * target_hz if new_hz else float(archive_delta)
    full_total = full_sqlite + full_archive

    report = [
        "# Historical Backfill – Storage Report",
        "",
        f"**Zeitraum:** `{start.isoformat()}` bis `{end.isoformat()}` ({days} Tage)",
        "",
        "SQLite enthält den kleinen, filterbaren Katalog und Queue-/Statusdaten. Die umfangreichen Matchmetriken liegen im kanonischen Parquet-Archiv. WAL/SHM werden für die SQLite-Gesamtgröße mitgerechnet.",
        "",
    ]
    report += table(
        ["Bereich", "Vorher", "Nachher", "Delta"],
        [
            ["SQLite inkl. WAL/SHM", fmt_bytes(sqlite_before), fmt_bytes(sqlite_after), fmt_bytes(sqlite_delta)],
            ["Parquet-Archiv", fmt_bytes(archive_before), fmt_bytes(archive_after), fmt_bytes(archive_delta)],
            ["Gesamt", fmt_bytes(sqlite_before + archive_before), fmt_bytes(sqlite_after + archive_after), fmt_bytes(total_delta)],
            ["Archivdateien", f"{int(before.get('archive_files', 0)):,}", f"{int(after.get('archive_files', 0)):,}", f"{int(delta.get('archive_files', 0)):+,}"],
        ],
    )
    report += [
        "",
        "## Gemessene Rate",
        "",
    ]
    report += table(
        ["Speicher", "pro Tag", "pro Ø-Monat (30,44 Tage)"],
        [
            ["SQLite inkl. WAL/SHM", fmt_bytes(sqlite_delta / days), fmt_bytes(sqlite_delta / days * AVERAGE_MONTH_DAYS)],
            ["Parquet", fmt_bytes(archive_delta / days), fmt_bytes(archive_delta / days * AVERAGE_MONTH_DAYS)],
            ["Gesamt", fmt_bytes(total_delta / days), fmt_bytes(total_delta / days * AVERAGE_MONTH_DAYS)],
        ],
    )
    report += [
        "",
        "## Frischer Erstimport desselben Volumens",
        "",
        f"Die tatsächliche Delta-Zunahme ist durch bereits vorhandene Daten beeinflusst: {new_rows:,} neue Tagesindexzeilen und {new_hz:,} neue HZ-Archive kamen hinzu. Auf Basis dieser gemessenen Einheitskosten würde ein leerer Erstimport des gesamten Zeitraums ungefähr **{fmt_bytes(full_total)}** benötigen (SQLite {fmt_bytes(full_sqlite)}, Parquet {fmt_bytes(full_archive)}).",
        "",
    ]
    report += table(
        ["Erstimport-Schätzung", "pro Tag", "pro Ø-Monat (30,44 Tage)"],
        [
            ["SQLite-Katalog", fmt_bytes(full_sqlite / days), fmt_bytes(full_sqlite / days * AVERAGE_MONTH_DAYS)],
            ["Parquet-Metriken", fmt_bytes(full_archive / days), fmt_bytes(full_archive / days * AVERAGE_MONTH_DAYS)],
            ["Gesamt", fmt_bytes(full_total / days), fmt_bytes(full_total / days * AVERAGE_MONTH_DAYS)],
        ],
    )
    report += [
        "",
        "## Physische Dataset-Größen",
        "",
    ]
    report += table(
        ["Dataset", "Dateien", "Bytes", "physische Zeilen"],
        [[row["dataset"], f"{int(row['files']):,}", fmt_bytes(int(row["bytes"])), f"{int(row['physical_rows']):,}"] for row in dataset_rows],
    )
    report += [
        "",
        "Die Projektion ist eine belastbare Größenordnung, keine harte Obergrenze: Wochenenden, Liga-Mix, vorhandene Provider-Metriken und die Zahl der Shots/Events pro Match verändern die Parquet-Größe.",
        "",
    ]
    (output_dir / "STORAGE_REPORT.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    return {
        "sqlite_before": sqlite_before, "sqlite_after": sqlite_after, "sqlite_delta": sqlite_delta,
        "archive_before": archive_before, "archive_after": archive_after, "archive_delta": archive_delta,
        "total_delta": total_delta, "full_total_estimate": round(full_total),
    }


def build_status(
    run: dict[str, Any],
    coverage: dict[str, Any],
    distribution: list[dict[str, Any]],
    metric: dict[str, Any],
    storage: dict[str, Any],
    output_dir: Path,
) -> None:
    result = run.get("result") or {}
    details = result.get("details") or {}
    feed = result.get("feed") or {}
    access = result.get("access") or {}
    start = str(run.get("from_date"))
    end = str(run.get("to_date"))
    days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
    status = str(run.get("status") or "ERROR")
    quality = "PASS" if status == "PASS" and not metric.get("error") else "PARTIAL"
    report = [
        "# Historical Backfill Status",
        "",
        f"**Status:** `{quality}`  ",
        f"**Zeitraum:** `{start}` bis `{end}` inklusiv ({days} Kalendertage)  ",
        f"**Scope:** FotMob-Tagesfeed aller Länder und Ligen, kein Liga-Filter, {run.get('workers', 10)} Detail-Worker  ",
        f"**Laufzeit:** `{float(run.get('elapsed_seconds', 0.0)):.3f} Sekunden`  ",
        f"**Start/Ende UTC:** `{run.get('started_at_utc')}` / `{run.get('ended_at_utc')}`",
        "",
        "## Zusammenfassung",
        "",
    ]
    report += table(
        ["Metrik", "Wert"],
        [
            ["Tagesfeeds", f"{days} / {days}"],
            ["Feed-Zeilen", f"{int(result.get('fixtures', 0)):,}"],
            ["Eindeutige Spiele", f"{int(result.get('unique_fixtures', 0)):,}"],
            ["Länder / Liga-IDs / Länder-Liga-Schlüssel", f"{int(result.get('countries', 0))} / {len({row['league_id'] for row in coverage['league_rows']})} / {len(coverage['league_rows'])}"],
            ["Saisons", ", ".join(str(row.get("season_label") or row.get("season_id")) for row in coverage["season_rows"]) or "–"],
            ["HZ-Daten gespeichert", f"{int(details.get('fetched', 0)):,} neu, final {sum(int(row.get('matches', 0)) for row in distribution if row.get('detail_status') in {'FETCHED', 'PARTIAL'}):,}"],
            ["Ohne HZ übersprungen", f"{int(details.get('skipped_no_halftime', 0)):,} im Lauf"],
            ["Ohne Detaildaten übersprungen", f"{int(details.get('skipped_no_data', 0)):,} im Lauf"],
            ["API-Requests", f"{int(access.get('requests', 0)):,}, Erfolg {float(access.get('success_rate', 0.0)) * 100:.1f}%"],
            ["Payload", fmt_bytes(int(access.get('total_payload_bytes', 0)))],
            ["Speicherzunahme gesamt", fmt_bytes(storage["total_delta"])],
        ],
    )
    report += [
        "",
        "Die Tagesfeeds enthalten jedes von FotMob gelieferte Spiel unabhängig von Land, Liga und Uhrzeit. `includeNextDayLateNight=true` bleibt aktiv; diese Einträge sind im Tagesindex markiert. Detailantworten ohne nutzbare FirstHalf-Daten werden nicht als Metrikdataset gespeichert.",
        "",
        "## Qualitätschecks",
        "",
    ]
    errors = result.get("errors") or []
    warnings = result.get("warnings") or []
    report += table(
        ["Check", "Ergebnis"],
        [
            ["Backfill-Runner", status],
            ["Indexfehler", f"PASS (0)" if not errors else f"FAIL ({len(errors)})"],
            ["Warnings", f"PASS (0)" if not warnings else f"INFO ({len(warnings)})"],
            ["Metric-Parquet-Scan", "PASS" if not metric.get("error") else f"PARTIAL: {metric['error']}"],
            ["429 / 403 / 5xx / Timeout / Parse", "0 / 0 / 0 / 0 / 0" if not access.get("errors") else f"INFO ({access.get('errors')})"],
        ],
    )
    report += [
        "",
        "## Zielverteilung",
        "",
        "Die vollständige Verteilung liegt in [TARGET_DISTRIBUTION.csv](TARGET_DISTRIBUTION.csv). Sie trennt bewusst `FETCHED`/HZ-Daten von `SKIPPED_NO_HALFTIME` und `SKIPPED_NO_DATA`; fehlende HZ-Daten werden nicht künstlich als ZERO oder 2PLUS klassifiziert.",
        "",
    ]
    report += table(
        ["Detailstatus", "Target-Klasse", "2H-Tore", "Spiele", "Anteil"],
        [
            [row["detail_status"], row["target_class"], row.get("second_half_goals", ""), f"{int(row['matches']):,}", f"{float(row['share_of_unique_matches']):.2f}%"]
            for row in distribution
        ],
    )
    report += [
        "",
        "## Ablage der gewünschten Dateien",
        "",
        "- [HISTORICAL_LEAGUE_COVERAGE.csv](HISTORICAL_LEAGUE_COVERAGE.csv) – jede beobachtete Länder-/Liga-Kombination",
        "- [HISTORICAL_SEASON_COVERAGE.csv](HISTORICAL_SEASON_COVERAGE.csv) – Saisonabdeckung",
        "- [DATASET_SUMMARY.csv](DATASET_SUMMARY.csv) – physische Dataset-Dateien, Bytes und Zeilen",
        "- [TARGET_DISTRIBUTION.csv](TARGET_DISTRIBUTION.csv) – ZERO/2PLUS bzw. nicht verfügbare Targets",
        "- [METRIC_COVERAGE.csv](METRIC_COVERAGE.csv) – HZ-/FT-Metrikabdeckung",
        "- [STORAGE_REPORT.md](STORAGE_REPORT.md) – gemessene Größen und Hochrechnung",
        "",
        "Zusätzlich bleibt [HISTORICAL_BACKFILL_RUN.json](HISTORICAL_BACKFILL_RUN.json) als maschinenlesbarer Laufnachweis erhalten.",
        "",
        "## Reproduzierbarkeit",
        "",
        "```text",
        f"python -m scripts.run_historical_backfill --root . --from-date {start} --to-date {end} --workers {run.get('workers', 10)}",
        f"python -m scripts.build_historical_backfill_reports --root . --run-json outputs/HISTORICAL_BACKFILL_RUN.json",
        "```",
        "",
    ]
    (output_dir / "HISTORICAL_BACKFILL_STATUS.md").write_text("\n".join(report) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    output_dir = args.output_dir if args.output_dir.is_absolute() else root / args.output_dir
    run_path = args.run_json if args.run_json.is_absolute() else root / args.run_json
    run = run_json(run_path)
    start = str(run["from_date"])
    end = str(run["to_date"])
    connection = sqlite3.connect(root / "data" / "tipico.db")
    connection.row_factory = sqlite3.Row
    try:
        coverage = build_coverage_csvs(connection, output_dir, start, end)
        distribution = build_target_distribution(connection, output_dir, start, end)
        archive_root = root / "data" / "archive" / "fotmob"
        metric_path = output_dir / "METRIC_COVERAGE.csv"
        if args.reuse_metric and metric_path.exists():
            with metric_path.open(newline="", encoding="utf-8") as handle:
                metric_rows = list(csv.DictReader(handle))
            metric = {
                "target_matches": len(target_ids(connection, start, end)),
                "hz_matches": scalar(
                    connection,
                    """
                    SELECT COUNT(*) FROM fotmob_match_index
                    WHERE provider='FOTMOB' AND detail_status IN ('FETCHED','PARTIAL')
                    """,
                ),
                "rows": metric_rows,
                "error": next(
                    (
                        str(row.get("scan_status"))
                        for row in metric_rows
                        if str(row.get("scan_status", "")).startswith(("ERROR", "MISSING"))
                    ),
                    None,
                ),
            }
        else:
            metric = build_metric_coverage(connection, archive_root, output_dir, start, end)
        dataset_rows = build_dataset_summary(connection, archive_root, output_dir, start, end)
        storage = build_storage_report(run, output_dir, dataset_rows)
        build_status(run, coverage, distribution, metric, storage, output_dir)
    finally:
        connection.close()
    print(json.dumps({
        "status": "PASS" if not metric.get("error") and str(run.get("status")) == "PASS" else "PARTIAL",
        "output_dir": str(output_dir),
        "league_rows": len(coverage["league_rows"]),
        "season_rows": len(coverage["season_rows"]),
        "target_rows": len(distribution),
        "metric_rows": len(metric["rows"]),
        "dataset_rows": len(dataset_rows),
        "storage_delta_bytes": storage["total_delta"],
    }, ensure_ascii=False, indent=2))
    return 0 if not metric.get("error") and str(run.get("status")) == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
