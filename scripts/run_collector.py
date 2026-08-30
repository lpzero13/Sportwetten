"""Run the independent Tipico historical collector."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import Settings, configure_logging
from fotmob.service import FotMobService
from services.collector import Collector
from storage.database import Database
from storage.raw_storage import RawStorage
from tipico.client import TipicoClient


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument(
        "--duration-minutes",
        type=float,
        default=None,
        help="Stop after this duration; omit for continuous collection.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Fetch the live feed once and process immediately due detail jobs.",
    )
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--feed-interval", type=int, default=None)
    parser.add_argument("--core-interval", type=int, default=None)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    root = args.root.resolve()
    settings = Settings.from_env(root)
    overrides = {}
    if args.workers is not None:
        overrides["collector_detail_workers"] = max(1, min(5, args.workers))
    if args.feed_interval is not None:
        overrides["collector_feed_refresh_seconds"] = max(1, args.feed_interval)
    if args.core_interval is not None:
        overrides["collector_core_refresh_seconds"] = max(1, args.core_interval)
    if overrides:
        settings = replace(settings, **overrides)

    logger = configure_logging(settings)
    database = Database(settings.database_path)
    raw_storage = RawStorage(
        settings.raw_storage_path,
        # Collector writes only explicit HALFTIME/debug slots. Enabling the
        # sink here does not enable raw-on-every-poll.
        enabled=True,
        compression=settings.raw_compression,
    )
    client = TipicoClient(settings, logger=logger)
    fotmob_service = FotMobService(settings, database, logger=logger)
    collector = Collector(
        client,
        database,
        raw_storage,
        settings,
        logger=logger,
        fotmob_service=fotmob_service,
    )
    try:
        status = (
            collector.run_once()
            if args.once
            else collector.run_forever(duration_minutes=args.duration_minutes)
        )
        print(json.dumps(status, ensure_ascii=False, indent=2))
    finally:
        client.close()
        database.close()


if __name__ == "__main__":
    main()
