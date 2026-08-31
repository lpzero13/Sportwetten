from __future__ import annotations

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from config import Settings
from intelligence.service import MarketIntelligenceService
from models.snapshot import Snapshot
from services.collector import Collector
from storage.database import Database
from storage.parquet_archive import ParquetArchive, build_snapshot_payload
from storage.raw_storage import RawStorage
from storage.repositories import EventRepository
from tipico.parser import parse_event_details, parse_live_feed

from tests.test_collector import FakeCollectorClient, load_fixture


def test_one_hundred_current_refreshes_do_not_create_history(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "tipico.db")
    event = parse_live_feed(load_fixture("live_feed.json"))[0]
    repository = EventRepository(database)

    for index in range(100):
        event.display_minute = f"{50 + index}'"
        repository.save_observation(event, f"2026-08-29T10:{index // 60:02d}:{index % 60:02d}+00:00")

    assert database.count_rows("current_event_state") == 1
    assert database.count_rows("event_states") == 0
    assert database.count_rows("snapshots") == 0
    assert database.count_rows("snapshot_outbox") == 0
    database.close()


def test_snapshot_slot_is_idempotent_and_exports_to_parquet(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "tipico.db")
    details = parse_event_details(load_fixture("event_detail.json"))
    settings = Settings(root_dir=tmp_path)
    analysis = MarketIntelligenceService(database, settings).analyze(
        details,
        observed_at="2026-08-29T10:00:00+00:00",
        persist=False,
    )
    snapshot = Snapshot(
        event_id=details.event.event_id,
        observed_at="2026-08-29T10:00:00+00:00",
        snapshot_type="HALFTIME",
        trigger_reason="FIRST_HALF_TO_HALF_TIME",
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
        competition_id=details.event.competition_id,
        competition_name=details.event.competition_name,
        competition_country=details.event.competition_country,
        home_team=details.event.home_team,
        away_team=details.event.away_team,
        kickoff_time=details.event.kickoff_time,
    )
    payload = build_snapshot_payload(details, analysis, snapshot)

    first_id, first_created = database.enqueue_historical_snapshot(snapshot, payload)
    second_id, second_created = database.enqueue_historical_snapshot(snapshot, payload)

    assert first_created is True
    assert second_created is False
    assert second_id == first_id
    assert database.count_rows("snapshots") == 1
    assert database.count_rows("snapshot_outbox") == 1

    archive = ParquetArchive(tmp_path / "archive")
    export = archive.export_pending(database, batch_size=100)
    assert export["errors"] == 0
    assert export["snapshots_exported"] == 1
    assert database.count_rows("snapshot_outbox") == 0
    files = list((tmp_path / "archive").rglob("*.parquet"))
    assert len(files) == 2
    import pyarrow.parquet as pq

    snapshot_file = next(path for path in files if "snapshots" in path.parts)
    strategy_file = next(path for path in files if "strategy" in path.parts)
    rows = pq.read_table(snapshot_file).to_pylist()
    assert len(rows) == 1
    assert rows[0]["snapshot_id"] == first_id
    assert rows[0]["schema_version"] == "tipico_snapshot_v1"
    assert rows[0]["event_id"] == details.event.event_id
    assert rows[0]["q_zero_best"] == payload["q_zero_best"]
    strategy_rows = pq.read_table(strategy_file).to_pylist()
    assert strategy_rows[0]["tipico_event_id"] == details.event.event_id
    assert strategy_rows[0]["market_p1"] == payload["p1_market"]
    assert strategy_rows[0]["p1_buffer"] == payload["p1_buffer"]
    database.close()


def test_persisted_analysis_refreshes_current_state_without_history_growth(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "tipico.db")
    details = parse_event_details(load_fixture("event_detail.json"))
    settings = Settings(root_dir=tmp_path)
    service = MarketIntelligenceService(database, settings)

    service.analyze(details, observed_at="2026-08-29T10:00:00+00:00", persist=True)
    first_strategy_rows = database.count_rows("strategy_evaluations")
    first_current_rows = database.count_rows("current_canonical_outcomes")
    service.analyze(details, observed_at="2026-08-29T10:00:05+00:00", persist=True)

    assert database.count_rows("canonical_outcomes") == 0
    assert database.count_rows("strategy_evaluations") == first_strategy_rows
    assert database.count_rows("current_canonical_outcomes") == first_current_rows
    assert database.count_rows("current_strategy_evaluations") == 1
    database.close()


def test_collector_queues_each_minute_slot_only_once(tmp_path: Path) -> None:
    base = load_fixture("live_feed.json")
    detail = load_fixture("event_detail.json")
    settings = Settings(
        root_dir=tmp_path,
        store_raw_responses=False,
        collector_retry_delays_seconds=(),
    )
    database = Database(settings.database_path)
    client = FakeCollectorClient(base, detail)
    collector = Collector(
        client, database, RawStorage(settings.raw_storage_path, enabled=False), settings,
        client_factory=lambda: client,
    )

    collector._poll_feed()
    event = client.live_payload["LIVE"]["events"]["721621110"]
    queued: list[str] = []
    for minute in (61, 62, 71, 72, 81, 86, 91, 92):
        event["date"] = f"{minute}'"
        collector._poll_feed()
        queued.extend(job.snapshot_type for _, _, job in collector._queue)

    queued_now = [job.snapshot_type for _, _, job in collector._queue]
    assert queued_now == [
        "MINUTE_60", "MINUTE_70", "MINUTE_80", "MINUTE_85", "MINUTE_90",
    ]
    # The in-memory trigger guard prevents a second queued job after a due job
    # has been drained and persisted.
    collector._executor = ThreadPoolExecutor(max_workers=3)
    collector._running = True
    try:
        collector._drain_due_jobs()
        while collector._futures:
            collector._collect_finished(block=True)
            collector._drain_due_jobs()
        event["date"] = "92'"
        collector._poll_feed()
        assert [job.snapshot_type for _, _, job in collector._queue] == []
    finally:
        collector._running = False
        collector._executor.shutdown(wait=True)
        collector._executor = None
        database.close()


def test_final_snapshot_writes_explicit_match_result(tmp_path: Path) -> None:
    feed = load_fixture("live_feed.json")
    detail = load_fixture("event_detail.json")
    feed["LIVE"]["events"]["721621110"].update(
        {"status": "finished", "type": "finished", "date": "FT"}
    )
    feed["LIVE"]["scores"]["721621110"] = {
        "currentScore": ["3", "1"],
        "htScore": ["1", "0"],
    }
    detail["event"].update(
        {"status": "finished", "eventState": "finished", "date": "FT"}
    )
    detail["event"]["eventScores"] = {
        "currentScore": ["3", "1"],
        "htScore": ["1", "0"],
    }
    settings = Settings(
        root_dir=tmp_path,
        store_raw_responses=False,
        collector_retry_delays_seconds=(),
    )
    database = Database(settings.database_path)
    client = FakeCollectorClient(feed, detail)
    collector = Collector(
        client, database, RawStorage(settings.raw_storage_path, enabled=False), settings,
        client_factory=lambda: client,
    )

    collector.run_once()

    final = database.latest_snapshot_for_event("721621110", snapshot_type="FINAL")
    result = database.match_result_for_event("721621110")
    assert final is not None
    assert result is not None
    assert result["ht_home"] == 1
    assert result["ht_away"] == 0
    assert result["ft_home"] == 3
    assert result["ft_away"] == 1
    assert result["first_half_goals"] == 1
    assert result["second_half_goals"] == 3
    assert result["second_half_goal_class"] == "2_PLUS"
    assert database.count_rows("snapshots") == 1
    database.close()
