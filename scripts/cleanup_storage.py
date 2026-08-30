"""Remove only expired debug raw payloads and completed staging leftovers."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import Settings
from storage.database import Database
from storage.parquet_archive import ParquetArchive
from storage.raw_storage import RawStorage


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--raw-days", type=int, default=None)
    parser.add_argument(
        "--keep-outbox",
        action="store_true",
        help="Exportierte Outbox-Zeilen nicht entfernen.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    root = args.root.resolve()
    settings = Settings.from_env(root)
    raw = RawStorage(
        settings.raw_storage_path,
        enabled=True,
        compression=settings.raw_compression,
    )
    archive = ParquetArchive(settings.archive_path, compression=settings.parquet_compression)
    database = Database(settings.database_path)
    try:
        removed_raw = raw.cleanup_older_than(
            days=max(0, args.raw_days if args.raw_days is not None else settings.debug_raw_retention_days),
            preserve_kinds=("paper_entries",),
        )
        removed_tmp = archive.cleanup_temporary_files()
        removed_outbox = 0
        if not args.keep_outbox:
            removed_outbox = database.delete_exported_snapshot_outbox()
        print(
            json.dumps(
                {
                    "status": "COMPLETED",
                    "raw_debug_files_removed": removed_raw,
                    "parquet_temp_files_removed": removed_tmp,
                    "exported_outbox_rows_removed": removed_outbox,
                    "paper_trades": database.count_rows("paper_trades"),
                    "match_results": database.count_rows("match_results"),
                    "paper_entry_raw_preserved": True,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        database.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
