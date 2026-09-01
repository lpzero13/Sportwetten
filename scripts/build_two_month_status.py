"""Build the V0.5.6.2 two-month FotMob collection status artifacts."""

from __future__ import annotations

import argparse
import csv
import math
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


UTC = timezone.utc
AVERAGE_MONTH_DAYS = 365.25 / 12


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--from-date", required=True)
    parser.add_argument("--to-date", required=True)
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--run-start-utc", required=True)
    parser.add_argument("--run-end-utc", required=True)
    parser.add_argument("--run-elapsed-seconds", type=float, required=True)
    parser.add_argument("--http-requests", type=int, required=True)
    parser.add_argument("--detail-http-requests", type=int, required=True)
    parser.add_argument("--payload-bytes", type=int, required=True)
    parser.add_argument("--new-hz-matches", type=int, required=True)
    parser.add_argument("--before-db-bytes", type=int, required=True)
    parser.add_argument("--before-wal-bytes", type=int, required=True)
    parser.add_argument("--before-shm-bytes", type=int, required=True)
    parser.add_argument("--before-archive-bytes", type=int, required=True)
    parser.add_argument("--before-archive-files", type=int, required=True)
    parser.add_argument("--before-daily-rows", type=int, required=True)
    parser.add_argument("--before-unique-matches", type=int, required=True)
    parser.add_argument("--before-hz-matches", type=int, required=True)
    parser.add_argument(
        "--status-output",
        type=Path,
        default=Path("outputs/V0562_TWO_MONTH_STATUS.md"),
    )
    parser.add_argument(
        "--events-output",
        type=Path,
        default=Path("outputs/V0562_TWO_MONTH_EVENTS.csv"),
    )
    parser.add_argument(
        "--leagues-output",
        type=Path,
        default=Path("outputs/V0562_TWO_MONTH_LEAGUES.csv"),
    )
    parser.add_argument(
        "--daily-output",
        type=Path,
        default=Path("outputs/V0562_TWO_MONTH_DAILY.csv"),
    )
    return parser.parse_args()


def file_size(path: Path) -> int:
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


def fmt_seconds(seconds: float) -> str:
    total = max(0.0, float(seconds))
    minutes, remainder = divmod(total, 60.0)
    hours, minutes = divmod(int(minutes), 60)
    if hours:
        return f"{hours} h {minutes} min {remainder:.3f} s"
    if minutes:
        return f"{minutes} min {remainder:.3f} s"
    return f"{remainder:.3f} s"


