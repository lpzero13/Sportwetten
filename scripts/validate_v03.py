"""Run a reproducible live-feed smoke validation for V0.3."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import Settings, configure_logging
from intelligence.service import MarketIntelligenceService
from storage.database import Database
from tipico.client import TipicoClient
from tipico.parser import parse_event_details, parse_live_feed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="work/v03-validation")
    parser.add_argument("--max-events", type=int, default=10)
    args = parser.parse_args()

    settings = Settings(root_dir=Path(args.root).resolve())
    logger = configure_logging(settings)
    database = Database(settings.database_path)
    client = TipicoClient(settings, logger=logger)
    service = MarketIntelligenceService(database, settings, logger=logger)
    summary: dict[str, object] = {}
    try:
        feed = client.get_live_football_events()
        live_events = parse_live_feed(feed.payload, logger=logger)
        halftime = [
            event
            for event in live_events
            if event.display_minute.upper() == "HZ"
            or event.period.upper() in {"HALF_TIME", "HALFTIME", "HT"}
        ][: max(1, args.max_events)]
        event_rows: list[dict[str, object]] = []
        equivalent_groups = 0
        best_odds_checks = 0
        probability_ok = 0
        strategy_ok = 0
        for event in halftime:
            response = client.get_event_details(event.event_id)
            details = parse_event_details(
                response.payload,
                event_id=event.event_id,
                logger=logger,
            )
            details.event.competition_name = event.competition_name
            analysis = service.analyze(
                details,
                observed_at=response.metrics.response_received_at,
                now=datetime.now(timezone.utc),
            )
            for group in (analysis.zero_equivalence, analysis.two_plus_equivalence):
                if group.status != "EQUIVALENT":
                    continue
                equivalent_groups += 1
                selected = group.best_odds.selected if group.best_odds else None
                available = [
                    candidate.odds
                    for candidate in group.candidates
                    if candidate.available
                    and candidate.odds is not None
                    and candidate.status
                    not in {"paused", "suspended", "stopped", "closed", "inactive"}
                ]
                if selected is not None and available and selected.odds == max(available):
                    best_odds_checks += 1
            probability_ok += analysis.probability.status == "OK"
            strategy_ok += (
                analysis.strategy.status == "OK"
                and analysis.strategy.q_zero is not None
                and analysis.strategy.q_two_plus is not None
            )
            event_rows.append(
                {
                    "event_id": event.event_id,
                    "teams": f"{event.home_team} - {event.away_team}",
                    "score": event.score_label,
                    "markets": details.market_count,
                    "outcomes": details.outcome_count,
                    "known_outcomes": analysis.known_outcome_count,
                    "unknown_outcomes": analysis.unknown_outcome_count,
                    "zero_status": analysis.zero_equivalence.status,
                    "zero_quote": (
                        analysis.strategy.q_zero
                        if analysis.strategy.q_zero is not None
                        else None
                    ),
                    "two_plus_status": analysis.two_plus_equivalence.status,
                    "two_plus_quote": (
                        analysis.strategy.q_two_plus
                        if analysis.strategy.q_two_plus is not None
                        else None
                    ),
                    "probability_status": analysis.probability.status,
                    "strategy_status": analysis.strategy.status,
                }
            )
        summary = {
            "observed_at": feed.metrics.response_received_at,
            "live_soccer_events": len(live_events),
            "halftime_events_seen": len(halftime),
            "events_tested": len(event_rows),
            "equivalent_groups": equivalent_groups,
            "best_odds_checks": best_odds_checks,
            "probability_ok": probability_ok,
            "strategy_quote_sets": strategy_ok,
            "canonical_outcomes": database.count_rows("canonical_outcomes"),
            "strategy_evaluations": database.count_rows("strategy_evaluations"),
            "events": event_rows,
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    finally:
        client.close()
        database.close()


if __name__ == "__main__":
    raise SystemExit(main())
