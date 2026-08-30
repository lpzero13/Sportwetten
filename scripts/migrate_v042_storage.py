"""Export the existing SQLite snapshot history to the V0.4.2 Parquet archive.

The default operation is deliberately limited to EXPORT, VERIFY and REPORT.
It never deletes SQLite rows.  Any later cleanup therefore remains an explicit
and separately reviewable operation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import Settings
from storage.database import Database
from storage.parquet_archive import ARCHIVE_SCHEMA_VERSION, ParquetArchive

try:
    import pyarrow.parquet as pq
except ImportError:  # pragma: no cover - requirements install provides it
    pq = None


TABLES = (
    "events",
    "event_states",
    "current_event_state",
    "markets",
    "outcomes",
    "odds_history",
    "competitions",
    "snapshots",
    "snapshot_outbox",
    "match_results",
    "market_presence",
    "canonical_outcomes",
    "current_canonical_outcomes",
    "strategy_evaluations",
    "current_strategy_evaluations",
    "paper_portfolios",
    "paper_trades",
    "paper_bankroll_transactions",
    "paper_signal_log",
    "paper_worker_runs",
)


def _value(row: Mapping[str, Any], key: str, default: Any = None) -> Any:
    available = row.keys() if hasattr(row, "keys") else row
    value = row[key] if key in available else None
    return default if value is None else value


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _minute(value: Any) -> int | None:
    if value is None:
        return None
    digits = "".join(char for char in str(value) if char.isdigit())
    return int(digits) if digits else None


def _second_half_values(row: Mapping[str, Any]) -> tuple[int | None, str | None]:
    second_half = _int_or_none(_value(row, "second_half_goals"))
    if second_half is None:
        ft_home = _int_or_none(_value(row, "score_home", _value(row, "ft_home")))
        ft_away = _int_or_none(_value(row, "score_away", _value(row, "ft_away")))
        ht_home = _int_or_none(_value(row, "ht_score_home", _value(row, "ht_home")))
        ht_away = _int_or_none(_value(row, "ht_score_away", _value(row, "ht_away")))
        if None not in {ft_home, ft_away, ht_home, ht_away}:
            second_half = int(ft_home + ft_away - ht_home - ht_away)
    if second_half is None or second_half < 0:
        return second_half, None
    return second_half, "2_PLUS" if second_half >= 2 else str(second_half)


def row_to_record(row: Mapping[str, Any]) -> dict[str, Any]:
    """Convert both V0.3 and V0.4 SQLite rows to the flat archive schema."""

    event_id = str(_value(row, "event_id", ""))
    captured_at = str(_value(row, "observed_at", datetime.now(timezone.utc).isoformat()))
    competition_id = _value(row, "competition_id", _value(row, "event_competition_id"))
    competition_name = _value(row, "competition_name", _value(row, "event_competition_name"))
    competition_country = _value(
        row,
        "competition_country",
        _value(row, "event_competition_country"),
    )
    home_team = _value(row, "home_team", _value(row, "event_home_team"))
    away_team = _value(row, "away_team", _value(row, "event_away_team"))
    kickoff_at = _value(row, "kickoff_time", _value(row, "event_kickoff_time"))
    ht_home = _int_or_none(_value(row, "ht_score_home", _value(row, "event_ht_score_home")))
    ht_away = _int_or_none(_value(row, "ht_score_away", _value(row, "event_ht_score_away")))
    score_home = _int_or_none(_value(row, "score_home"))
    score_away = _int_or_none(_value(row, "score_away"))
    first_half_goals = (
        int(ht_home + ht_away) if ht_home is not None and ht_away is not None else None
    )
    second_half_goals, derived_class = _second_half_values(row)
    snapshot_id = int(_value(row, "snapshot_id", 0))
    record: dict[str, Any] = {
        "schema_version": ARCHIVE_SCHEMA_VERSION,
        "snapshot_id": snapshot_id,
        "event_id": event_id,
        "competition_id": str(competition_id) if competition_id is not None else None,
        "competition_name": competition_name,
        "competition_country": competition_country,
        "home_team": home_team,
        "away_team": away_team,
        "kickoff_at": kickoff_at,
        "snapshot_type": str(_value(row, "snapshot_type", "LEGACY")),
        "captured_at": captured_at,
        "match_minute": _int_or_none(_value(row, "match_minute"))
        or _minute(_value(row, "display_time")),
        "score_home": score_home,
        "score_away": score_away,
        "ht_home": ht_home,
        "ht_away": ht_away,
        "first_half_goals": first_half_goals,
        "second_half_goals": second_half_goals,
        "second_half_goal_class": _value(row, "second_half_goal_class", derived_class),
        "match_status": _value(row, "match_status"),
        "display_time": _value(row, "display_time"),
        "snapshot_quality": _value(row, "snapshot_quality"),
        "market_count": int(_value(row, "market_count", 0) or 0),
        "outcome_count": int(_value(row, "outcome_count", 0) or 0),
        "q_zero_best": _float_or_none(_value(row, "q_zero_best")),
        "q_zero_source_type": _value(row, "q_zero_source_type"),
        "q_zero_market_id": _value(row, "q_zero_market_id"),
        "q_zero_outcome_id": _value(row, "q_zero_outcome_id"),
        "q_two_plus_best": _float_or_none(_value(row, "q_two_plus_best")),
        "q_two_plus_source_type": _value(row, "q_two_plus_source_type"),
        "q_two_plus_market_id": _value(row, "q_two_plus_market_id"),
        "q_two_plus_outcome_id": _value(row, "q_two_plus_outcome_id"),
        "remaining_under_05": _float_or_none(_value(row, "remaining_under_05")),
        "remaining_over_05": _float_or_none(_value(row, "remaining_over_05")),
        "remaining_under_15": _float_or_none(_value(row, "remaining_under_15")),
        "remaining_over_15": _float_or_none(_value(row, "remaining_over_15")),
        "p0_market": _float_or_none(_value(row, "p0_market")),
        "p1_market": _float_or_none(_value(row, "p1_market")),
        "p2plus_market": _float_or_none(_value(row, "p2plus_market")),
        "p1_break_even": _float_or_none(_value(row, "p1_break_even")),
        "p1_buffer": _float_or_none(_value(row, "p1_buffer")),
        "win_roi": _float_or_none(_value(row, "win_roi")),
        "normalizer_version": _value(row, "normalizer_version"),
        "strategy_version": _value(row, "strategy_version"),
        "relevant_markets_json": str(_value(row, "relevant_markets_json", "[]") or "[]"),
        "goal_at": _value(row, "goal_at"),
        "reopen_at": _value(row, "reopen_at"),
        "reopen_delay_seconds": _float_or_none(_value(row, "reopen_delay_seconds")),
        "raw_payload_path": _value(row, "raw_payload_path"),
        "payload_hash": _value(row, "payload_hash"),
    }
    if not record["payload_hash"]:
        encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, default=str)
        record["payload_hash"] = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return record


def _row_counts(database: Database) -> dict[str, int]:
    return {table: database.count_rows(table) for table in TABLES}


def _verify(files: list[Path], records: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    if pq is None:
        return ["pyarrow ist nicht installiert; Parquet kann nicht validiert werden."]
    exported: list[dict[str, Any]] = []
    for path in files:
        if not path.exists():
            errors.append(f"Fehlende Archivdatei: {path}")
            continue
        try:
            exported.extend(pq.read_table(path).to_pylist())
        except Exception as exc:  # pragma: no cover - depends on filesystem corruption
            errors.append(f"Parquet konnte nicht gelesen werden ({path}): {exc}")
    if len(exported) != len(records):
        errors.append(f"Row Count abweichend: SQLite={len(records)}, Parquet={len(exported)}")
    input_keys = {
        (int(row["snapshot_id"]), str(row["event_id"]), str(row["snapshot_type"]))
        for row in records
    }
    output_keys = {
        (int(row["snapshot_id"]), str(row["event_id"]), str(row["snapshot_type"]))
        for row in exported
    }
    if input_keys != output_keys:
        errors.append("Event IDs/Snapshot Types stimmen zwischen SQLite und Parquet nicht überein.")
    by_id = {int(row["snapshot_id"]): row for row in exported}
    for source in records[: min(25, len(records))]:
        target = by_id.get(int(source["snapshot_id"]))
        if target is None:
            continue
        for field in (
            "q_zero_best", "q_two_plus_best", "score_home", "score_away",
            "ht_home", "ht_away", "second_half_goals", "second_half_goal_class",
        ):
            if target.get(field) != source.get(field):
                errors.append(
                    f"Stichprobe abweichend: snapshot_id={source['snapshot_id']} field={field}"
                )
                break
        if target.get("schema_version") != ARCHIVE_SCHEMA_VERSION:
            errors.append(f"Schema-Version fehlt bei snapshot_id={source['snapshot_id']}")
    return errors


def _markdown_report(
    *,
    root: Path,
    archive: ParquetArchive,
    before_size: int,
    after_size: int,
    before_rows: dict[str, int],
    records: list[dict[str, Any]],
    files: list[Path],
    errors: list[str],
) -> str:
    removable = {
        key: before_rows[key]
        for key in (
            "event_states", "odds_history", "market_presence",
            "canonical_outcomes", "strategy_evaluations",
        )
    }
    validation = "PASS" if not errors else "FAIL"
    lines = [
        "# Storage Migration Report V0.4.2",
        "",
        f"- Zeitpunkt: `{datetime.now(timezone.utc).isoformat()}`",
        f"- Root: `{root}`",
        f"- Zielarchiv: `{archive.snapshot_root}`",
        "- Ablauf: `EXPORT → VERIFY → REPORT`",
        "- SQLite-History wurde in diesem Lauf nicht gelöscht.",
        "",
        "## Größen",
        "",
        f"- DB-Größe vorher (inkl. WAL/SHM): **{before_size / 1024 / 1024:.3f} MB**",
        f"- DB-Größe nach Export: **{after_size / 1024 / 1024:.3f} MB**",
        f"- Parquet-Größe dieses Archivs: **{archive.total_size_bytes / 1024 / 1024:.3f} MB**",
        f"- Parquet-Dateien: **{len(files)}**",
        "- Erwartete DB-Größe nach optionalem Cleanup: wird erst nach Backup und explizitem Cleanup gemessen.",
        "",
        "## Snapshot-Export",
        "",
        f"- Exportierte Rows: **{len(records)}**",
        f"- Validierungsstatus: **{validation}**",
        "",
        "## Rows vor Migration",
        "",
        "| Tabelle | Rows |",
        "|---|---:|",
    ]
    lines.extend(f"| `{table}` | {count} |" for table, count in before_rows.items())
    lines.extend(
        [
            "",
            "## Potenziell redundante alte Historie",
            "",
            "Diese Rows sind nur Kandidaten für einen späteren, ausdrücklich freigegebenen Cleanup. Paper-Trades, Match-Results und Ledger bleiben erhalten.",
            "",
            "| Tabelle | potenziell prüfbare Rows |",
            "|---|---:|",
        ]
    )
    lines.extend(f"| `{table}` | {count} |" for table, count in removable.items())
    if errors:
        lines.extend(["", "## Validierungsfehler", "", *[f"- {error}" for error in errors]])
    else:
        lines.extend(["", "## Validierung", "", "- Row Count, Schlüssel, Schema-Version und Stichprobenquoten sind konsistent."])
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("outputs/STORAGE_MIGRATION_REPORT.md"),
    )
    parser.add_argument("--archive", type=Path, default=None)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    root = args.root.resolve()
    settings = Settings.from_env(root)
    archive_root = (args.archive or settings.archive_path).resolve()
    report_path = args.report if args.report.is_absolute() else root / args.report
    report_path.parent.mkdir(parents=True, exist_ok=True)
    database = Database(settings.database_path)
    archive = ParquetArchive(archive_root, compression=settings.parquet_compression)
    before_rows = _row_counts(database)
    before_size = database.database_size_bytes
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    files: list[Path] = []
    try:
        rows = database.connection.execute(
            """
            SELECT s.*, e.competition_id AS event_competition_id,
                   e.competition_name AS event_competition_name,
                   e.competition_country AS event_competition_country,
                   e.home_team AS event_home_team, e.away_team AS event_away_team,
                   e.kickoff_time AS event_kickoff_time,
                   e.ht_score_home AS event_ht_score_home,
                   e.ht_score_away AS event_ht_score_away
            FROM snapshots s
            LEFT JOIN events e ON e.event_id = s.event_id
            ORDER BY s.observed_at, s.snapshot_id
            """
        ).fetchall()
        records = [row_to_record(row) for row in rows]
        if records:
            result = archive.write_records(records)
            files = [Path(path) for path in result["files"]]
        errors = _verify(files, records)
    except Exception as exc:
        errors.append(str(exc))
    finally:
        after_size = database.database_size_bytes
        report = _markdown_report(
            root=root,
            archive=archive,
            before_size=before_size,
            after_size=after_size,
            before_rows=before_rows,
            records=records,
            files=files,
            errors=errors,
        )
        report_path.write_text(report, encoding="utf-8")
        database.close()
    print(
        json.dumps(
            {
                "status": "PASS" if not errors else "FAIL",
                "report": str(report_path),
                "rows": len(records),
                "files": [str(path) for path in files],
                "errors": errors,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
