"""Run the independent, read-only-Tipico paper trading worker."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from concurrent.futures import ThreadPoolExecutor

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import Settings, configure_logging
from paper.service import PaperTradingService
from storage.database import Database
from services.stop_request import StopRequest
from tipico.client import TipicoApiError, TipicoClient
from tipico.parser import parse_event_details


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--interval", type=int, default=5, help="Entry polling interval in seconds.")
    parser.add_argument("--once", action="store_true", help="Run one worker iteration and exit.")
    parser.add_argument("--status", action="store_true", help="Show effective database and worker status without provider requests.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    root = args.root.resolve()
    settings = Settings.from_env(root)
    logger = configure_logging(settings)
    database = Database(settings.database_path)
    if args.status:
        from paper.journal import PaperJournal
        from runtime_status import APP_VERSION
        try:
            print(json.dumps({"app_version": APP_VERSION, "database_path": str(settings.database_path),
                              **PaperJournal(database).health()}, ensure_ascii=False, indent=2))
        finally:
            database.close()
        return
    client = TipicoClient(settings, logger=logger)
    service = PaperTradingService(database, settings, logger=logger)

    def resolve_final(event_id: str) -> dict[str, Any] | None:
        """Refresh only open trades; a live response is never treated as final."""

        try:
            response = client.get_event_details(event_id)
            details = parse_event_details(response.payload, event_id=event_id, logger=logger)
        except (TipicoApiError, TypeError, ValueError, KeyError) as exc:
            logger.warning("Could not refresh open paper event %s: %s", event_id, exc)
            raise RuntimeError(f"Result provider unavailable for {event_id}: {exc}") from exc
        status = str(details.event.status or "").strip().upper()
        if status in {"RUNNING", "LIVE", "BREAK", "HALF_TIME", "HALFTIME", "UNKNOWN", ""}:
            return None
        return {
            "final_score_home": details.event.score_home,
            "final_score_away": details.event.score_away,
            "status": status,
            "extra_time": details.event.extra_time,
            "penalties": details.event.penalties,
            "ht_home": details.event.ht_score_home,
            "ht_away": details.event.ht_score_away,
        }

    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="paper-results")
    settlement_future = None
    stop_request = StopRequest()
    try:
        while not stop_request.is_set():
            result = service.worker_once(resolver=resolve_final, include_settlement=args.once)
            print(json.dumps(result, ensure_ascii=False))
            if args.once:
                break
            if settlement_future is None or settlement_future.done():
                if settlement_future is not None:
                    try:
                        settlement_result = settlement_future.result()
                    except Exception as exc:
                        settlement_result = {"settlement_errors": 1, "error": str(exc)}
                        logger.exception("Background settlement failed")
                    database.set_paper_runtime_setting("settlement_last_result", json.dumps(settlement_result),
                                                       datetime.now(timezone.utc).isoformat())
                settlement_future = executor.submit(service.settle_open_trades, resolver=resolve_final)
            stop_request.wait(max(1, int(args.interval)))
    finally:
        executor.shutdown(wait=True)
        client.close()
        database.close()


if __name__ == "__main__":
    main()
