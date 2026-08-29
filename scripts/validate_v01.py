"""Run a small, honest V0.1 live smoke test against Tipico.

This intentionally fetches one live overview and one selected event detail.
It is not the 60-minute acceptance test and does not perform a manual quote
comparison.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import Settings, configure_logging
from services.event_service import EventService
from services.market_service import MarketService
from storage.database import Database
from storage.raw_storage import RawStorage
from tipico.client import TipicoClient


KNOWN_TYPES = {
    "standard",
    "standard-rest",
    "points-more-less-rest",
    "next-point",
    "handicap",
    "score-both",
    "section-points-more-less",
    "double-chance",
    "head-to-head",
}


def run(root: Path, requested_event_id: str | None = None) -> dict:
    settings = Settings.from_env(root)
    logger = configure_logging(settings)
    database = Database(settings.database_path)
    raw_storage = RawStorage(settings.raw_storage_path, enabled=True)
    client = TipicoClient(settings, logger=logger)
    events = EventService(client, database, raw_storage, settings, logger=logger)
    markets = MarketService(client, database, raw_storage, settings, logger=logger)

    try:
        overview = events.refresh()
        live_events = overview.events
        selected = next(
            (
                event
                for event in live_events
                if requested_event_id and event.event_id == requested_event_id
            ),
            None,
        )
        if selected is None:
            selected = next(
                (
                    event
                    for event in live_events
                    if (event.bet_markets_count or 0) > 0
                ),
                live_events[0] if live_events else None,
            )

        detail_result = None
        if selected is not None:
            detail_result = markets.load_event_details(
                selected.event_id,
                overview_event=selected,
            )

        details = detail_result.details if detail_result else None
        unknown_types = sorted(
            {
                market.type
                for market in (details.markets if details else [])
                if market.type not in KNOWN_TYPES
            }
        )
        api_requests = events.request_count + markets.request_count
        api_errors = events.error_count + markets.error_count
        return {
            "tipico_v01_status": (
                "PASS"
                if overview.success and (detail_result is None or detail_result.success)
                else "PARTIAL"
            ),
            "tested_live_events": len(live_events),
            "tested_competitions": len(
                {event.competition_id for event in live_events if event.competition_id}
            ),
            "tested_event_details": 1 if detail_result else 0,
            "selected_event_id": selected.event_id if selected else None,
            "highest_overview_market_count": max(
                (event.bet_markets_count or 0 for event in live_events),
                default=0,
            ),
            "selected_event_market_count": details.market_count if details else 0,
            "selected_event_outcome_count": details.outcome_count if details else 0,
            "quote_match_rate": "MANUAL_REQUIRED",
            "api_requests": api_requests,
            "api_errors": api_errors,
            "api_error_rate": (api_errors / api_requests) if api_requests else None,
            "parsing_errors": events.parse_error_count + markets.parse_error_count,
            "unknown_market_types": unknown_types,
            "manual_comparison": "NOT_RUN",
            "long_running_stability": "NOT_RUN",
            "raw_storage": settings.store_raw_responses,
            "database": str(settings.database_path),
        }
    finally:
        client.close()
        database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Runtime root for database, raw payloads and logs",
    )
    parser.add_argument("--event-id", help="Optional live event ID to inspect")
    args = parser.parse_args()
    print(json.dumps(run(args.root.resolve(), args.event_id), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
