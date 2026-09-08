"""Compare real result recovery on a SQLite backup; never modifies the source."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import Settings
from services.result_backfill import ResultBackfillRunner, _event_from_row
from fotmob.matching import MatchIdentity
from fotmob.models import FotMobFetchResult
from fotmob.parser import parse_fotmob_payload
from storage.database import Database


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


class CaptureClient:
    def __init__(self, client, directory, replay=None):
        self.client, self.directory = client, directory
        self.replay, self.replayed = replay, 0
        directory.mkdir()

    def __getattr__(self, key):
        return getattr(self.client, key)

    def capture(self, key, fetched):
        write(self.directory / (hashlib.sha256(key.encode()).hexdigest() + ".json"), {
            "endpoint": fetched.endpoint or key, "fetched_at": datetime.now(timezone.utc).isoformat(),
            "success": fetched.success, "status_code": fetched.status_code,
            "error": fetched.error, "payload": fetched.payload})
        return fetched

    def fetch_json(self, endpoint):
        cached = self.cached(endpoint)
        if cached is not None:
            return cached
        return self.capture(endpoint, self.client.fetch_json(endpoint))

    def fetch_match_details(self, match_id):
        cached = self.cached("detail:" + match_id, match_id)
        if cached is not None:
            return cached
        return self.capture("detail:" + match_id, self.client.fetch_match_details(match_id))

    def cached(self, key, match_id=None):
        if self.replay is None:
            return None
        path = self.replay / (hashlib.sha256(key.encode()).hexdigest() + ".json")
        if not path.exists():
            return None
        record = json.loads(path.read_text(encoding="utf-8"))
        if not record["success"]:
            return None
        self.replayed += 1
        # Preserve the original retrieval timestamp when replaying evidence.
        write(self.directory / path.name, record)
        return FotMobFetchResult(success=True, payload=record["payload"], status_code=record["status_code"],
            endpoint=record["endpoint"], match=parse_fotmob_payload(record["payload"], provider_match_id=match_id) if match_id else None)


def inventory(connection):
    return {str(row["event_id"]): dict(row) for row in connection.execute("""
        SELECT e.event_id, e.home_team, e.away_team, e.kickoff_time,
               e.competition_country, e.competition_name,
               r.ft_home, r.ft_away, r.ht_home, r.ht_away,
               COALESCE(r.result_use_ft, 0) AS ft_usable,
               COALESCE(r.result_use_h2, 0) AS h2_usable,
               r.result_source, r.result_evidence_id
        FROM events e LEFT JOIN match_results r ON r.event_id=e.event_id
        WHERE lower(e.sport)='soccer'""")}


def counts(rows, day=None):
    values = list(rows.values())
    if day:
        values = [r for r in values if r["kickoff_time"] and datetime.fromisoformat(
            r["kickoff_time"].replace("Z", "+00:00")).astimezone(ZoneInfo("Europe/Berlin")).date().isoformat() == day]
    return {"events": len(values), "ft_usable": sum(r["ft_usable"] for r in values),
            "h2_usable": sum(r["h2_usable"] for r in values)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New directory only")
    parser.add_argument("--replay", type=Path, help="Reuse captured responses; fetch missing ones normally")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    source = sqlite3.connect(args.source.resolve().as_uri() + "?mode=ro", uri=True)
    target_path = args.output / "tipico-canary.db"
    target = sqlite3.connect(target_path)
    source.backup(target)
    source.close()
    target.close()
    db = Database(target_path)
    try:
        settings = Settings.from_env(Path(__file__).resolve().parents[1])
        runner = ResultBackfillRunner(settings, db)
        runner.client = CaptureClient(runner.client, args.output / "responses", args.replay)
        before = inventory(db.connection)
        write(args.output / "before.json", before)
        print(json.dumps({"phase": "start", "before": counts(before), "network": runner._network_allowed("worker")}), flush=True)
        started = time.monotonic()
        result = runner.run(apply=True, workers=10, limit=len(before), mode="worker",
                            refresh_index=True, event_ids=list(before))
        elapsed = round(time.monotonic() - started, 2)
        write(args.output / "run.json", result)
        after = inventory(db.connection)
        write(args.output / "after.json", after)
        gained = [r for key, r in after.items() if r["ft_usable"] > before[key]["ft_usable"] or r["h2_usable"] > before[key]["h2_usable"]]
        write(args.output / "gained.json", gained)
        # Explain the strongest rejected candidate, without relaxing acceptance.
        events = [_event_from_row(row) for row in db.connection.execute(
            "SELECT e.*, 0 AS backfill_attempt_count FROM events e LEFT JOIN match_results r ON r.event_id=e.event_id WHERE lower(e.sport)='soccer' AND COALESCE(r.result_use_ft,0)=0")]
        runner._provider_days = []
        catalog = runner._catalog(events, allow_network=False, persist=False)
        unresolved = []
        for event in events:
            matched = runner.matcher.match(MatchIdentity.from_tipico_event(event), runner._candidate_matches(event, catalog))
            unresolved.append({"event_id": event.event_id, "home": event.home_team, "away": event.away_team,
                               "league": event.competition_name, "country": event.competition_country,
                               "kickoff": event.kickoff_time, "status": matched.status, "reasons": matched.reasons,
                               "candidates": [{"id": c.provider_match_id, "score": c.score, "reasons": c.reasons} for c in matched.candidates]})
        write(args.output / "unresolved.json", unresolved)
        days = sorted({datetime.fromisoformat(r["kickoff_time"].replace("Z", "+00:00")).astimezone(ZoneInfo("Europe/Berlin")).date().isoformat() for r in before.values() if r["kickoff_time"]})
        summary = {"source": str(args.source.resolve()), "copy": str(target_path.resolve()), "source_modified": False,
                   "elapsed_seconds": elapsed, "before": counts(before), "after": counts(after),
                   "day_2026_09_05_before": counts(before, "2026-09-05"), "day_2026_09_05_after": counts(after, "2026-09-05"),
                   "status": result["status"], "counts": result.get("counts"), "gained_events": len(gained),
                   "by_day": {day: {"before": counts(before, day), "after": counts(after, day)} for day in days},
                   "replayed_responses": runner.client.replayed,
                   "provider_requests": result.get("client_metrics"), "quick_check": db.connection.execute("PRAGMA quick_check").fetchone()[0]}
        write(args.output / "summary.json", summary)
        print(json.dumps(summary, ensure_ascii=True), flush=True)
    finally:
        db.close()


if __name__ == "__main__":
    main()
