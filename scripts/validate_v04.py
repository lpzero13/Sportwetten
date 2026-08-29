"""Run a read-only V0.4 schema and live-feed smoke validation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import Settings, configure_logging
from storage.database import Database
from tipico.client import TipicoApiError, TipicoClient
from tipico.parser import parse_live_feed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--max-events", type=int, default=5)
    parser.add_argument("--skip-live", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    settings = Settings.from_env(root)
    logger = configure_logging(settings)
    database = Database(settings.database_path)
    result: dict[str, object] = {
        "database": str(settings.database_path),
        "tables": {},
        "country_competitions": 0,
        "bundesliga_countries": [],
    }
    table_names = (
        "events", "competitions", "snapshots", "canonical_outcomes",
        "strategy_evaluations", "paper_portfolios", "paper_trades",
        "paper_bankroll_transactions", "paper_signal_log",
    )
    result["tables"] = {name: database.count_rows(name) for name in table_names}
    competitions = database.list_competitions()
    with_country = [row for row in competitions if row["country_or_region"]]
    result["country_competitions"] = len(with_country)
    result["bundesliga_countries"] = sorted({
        str(row["country_or_region"])
        for row in competitions
        if "bundesliga" in str(row["competition_name"]).casefold()
        and row["country_or_region"]
    })

    if not args.skip_live:
        client = TipicoClient(settings, logger=logger)
        try:
            response = client.get_live_football_events()
            events = parse_live_feed(response.payload, logger=logger)
            result["live"] = {
                "success": True,
                "events": len(events),
                "sample": [
                    {
                        "event_id": event.event_id,
                        "competition": event.competition_name,
                        "country": event.competition_country,
                    }
                    for event in events[: max(1, int(args.max_events))]
                ],
                "metrics": {
                    "response_time_ms": response.metrics.response_time_ms,
                    "payload_size": response.metrics.payload_size,
                },
            }
        except (TipicoApiError, TypeError, ValueError, KeyError) as exc:
            result["live"] = {"success": False, "error": str(exc)}
        finally:
            client.close()
    database.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
