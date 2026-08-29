"""Backfill competition countries from stored Tipico live payloads."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storage.database import Database
from tipico.parser import parse_live_feed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()
    root = args.root.resolve()
    database = Database(root / "data" / "tipico.db")
    files = sorted((root / "data" / "raw").glob("**/live/*.json"))
    updated_events = 0
    seen_competitions: set[str] = set()
    try:
        for path in files:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                events = parse_live_feed(payload)
                stamp = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
            except (OSError, ValueError, TypeError, KeyError):
                continue
            for event in events:
                if not event.competition_country:
                    continue
                if database.update_event_competition_country(
                    event.event_id, event.competition_country, observed_at=stamp
                ):
                    updated_events += 1
                if event.competition_id:
                    seen_competitions.add(str(event.competition_id))
        print(json.dumps({
            "files_scanned": len(files),
            "events_updated": updated_events,
            "competitions_seen_with_country": len(seen_competitions),
        }, ensure_ascii=False, indent=2))
    finally:
        database.close()


if __name__ == "__main__":
    main()