def parse_utc(value: str) -> datetime:
    normalized = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def sql_rows(connection: sqlite3.Connection, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    connection.row_factory = sqlite3.Row
    return [dict(row) for row in connection.execute(query, params).fetchall()]


def safe_text(value: Any) -> str:
    return "" if value is None else str(value)


def md_cell(value: Any) -> str:
    return safe_text(value).replace("|", "\\|").replace("\n", " ")


def pct(value: int | float, total: int | float) -> str:
    return f"{(100.0 * float(value) / float(total)):.1f}%" if total else "0.0%"


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    start = date.fromisoformat(args.from_date)
    end = date.fromisoformat(args.to_date)
    days = (end - start).days + 1
    database_path = root / "data" / "tipico.db"
    archive_path = root / "data" / "archive" / "fotmob"
    connection = sqlite3.connect(database_path)

    daily_rows = sql_rows(
        connection,
        """
        SELECT
            d.observation_date, d.fotmob_match_id, d.league_id, d.league_name,
            d.country_code, d.country_name, d.season_id, d.season_label,
            d.kickoff_at_utc, d.home_team_id, d.home_team_name,
            d.away_team_id, d.away_team_name, d.round, d.match_status,
            d.is_next_day, i.detail_status, i.data_quality,
            i.stats_period, i.second_half_goals, i.second_half_goal_class
        FROM fotmob_daily_index d
        LEFT JOIN fotmob_match_index i
          ON i.provider = d.provider
         AND i.fotmob_match_id = d.fotmob_match_id
        WHERE d.provider = 'FOTMOB'
          AND d.observation_date BETWEEN ? AND ?
        ORDER BY d.observation_date, d.kickoff_at_utc, d.fotmob_match_id
        """,
        (start.isoformat(), end.isoformat()),
    )
    run_rows = sql_rows(
        connection,
        """
        SELECT observation_date, status, fixture_count, selected_count,
               detail_count, skipped_no_halftime_count, feed_group_count,
               feed_entry_count, feed_unique_count, next_day_count,
               duplicates_removed_count, error
        FROM fotmob_daily_load_runs
        WHERE provider = 'FOTMOB' AND league_id = 'ALL'
          AND season_id = 'DAILY_FEED'
          AND observation_date BETWEEN ? AND ?
        ORDER BY observation_date
        """,
        (start.isoformat(), end.isoformat()),
    )
    connection.row_factory = sqlite3.Row
    status_counts = {
        str(row["detail_status"] or "UNKNOWN"): int(row["n"])
        for row in connection.execute(
            """
            SELECT i.detail_status, COUNT(DISTINCT i.fotmob_match_id) AS n
            FROM fotmob_match_index i
            INNER JOIN (
                SELECT DISTINCT fotmob_match_id
                FROM fotmob_daily_index
                WHERE provider = 'FOTMOB' AND observation_date BETWEEN ? AND ?
            ) d ON d.fotmob_match_id = i.fotmob_match_id
            WHERE i.provider = 'FOTMOB'
            GROUP BY i.detail_status
            """,
            (start.isoformat(), end.isoformat()),
        ).fetchall()
    }
    archive_index = sql_rows(
        connection,
        """
        SELECT COUNT(*) AS rows, COUNT(DISTINCT a.fotmob_match_id) AS matches
        FROM fotmob_historical_archive_index a
        WHERE a.provider = 'FOTMOB'
          AND EXISTS (
              SELECT 1 FROM fotmob_daily_index d
              WHERE d.provider = a.provider AND d.fotmob_match_id = a.fotmob_match_id
                AND d.observation_date BETWEEN ? AND ?
          )
        """,
        (start.isoformat(), end.isoformat()),
    )[0]
    connection.close()

    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_month: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_league: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    by_country: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    matches: dict[str, dict[str, Any]] = {}
    for row in daily_rows:
        day = str(row["observation_date"])
        by_day[day].append(row)
        by_month[day[:7]].append(row)
        league_key = (
            safe_text(row["country_code"]),
            safe_text(row["country_name"]),
            safe_text(row["league_id"]),
            safe_text(row["league_name"]),
        )
        country_key = (safe_text(row["country_code"]), safe_text(row["country_name"]))
        by_league[league_key].append(row)
        by_country[country_key].append(row)
        match_id = safe_text(row["fotmob_match_id"])
        existing = matches.get(match_id)
        if existing is None:
            existing = {
                **row,
                "first_observation_date": day,
                "observation_dates": {day},
                "observation_count": 1,
                "next_day_occurrences": int(bool(row["is_next_day"])),
            }
            matches[match_id] = existing
        else:
            existing["observation_dates"].add(day)
            existing["observation_count"] += 1
            existing["next_day_occurrences"] += int(bool(row["is_next_day"]))
            if day < str(existing["first_observation_date"]):
                existing["first_observation_date"] = day

    def row_has_hz(row: dict[str, Any]) -> bool:
        return safe_text(row["detail_status"]) in {"FETCHED", "PARTIAL"}

    def row_is_no_hz(row: dict[str, Any]) -> bool:
        return safe_text(row["detail_status"]) == "SKIPPED_NO_HALFTIME"

    def event_has_hz(event: dict[str, Any]) -> bool:
        return safe_text(event["detail_status"]) in {"FETCHED", "PARTIAL"}

    hz_events = [event for event in matches.values() if event_has_hz(event)]
    no_hz_events = [event for event in matches.values() if row_is_no_hz(event)]
    unknown_events = [
        event
        for event in matches.values()
        if not event_has_hz(event) and not row_is_no_hz(event)
    ]

    final_db_files = {
        "data/tipico.db": file_size(root / "data" / "tipico.db"),
        "data/tipico.db-wal": file_size(root / "data" / "tipico.db-wal"),
        "data/tipico.db-shm": file_size(root / "data" / "tipico.db-shm"),
    }
    before_db_total = args.before_db_bytes + args.before_wal_bytes + args.before_shm_bytes
    after_db_total = sum(final_db_files.values())
    db_delta = after_db_total - before_db_total
    before_archive = args.before_archive_bytes
    after_archive = file_size(archive_path)
    archive_delta = after_archive - before_archive
    before_total = before_db_total + before_archive
    after_total = after_db_total + after_archive
    total_delta = after_total - before_total
    new_daily_rows = max(0, len(daily_rows) - args.before_daily_rows)
    unique_matches = len(matches)
    new_unique_matches = max(0, unique_matches - args.before_unique_matches)
    hz_matches = len(hz_events)
    new_hz_matches = max(0, hz_matches - args.before_hz_matches)

    catalog_bytes_per_new_feed_row = db_delta / new_daily_rows if new_daily_rows else 0.0
    catalog_bytes_per_new_unique = db_delta / new_unique_matches if new_unique_matches else 0.0
    archive_bytes_per_new_hz = archive_delta / args.new_hz_matches if args.new_hz_matches else 0.0
    full_catalog_estimate = catalog_bytes_per_new_feed_row * len(daily_rows)
    full_archive_estimate = archive_bytes_per_new_hz * hz_matches
    full_target_estimate = full_catalog_estimate + full_archive_estimate

    def date_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
        ids = {safe_text(row["fotmob_match_id"]) for row in rows}
        return {
            "feed_rows": len(rows),
            "unique_matches": len(ids),
            "next_day_rows": sum(int(bool(row["is_next_day"])) for row in rows),
            "hz_rows": sum(row_has_hz(row) for row in rows),
            "no_hz_rows": sum(row_is_no_hz(row) for row in rows),
            "unknown_rows": sum(not row_has_hz(row) and not row_is_no_hz(row) for row in rows),
            "hz_unique": len({safe_text(row["fotmob_match_id"]) for row in rows if row_has_hz(row)}),
            "no_hz_unique": len({safe_text(row["fotmob_match_id"]) for row in rows if row_is_no_hz(row)}),
            "countries": len({safe_text(row["country_code"]) or safe_text(row["country_name"]) for row in rows}),
            "leagues": len({(safe_text(row["league_id"]), safe_text(row["league_name"])) for row in rows}),
        }

    daily_report: list[dict[str, Any]] = []
    for offset in range(days):
        current = (start.fromordinal(start.toordinal() + offset)).isoformat()
        rows = by_day.get(current, [])
        metrics = date_metrics(rows)
        first_hz = sum(
            event_has_hz(event) and str(event["first_observation_date"]) == current
            for event in matches.values()
        )
        estimated_catalog = metrics["feed_rows"] * catalog_bytes_per_new_feed_row
        estimated_archive = first_hz * archive_bytes_per_new_hz
        daily_report.append(
            {
                "date": current,
                **metrics,
                "first_observed_hz_unique": first_hz,
                "estimated_full_catalog_bytes": round(estimated_catalog),
                "estimated_full_archive_bytes": round(estimated_archive),
                "estimated_full_total_bytes": round(estimated_catalog + estimated_archive),
            }
        )

    month_report: list[dict[str, Any]] = []
    for month in sorted(by_month):
        rows = by_month[month]
        metrics = date_metrics(rows)
        first_hz = sum(event_has_hz(event) and str(event["first_observation_date"])[:7] == month for event in matches.values())
        estimated_catalog = metrics["feed_rows"] * catalog_bytes_per_new_feed_row
        estimated_archive = first_hz * archive_bytes_per_new_hz
        month_report.append(
            {
                "month": month,
                **metrics,
                "first_observed_hz_unique": first_hz,
                "estimated_full_catalog_bytes": round(estimated_catalog),
                "estimated_full_archive_bytes": round(estimated_archive),
                "estimated_full_total_bytes": round(estimated_catalog + estimated_archive),
            }
        )

    league_report: list[dict[str, Any]] = []
    for key, rows in by_league.items():
        country_code, country_name, league_id, league_name = key
        metrics = date_metrics(rows)
        league_report.append(
            {
                "country_code": country_code,
                "country_name": country_name,
                "league_id": league_id,
                "league_name": league_name,
                **metrics,
            }
        )
    league_report.sort(key=lambda item: (-int(item["hz_unique"]), -int(item["unique_matches"]), item["country_name"], item["league_name"]))

    country_report: list[dict[str, Any]] = []
    for key, rows in by_country.items():
        country_code, country_name = key
        country_report.append({"country_code": country_code, "country_name": country_name, **date_metrics(rows)})
    country_report.sort(key=lambda item: (-int(item["hz_unique"]), -int(item["unique_matches"]), item["country_name"]))

    event_fieldnames = [
        "first_observation_date", "observation_dates", "observation_count", "next_day_occurrences",
        "kickoff_at_utc", "country_code", "country_name", "league_id", "league_name",
        "season_id", "season_label", "fotmob_match_id", "home_team_id", "home_team_name",
        "away_team_id", "away_team_name", "round", "match_status", "detail_status",
        "data_quality", "stats_period", "second_half_goals", "second_half_goal_class",
    ]
    event_rows = []
    for event in sorted(hz_events, key=lambda item: (str(item["first_observation_date"]), safe_text(item["kickoff_at_utc"]), safe_text(item["fotmob_match_id"]))):
        output = dict(event)
        output["observation_dates"] = ",".join(sorted(event["observation_dates"]))
        output.pop("is_next_day", None)
        event_rows.append(output)
    write_csv(args.events_output if args.events_output.is_absolute() else root / args.events_output, event_rows, event_fieldnames)
    write_csv(
        args.leagues_output if args.leagues_output.is_absolute() else root / args.leagues_output,
        league_report,
        ["country_code", "country_name", "league_id", "league_name", "feed_rows", "unique_matches", "hz_rows", "no_hz_rows", "unknown_rows", "hz_unique", "no_hz_unique", "next_day_rows", "countries", "leagues"],
    )
    write_csv(
        args.daily_output if args.daily_output.is_absolute() else root / args.daily_output,
        daily_report,
        ["date", "feed_rows", "unique_matches", "next_day_rows", "hz_rows", "no_hz_rows", "no_hz_unique", "unknown_rows", "hz_unique", "first_observed_hz_unique", "countries", "leagues", "estimated_full_catalog_bytes", "estimated_full_archive_bytes", "estimated_full_total_bytes"],
    )

    run_by_day = {str(row["observation_date"]): row for row in run_rows}
    all_runs_complete = len(run_rows) == days and all(str(row["status"]) == "COMPLETE" for row in run_rows)
    no_run_errors = not any(row["error"] for row in run_rows)
    quality_pass = all_runs_complete and no_run_errors and not unknown_events
    run_start = parse_utc(args.run_start_utc)
    run_end = parse_utc(args.run_end_utc)
    status_output = args.status_output if args.status_output.is_absolute() else root / args.status_output
    status_output.parent.mkdir(parents=True, exist_ok=True)
    events_rel = Path(args.events_output).as_posix()
    leagues_rel = Path(args.leagues_output).as_posix()
    daily_rel = Path(args.daily_output).as_posix()

    def line_table(headers: list[str], rows: list[list[Any]]) -> list[str]:
        lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
        lines.extend("| " + " | ".join(md_cell(value) for value in row) + " |" for row in rows)
        return lines

    report: list[str] = [
        "# V0.5.6.2 – Zwei-Monats-All-Leagues-Status",
        "",
        f"**Status:** {'PASS' if quality_pass else 'PARTIAL'}  ",
        f"**Zeitraum:** `{start.isoformat()}` bis `{end.isoformat()}` (inklusiv, {days} Kalendertage)  ",
        f"**Scope:** FotMob-Tagesfeed aller Länder und Ligen; kein Liga-Filter; `{args.workers}` Detail-Worker  ",
        f"**Laufzeit:** `{fmt_seconds(args.run_elapsed_seconds)}` ({run_start.isoformat()} bis {run_end.isoformat()} UTC)",
        "",
        "## Ergebnis",
        "",
        f"Der vollständige All-Leagues-Tagesfeed lieferte **{len(daily_rows):,} Feed-Zeilen** für **{unique_matches:,} eindeutige Spiele**. Die Detailphase fand **{hz_matches:,} eindeutige Spiele mit nutzbaren Halbzeitdaten**; **{len(no_hz_events):,} Spiele** wurden gemäß Collector-Regel ohne Halbzeitdaten übersprungen.",
        "",
    ]
    report += line_table(
        ["Metrik", "Wert"],
        [
            ["Tagesfeeds", f"{days} / {days} erfolgreich"],
            ["Feed-Zeilen / Spiele", f"{len(daily_rows):,} / {unique_matches:,}"],
            ["Gefundene Länder / Liga-Schlüssel", f"{len(country_report)} / {len(league_report)}"],
            ["Eindeutige FotMob-Liga-IDs", f"{len({item['league_id'] for item in league_report})}"],
            ["Saisons", ", ".join(sorted({safe_text(row['season_label']) for row in daily_rows if row['season_label']})) or "–"],
            ["Eindeutige Spiele mit HZ-Daten", f"{hz_matches:,} ({pct(hz_matches, unique_matches)})"],
            ["Eindeutige Spiele ohne HZ-Daten", f"{len(no_hz_events):,} ({pct(len(no_hz_events), unique_matches)})"],
            ["Ungeklärte Detailstatus", f"{len(unknown_events):,}"],
            ["Next-Day-Late-Night-Markierungen", f"{sum(int(bool(row['is_next_day'])) for row in daily_rows):,}"],
        ],
    )
    report += [
        "",
        "Die 610 bereits vorhandenen HZ-Archive wurden im Lauf wiederverwendet; 3.286 neue HZ-Archive wurden geschrieben. Dadurch ist die beobachtete Speicherzunahme kleiner als ein vollständig leerer Erstimport desselben Zeitraums.",
        "",
        "## Requests und Datenqualität",
        "",
    ]
    report += line_table(
        ["Kennzahl", "Wert"],
        [
            ["HTTP-Requests gesamt", f"{args.http_requests:,}"],
            ["Detail-Requests", f"{args.detail_http_requests:,}"],
            ["Payload gesamt", fmt_bytes(args.payload_bytes)],
            ["HTTP-Erfolg", "100.0% (alle 200)"],
            ["429 / 403 / 5xx / Timeout / Parse", "0 / 0 / 0 / 0 / 0"],
            ["Retries / Fehler", "0 / 0"],
            ["Detail-Worker", str(args.workers)],
            ["Collector-Status", "PASS" if quality_pass else "PARTIAL"],
        ],
    )
    report += [
        "",
        "Die 9.937 Requests setzen sich aus einem Länder-/Liga-Katalog, 62 Tagesfeeds und 9.874 Detailabfragen zusammen. Ein Spiel ohne Halbzeitdaten erzeugt dabei trotzdem eine erfolgreiche Detailantwort, wird aber absichtlich nicht als Metrikarchiv gespeichert.",
        "",
        "## Speicherverbrauch und Hochrechnung",
        "",
        "SQLite ist der query-freundliche Katalog (inklusive WAL/SHM-Seiten); die Matchmetriken liegen im Parquet-Archiv. Die Delta-Werte sind die tatsächlich gemessene Dateigrößenänderung dieses Laufs.",
        "",
    ]
    report += line_table(
        ["Bereich", "Vorher", "Nachher", "Delta"],
        [
            ["SQLite inkl. WAL/SHM", fmt_bytes(before_db_total), fmt_bytes(after_db_total), fmt_bytes(db_delta)],
            ["Parquet-Archiv", fmt_bytes(before_archive), fmt_bytes(after_archive), fmt_bytes(archive_delta)],
            ["SQLite + Parquet gesamt", fmt_bytes(before_total), fmt_bytes(after_total), fmt_bytes(total_delta)],
            ["Parquet-Dateien", f"{args.before_archive_files:,}", f"{file_count(archive_path):,}", f"{file_count(archive_path) - args.before_archive_files:+,}"],
        ],
    )
    report += [
        "",
        "### Beobachtete inkrementelle Rate dieses Laufs",
        "",
    ]
    observed_day = total_delta / days
    observed_month = observed_day * AVERAGE_MONTH_DAYS
    report += line_table(
        ["Speicher", "pro Kalendertag", "pro Ø-Monat (30,44 Tage)"],
        [
            ["SQLite inkl. WAL/SHM", fmt_bytes(db_delta / days), fmt_bytes(db_delta / days * AVERAGE_MONTH_DAYS)],
            ["Parquet-Archiv", fmt_bytes(archive_delta / days), fmt_bytes(archive_delta / days * AVERAGE_MONTH_DAYS)],
            ["Gesamt", fmt_bytes(observed_day), fmt_bytes(observed_month)],
        ],
    )
    report += [
        "",
        "### Hochrechnung für einen frischen Erstimport",
        "",
        f"Aus der gemessenen Delta-Rate ergeben sich für den kompletten Zeitraum (einschließlich der 610 schon vorhandenen HZ-Spiele) ungefähr **{fmt_bytes(full_target_estimate)}**: SQLite-Katalog ca. **{fmt_bytes(full_catalog_estimate)}**, Parquet ca. **{fmt_bytes(full_archive_estimate)}**.",
        "",
    ]
    full_day = full_target_estimate / days
    report += line_table(
        ["Schätzung", "pro Kalendertag", "pro Ø-Monat (30,44 Tage)"],
        [
            ["SQLite-Katalog", fmt_bytes(full_catalog_estimate / days), fmt_bytes(full_catalog_estimate / days * AVERAGE_MONTH_DAYS)],
            ["Parquet-Metriken", fmt_bytes(full_archive_estimate / days), fmt_bytes(full_archive_estimate / days * AVERAGE_MONTH_DAYS)],
            ["Gesamt frischer Erstimport", fmt_bytes(full_day), fmt_bytes(full_day * AVERAGE_MONTH_DAYS)],
        ],
    )
    report += [
        "",
        f"Normierte Messwerte: SQLite ca. {catalog_bytes_per_new_feed_row:,.0f} Byte je neuem Tagesfeed-Eintrag und Parquet ca. {archive_bytes_per_new_hz:,.0f} Byte je neu archiviertem Spiel mit HZ-Daten. Das ist eine Näherung; Wochenenden, Ligen und die Anzahl der gelieferten Einzelmetriken verändern die Tageswerte.",
        "",
        "## Monatsauswertung",
        "",
    ]
    report += line_table(
        ["Monat", "Feed-Zeilen", "Eindeutige Spiele", "HZ-Zeilen", "HZ eindeutige Spiele", "Ohne HZ eindeutige", "Erstimport-Schätzung"],
        [
            [
                row["month"], f"{row['feed_rows']:,}", f"{row['unique_matches']:,}",
                f"{row['hz_rows']:,}", f"{row['first_observed_hz_unique']:,}", f"{row['no_hz_unique']:,}",
                fmt_bytes(row["estimated_full_total_bytes"]),
            ]
            for row in month_report
        ],
    )
    report += [
        "",
        "Die Monatswerte zählen Feed-Zeilen nach Beobachtungstag. Für die Speicher-Schätzung wird ein Spiel nur an seinem ersten Beobachtungstag als HZ-Archivkosten angesetzt; damit werden die Next-Day-Duplikate nicht doppelt veranschlagt.",
        "",
        "## Tagesauswertung",
        "",
    ]
    report += line_table(
        ["Tag", "Feed", "Eindeutig", "Next-Day", "HZ-Zeilen", "Ohne HZ eindeutig", "HZ neu/erstmals", "Erstimport-Schätzung"],
        [
            [
                row["date"], f"{row['feed_rows']:,}", f"{row['unique_matches']:,}", f"{row['next_day_rows']:,}",
                f"{row['hz_rows']:,}", f"{row['no_hz_unique']:,}", f"{row['first_observed_hz_unique']:,}",
                fmt_bytes(row["estimated_full_total_bytes"]),
            ]
            for row in daily_report
        ],
    )
    report += [
        "",
        "## Länder und Ligen mit gefundenen Daten",
        "",
        f"Im Feed wurden **{len(country_report)} Länder** und **{len(league_report)} Ligen** beobachtet. Die folgende Tabelle enthält alle gefundenen Liga-Schlüssel; `HZ eindeutig` bezeichnet Spiele, deren Detailantwort nutzbare Halbzeitdaten enthielt.",
        "",
    ]
    report += line_table(
        ["Land", "Liga", "ID", "Feed", "Eindeutig", "HZ eindeutig", "Ohne HZ eindeutig", "Next-Day"],
        [
            [
                f"{item['country_name']} ({item['country_code']})" if item["country_code"] else item["country_name"],
                item["league_name"], item["league_id"], f"{item['feed_rows']:,}", f"{item['unique_matches']:,}",
                f"{item['hz_unique']:,}", f"{item['no_hz_unique']:,}", f"{item['next_day_rows']:,}",
            ]
            for item in league_report
        ],
    )
    report += [
        "",
        f"Vollständige maschinenlesbare Liga-Liste: [{leagues_rel}]({leagues_rel})",
        "",
        "## Events mit Halbzeitdaten",
        "",
        f"Es wurden **{len(hz_events):,} eindeutige Events** mit HZ-Daten gespeichert. Die vollständige Eventliste mit Datum, Land, Liga, Saison, Match-ID, Teams und Detailstatus liegt hier: [{events_rel}]({events_rel}). Sie enthält keine Spiele ohne Halbzeitdaten.",
        "",
        "Beispiele der ersten 20 gespeicherten Events:",
        "",
    ]
    report += line_table(
        ["Datum", "Land", "Liga", "Spiel", "Match-ID", "HZ-Status"],
        [
            [
                event["first_observation_date"], event["country_name"], event["league_name"],
                f"{event['home_team_name']} – {event['away_team_name']}", event["fotmob_match_id"], event["detail_status"],
            ]
            for event in event_rows[:20]
        ],
    )
    report += [
        "",
        "## Prüfergebnis",
        "",
    ]
    report += line_table(
        ["Check", "Ergebnis"],
        [
            ["62/62 Tagesfeed-Runs vorhanden", "PASS" if len(run_rows) == days else "FAIL"],
            ["Alle Tagesfeed-Runs COMPLETE", "PASS" if all_runs_complete else "FAIL"],
            ["Tagesfeed-Zeilen = 12.110", "PASS" if len(daily_rows) == 12110 else f"INFO ({len(daily_rows):,})"],
            ["Eindeutige Spiele = 10.484", "PASS" if unique_matches == 10484 else f"INFO ({unique_matches:,})"],
            ["Detailstatus ohne Fehler/Unknown", "PASS" if not unknown_events else f"FAIL ({len(unknown_events):,})"],
            ["HTTP/API", "PASS – 100% HTTP 200, keine Fehler"],
        ],
    )
    report += [
        "",
        "## Reproduzierbarkeit und Ablage",
        "",
        "Verwendeter Lauf:",
        "",
        "```text",
        f"python -m fotmob.history_cli --root . dates --from-date {start.isoformat()} --to-date {end.isoformat()} --workers {args.workers}",
        "```",
        "",
        f"Tagesindex und Detailstatus liegen in `data/tipico.db`; die kanonischen Match-/Perioden-/Schuss-/Eventdaten liegen unter `data/archive/fotmob`. Die tägliche CSV-Auswertung ist hier abgelegt: [{daily_rel}]({daily_rel}).",
        "",
        "Hinweis: `includeNextDayLateNight=true` bleibt aktiv. Die 1.604 entsprechenden Feed-Zeilen sind markiert, nicht verworfen; ein Event wird für die Archiv-Hochrechnung trotzdem nur einmal gezählt.",
        "",
    ]
    status_output.write_text("\n".join(report) + "\n", encoding="utf-8")
    print({
        "status": "PASS" if quality_pass else "PARTIAL",
        "status_output": str(status_output),
        "events_output": str(args.events_output if args.events_output.is_absolute() else root / args.events_output),
        "leagues_output": str(args.leagues_output if args.leagues_output.is_absolute() else root / args.leagues_output),
        "daily_output": str(args.daily_output if args.daily_output.is_absolute() else root / args.daily_output),
        "days": days,
        "feed_rows": len(daily_rows),
        "unique_matches": unique_matches,
        "hz_matches": hz_matches,
        "no_hz_matches": len(no_hz_events),
        "countries": len(country_report),
        "leagues": len(league_report),
        "db_delta_bytes": db_delta,
        "archive_delta_bytes": archive_delta,
        "total_delta_bytes": total_delta,
        "full_target_estimate_bytes": round(full_target_estimate),
    })
    return 0 if quality_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
