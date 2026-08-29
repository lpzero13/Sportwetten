from __future__ import annotations

import json
from pathlib import Path

from models.market import EventDetails
from models.snapshot import Snapshot
from storage.database import Database
from storage.repositories import MarketRepository
from tipico.parser import parse_event_details


FIXTURES = Path(__file__).parent / "fixtures"


def load_details() -> EventDetails:
    payload = json.loads((FIXTURES / "event_detail.json").read_text(encoding="utf-8"))
    return parse_event_details(payload)


def test_snapshot_schema_links_market_presence_and_odds_history(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "tipico.db")
    details = load_details()
    snapshot = Snapshot(
        event_id=details.event.event_id,
        observed_at="2026-08-29T10:00:00+00:00",
        snapshot_type="HALFTIME",
        trigger_reason="HT_PHASE_1",
        match_status=details.event.status,
        display_time="HZ",
        score_home=1,
        score_away=1,
        ht_score_home=1,
        ht_score_away=1,
        market_count=details.market_count,
        outcome_count=details.outcome_count,
        open_outcome_count=details.open_outcome_count,
        paused_outcome_count=details.paused_outcome_count,
        snapshot_quality="COMPLETE",
    )
    snapshot_id = database.create_snapshot(snapshot)
    repository = MarketRepository(database)
    repository.save_details(details, snapshot.observed_at, snapshot_id=snapshot_id)
    for market in details.markets:
        database.add_market_presence(
            event_id=details.event.event_id,
            market_id=market.market_id,
            snapshot_id=snapshot_id,
            observed_at=snapshot.observed_at,
            market_type=market.type,
            fixed_param=market.fixed_param,
            market_status=market.status,
        )

    assert database.count_rows("competitions") == 1
    assert database.count_rows("snapshots") == 1
    assert database.count_rows("market_presence") == details.market_count
    history = database.connection.execute(
        "SELECT DISTINCT snapshot_id FROM odds_history"
    ).fetchall()
    assert {row["snapshot_id"] for row in history} == {snapshot_id}
    assert database.snapshots_for_event(details.event.event_id)[0]["snapshot_id"] == snapshot_id
    database.close()


def test_competition_event_count_is_distinct(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "tipico.db")
    details = load_details()
    repository = MarketRepository(database)
    repository.save_details(details, "2026-08-29T10:00:00+00:00")
    repository.save_details(details, "2026-08-29T10:00:05+00:00")

    row = database.connection.execute(
        "SELECT competition_id, events_observed FROM competitions"
    ).fetchone()
    assert row["competition_id"] == "104124301"
    assert row["events_observed"] == 1
    database.close()
