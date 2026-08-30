"""Run the independent, read-only-Tipico paper trading worker."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import Settings, configure_logging
from paper.service import PaperTradingService
from storage.database import Database
from storage.raw_storage import RawStorage
from tipico.client import TipicoApiError, TipicoClient
from tipico.parser import parse_event_details


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--interval", type=int, default=30, help="Polling interval in seconds.")
    parser.add_argument("--once", action="store_true", help="Run one worker iteration and exit.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    root = args.root.resolve()
    settings = Settings.from_env(root)
    logger = configure_logging(settings)
    database = Database(settings.database_path)
    client = TipicoClient(settings, logger=logger)
    entry_raw = RawStorage(
        settings.raw_storage_path,
        enabled=settings.raw_paper_entry,
        compression=settings.raw_compression,
    )

    def store_entry_raw(event_id: str) -> str | None:
        response = client.get_event_details(event_id)
        result = entry_raw.store(
            "paper_entries",
            event_id,
            response.payload,
            observed_at=response.metrics.response_received_at,
        )
        resolved = result.path or entry_raw.path_for_hash(
            "paper_entries",
            event_id,
            result.content_hash,
            observed_at=response.metrics.response_received_at,
        )
        return str(resolved) if resolved else None

    service = PaperTradingService(
        database,
        settings,
        logger=logger,
        entry_raw_store=store_entry_raw,
    )

    def resolve_final(event_id: str) -> dict[str, Any] | None:
        """Refresh only open trades; a live response is never treated as final."""

        try:
            response = client.get_event_details(event_id)
            details = parse_event_details(response.payload, event_id=event_id, logger=logger)
        except (TipicoApiError, TypeError, ValueError, KeyError) as exc:
            logger.warning("Could not refresh open paper event %s: %s", event_id, exc)
            return None
        status = str(details.event.status or "").strip().upper()
        if status in {"RUNNING", "LIVE", "BREAK", "HALF_TIME", "HALFTIME", "UNKNOWN", ""}:
            return None
        return {
            "final_score_home": details.event.score_home,
            "final_score_away": details.event.score_away,
            "status": status,
            "extra_time": details.event.extra_time,
            "penalties": details.event.penalties,
        }

    try:
        while True:
            result = service.worker_once(resolver=resolve_final)
            print(json.dumps(result, ensure_ascii=False))
            if args.once:
                break
            time.sleep(max(10, int(args.interval)))
    finally:
        client.close()
        database.close()


if __name__ == "__main__":
    main()
