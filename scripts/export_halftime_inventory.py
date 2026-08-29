"""Export a machine-readable halftime market inventory from a raw payload."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tipico.parser import parse_event_details


def _score(event: object) -> dict[str, int | None]:
    return {
        "home": getattr(event, "score_home", None),
        "away": getattr(event, "score_away", None),
    }


def export_inventory(payload_path: Path, output_path: Path) -> Path:
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    details = parse_event_details(payload)
    event = details.event
    report = {
        "observed_at": datetime.fromtimestamp(
            payload_path.stat().st_mtime, tz=timezone.utc
        ).isoformat(),
        "source_raw_payload": str(payload_path),
        "event_id": event.event_id,
        "competition": event.competition_name,
        "teams": {
            "home": event.home_team,
            "away": event.away_team,
        },
        "halftime_score": _score(event),
        "display_time": event.display_minute,
        "market_count": details.market_count,
        "outcome_count": details.outcome_count,
        "markets": [
            {
                "market_id": market.market_id,
                "type": market.type,
                "caption": market.caption,
                "fixedParam": market.fixed_param,
                "status": market.status,
                "outcomes": [
                    {
                        "outcome_id": outcome.outcome_id,
                        "caption": outcome.caption,
                        "choiceParam": outcome.choice_param,
                        "odds": outcome.odds,
                        "quoteRaw": outcome.quote_raw,
                        "quoteFloatValue": outcome.quote_float_value,
                        "status": outcome.status,
                        "available": outcome.is_available,
                    }
                    for outcome in market.outcomes
                ],
            }
            for market in details.markets
        ],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(export_inventory(args.payload.resolve(), args.output.resolve()))


if __name__ == "__main__":
    main()
