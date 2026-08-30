#!/usr/bin/env python3
"""Import the legacy FotMob SQLite archive into the V0.5.3 history layer."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import defaultdict
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import Settings
from fotmob.history_models import FotMobSeasonRef
from fotmob.history_storage import FotMobHistoricalArchive, FotMobHistoryStore
from fotmob.legacy import LEGACY_DB_DEFAULT, LegacyFotMobReader, season_for_date
from storage.database import Database


def _season_ref(store: FotMobHistoryStore, row: object) -> FotMobSeasonRef:
    season = season_for_date(row["date"])
    if season is None:
        long_label, short_label = "unknown", "unknown"
    else:
        long_label, short_label = season
    for existing in store.seasons("54"):
        if str(existing["season_label"]) in {long_label, short_label}:
            return FotMobSeasonRef(
                provider=str(existing["provider"]),
                league_id=str(existing["league_id"]),
                season_id=str(existing["season_id"]),
                season_label=str(existing["season_label"]),
                league_name=existing["league_name"],
                country=existing["country"],
                discovered_at=existing["discovered_at"],
            )
    reference = FotMobSeasonRef(
        provider="FOTMOB",
        league_id="54",
        season_id=f"legacy-{long_label.replace('/', '-')}",
        season_label=short_label,
        league_name="Bundesliga",
        country="GER",
    )
    store.upsert_seasons([reference])
    return reference


def _write_report(path: Path, result: dict) -> None:
    counts = result["counts"]
    archive = result.get("archive", {})
    path.write_text(
        "\n".join(
            (
                "# LEGACY_IMPORT_REPORT",
                "",
                "Pipeline: Legacy SQLite → adapter → validation/target recalculation → `fotmob_historical_v1` → Parquet.",
                "The source database was opened read-only; fresh provider data has precedence over this import.",
                "",
                f"- Input: `{result['input']}`",
                f"- Backup: `{result.get('backup') or 'not requested'}`",
                f"- Database: `{result['database']}`",
                f"- Archive root: `{result['archive_root']}`",
                "",
                "| Counter | Value |",
                "| --- | ---: |",
                *[f"| {key} | {value} |" for key, value in counts.items()],
                "",
                "## Archive result",
                "",
                f"- Written Parquet rows: **{archive.get('written', 0)}**",
                f"- Skipped because a same-or-better source exists: **{archive.get('skipped', 0)}**",
                f"- Replaced lower-priority rows: **{archive.get('replaced', 0)}**",
                f"- Parquet files: **{len(archive.get('paths', []))}**",
                f"- Parquet size: **{result.get('parquet_size_bytes', 0)} bytes**",
                "",
                "Quality classes: `COMPLETE`, `PARTIAL`, `SCORE_ONLY`, `INVALID`. The target is recalculated from final minus half-time total goals.",
                "",
            )
        ),
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import legacy FotMob SQLite rows")
    parser.add_argument("--input", type=Path, default=LEGACY_DB_DEFAULT)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--league-id", default="54")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--store-raw", action="store_true")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "outputs" / "LEGACY_IMPORT_REPORT.md")
    parser.add_argument("--no-backup", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = args.root.resolve()
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    backup: Path | None = None
    if not args.no_backup:
        backup = root / "work" / "v053-legacy-backup" / args.input.name
        backup.parent.mkdir(parents=True, exist_ok=True)
        if not backup.exists() or backup.stat().st_size != args.input.stat().st_size:
            shutil.copy2(args.input, backup)

    settings = Settings.from_env(root)
    database = Database(settings.database_path)
    store = FotMobHistoryStore(
        database,
        settings.archive_path,
        settings.raw_storage_path if args.store_raw else None,
    )
    archive = FotMobHistoricalArchive(settings.archive_path, settings.parquet_compression)
    counts = defaultdict(int)
    archive_result = {"written": 0, "skipped": 0, "replaced": 0, "paths": []}
    seen_ids: set[str] = set()
    buffer: list[dict] = []
    imported_rows: list[dict] = []

    def flush() -> None:
        nonlocal buffer, archive_result
        if not buffer:
            return
        result = archive.write_batch(store, buffer)
        for key in ("written", "skipped", "replaced"):
            archive_result[key] = int(archive_result.get(key, 0)) + int(result.get(key, 0))
        archive_result["paths"] = list(dict.fromkeys([
            *archive_result.get("paths", []), *result.get("paths", [])
        ]))
        buffer = []

    try:
        with LegacyFotMobReader(args.input) as reader:
            for legacy_row in reader.rows(args.league_id):
                provider_id = str(legacy_row["match_id"])
                counts["input"] += 1
                if provider_id in seen_ids:
                    counts["duplicate"] += 1
                    continue
                seen_ids.add(provider_id)
                season = _season_ref(store, legacy_row)
                record = reader.to_index_record(
                    legacy_row,
                    season_id=season.season_id,
                    season_label=season.season_label,
                )
                store.upsert_match_index([record])
                normalized = reader.to_historical_row(
                    legacy_row,
                    season_id=season.season_id,
                    season_label=season.season_label,
                )
                counts["valid"] += 1
                counts[str(normalized["data_quality"]).lower()] += 1
                raw_path = None
                if args.store_raw:
                    payload = {
                        "legacy_source": str(reader.path),
                        "match": {key: legacy_row[key] for key in legacy_row.keys() if key != "data_json"},
                        "data_json": json.loads(str(legacy_row["data_json"] or "{}")),
                    }
                    raw_path, _ = store.save_raw_payload(
                        payload,
                        league_id="54",
                        season_label=season.season_label,
                        provider_match_id=provider_id,
                    )
                    normalized["raw_payload_path"] = raw_path
                buffer.append(normalized)
                imported_rows.append(normalized)
                if len(buffer) >= max(1, int(args.batch_size)):
                    flush()
            flush()
    finally:
        database.close()

    # ``write_batch`` is the canonical source-priority gate.  Count rows that
    # became active in the catalog separately from rows merely seen in input.
    counts["imported"] = int(archive_result.get("written", 0))
    counts["skipped"] = int(archive_result.get("skipped", 0))
    counts["replaced"] = int(archive_result.get("replaced", 0))
    parquet_size = sum(
        path.stat().st_size
        for path in settings.archive_path.rglob("*.parquet")
        if path.is_file()
    )
    result = {
        "status": "PASS",
        "input": str(args.input.resolve()),
        "backup": str(backup) if backup else None,
        "database": str(settings.database_path),
        "archive_root": str(settings.archive_path),
        "counts": dict(counts),
        "archive": archive_result,
        "parquet_size_bytes": parquet_size,
        "report": str(output),
    }
    _write_report(output, result)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
