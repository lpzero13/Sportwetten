"""One real-provider paper entry (isolated DB), or retry its real settlement."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import Settings
from intelligence.service import MarketIntelligenceService
from paper.journal import PaperJournal
from paper.service import PaperTradingService
from storage.database import Database
from tipico.client import TipicoClient
from tipico.parser import parse_event_details, parse_live_feed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="Dedicated canary directory, never the production data directory")
    parser.add_argument("--phase", choices=["entry", "settlement"], default="entry")
    parser.add_argument("--event-id")
    args = parser.parse_args()
    settings = Settings(root_dir=args.root.resolve(), request_timeout_seconds=12)
    if args.phase == "entry" and settings.database_path.exists():
        raise SystemExit("Use a new canary directory; entry will not reuse or overwrite a database.")
    if args.phase == "settlement" and not settings.database_path.exists():
        raise SystemExit("Canary database not found.")
    client = TipicoClient(settings)
    db = None
    try:
        if args.phase == "entry":
            event_id = args.event_id
            if not event_id:
                feed = client.get_live_football_events()
                events = [e for e in parse_live_feed(feed.payload) if e.period == "HALF_TIME"]
                if not events:
                    print(json.dumps({"status": "PENDING", "reason": "NO_LIVE_HALFTIME"}))
                    return
                event_id = events[0].event_id
            response = client.get_event_details(event_id)
            details = parse_event_details(response.payload, event_id=event_id)
            db = Database(settings.database_path)
            db.upsert_event(details.event, response.metrics.response_received_at)
            service = PaperTradingService(db, settings)
            for family in ("MARKET_STRUCTURE", "MARKET_ONLY"):
                service.create_portfolio(name=f"Live Canary {family}", family=family, starting_bankroll=100)
            MarketIntelligenceService(db, settings).analyze(details, observed_at=response.metrics.response_received_at,
                                                           now=datetime.now(timezone.utc))
            run = service.process_signals()
        else:
            db = Database(settings.database_path)
            service = PaperTradingService(db, settings)
            def resolve(event_id):
                response = client.get_event_details(event_id)
                event = parse_event_details(response.payload, event_id=event_id).event
                return {"final_score_home": event.score_home, "final_score_away": event.score_away,
                        "status": event.status, "extra_time": event.extra_time, "penalties": event.penalties,
                        "ht_home": event.ht_score_home, "ht_away": event.ht_score_away}
            run = service.settle_open_trades(resolver=resolve)
        decisions = []
        for portfolio in service.portfolios():
            for row in PaperJournal(db).decision_rows(portfolio.portfolio_id):
                context = json.loads(row["payload_json"])
                decisions.append({"family": portfolio.family, "event": context["event"],
                                  "decision": row["decision"], "reason": row["reason"],
                                  "zero": context.get("zero"), "two_plus": context.get("two_plus"),
                                  "probability": context["probability"], "result": row["result_status"]})
        print(json.dumps({"phase": args.phase, "database_path": str(settings.database_path),
                          "observed_at": datetime.now(timezone.utc).isoformat(), "run": run,
                          "decisions": decisions, "trades": [dict(row) for row in db.paper_trade_rows()]}, ensure_ascii=False, indent=2))
    finally:
        if db:
            db.close()
        client.close()


if __name__ == "__main__":
    main()
