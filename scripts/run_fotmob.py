#!/usr/bin/env python3
"""Optional FotMob current-state worker.

It refreshes only already confirmed provider links.  Match discovery and
manual linking happen through the dashboard or an explicit integration call;
this worker never guesses an event from a same-day list.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import Settings, configure_logging
from fotmob.service import FotMobService
from storage.database import Database


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Optional FotMob worker")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--once", action="store_true")
    return parser.parse_args()


def run_once(service: FotMobService, logger: logging.Logger) -> dict[str, int]:
    refreshed = 0
    errors = 0
    if not service.enabled:
        logger.info("FotMob worker disabled: FOTMOB_ENABLED=false")
        return {"refreshed": 0, "errors": 0}
    if not service.automated_worker_allowed:
        logger.info(
            "FotMob worker disabled by provider policy: decision=%s automated_usage=%s",
            service.provider_decision,
            service.automated_usage,
        )
        return {"refreshed": 0, "errors": 0}
    for link in service.store.links(limit=500):
        if link["match_status"] not in {"EXACT", "HIGH_CONFIDENCE", "MANUALLY_CONFIRMED"}:
            continue
        result = service.refresh_link(str(link["internal_match_id"]), snapshot_type="AUTO")
        if result.success:
            refreshed += 1
        else:
            errors += 1
            logger.warning("FotMob refresh failed for %s: %s", link["internal_match_id"], result.error)
    export = service.export_pending()
    if export.get("errors"):
        errors += int(export["errors"])
    logger.info("FotMob worker: refreshed=%d errors=%d export=%s", refreshed, errors, export)
    return {"refreshed": refreshed, "errors": errors}


def main() -> None:
    args = parse_args()
    settings = Settings.from_env(args.root.resolve())
    logger = configure_logging(settings)
    database = Database(settings.database_path)
    service = FotMobService(settings, database, logger=logger)
    try:
        if not service.enabled or not service.automated_worker_allowed:
            run_once(service, logger)
            return
        if args.once:
            run_once(service, logger)
            return
        while True:
            run_once(service, logger)
            time.sleep(max(5, settings.fotmob_poll_seconds))
    except KeyboardInterrupt:
        logger.info("FotMob worker stopped")
    finally:
        database.close()


if __name__ == "__main__":
    main()
