from __future__ import annotations

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import Settings
from services.collector import Collector
from storage.database import Database
from storage.raw_storage import RawStorage
from tipico.client import ApiResponse, RequestMetrics, TipicoApiError


FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def response(endpoint: str, payload: dict) -> ApiResponse:
    return ApiResponse(
        payload=payload,
        metrics=RequestMetrics(
            endpoint=endpoint,
            method="GET",
            request_started_at="2026-08-29T10:00:00+00:00",
            response_received_at="2026-08-29T10:00:01+00:00",
            response_time_ms=25,
            status_code=200,
            payload_size=len(json.dumps(payload)),
        ),
    )


class FakeCollectorClient:
    def __init__(
        self,
        live_payload: dict,
        detail_payload: dict,
        upcoming_payload: dict | None = None,
    ) -> None:
        self.live_payload = live_payload
        self.detail_payload = detail_payload
        self.upcoming_payload = upcoming_payload

    def get_live_football_events(self) -> ApiResponse:
        return response("https://tipico.test/live", self.live_payload)

    def get_event_details(self, event_id: str) -> ApiResponse:
        return response(f"https://tipico.test/events/{event_id}", self.detail_payload)

    def get_upcoming_football_events(
        self,
        upcoming_time: str = "today",
        *,
        max_markets: int = 1,
    ) -> ApiResponse:
        return response("https://tipico.test/upcoming", self.upcoming_payload or {})


def test_collector_run_once_persists_snapshot_and_metrics(tmp_path: Path) -> None:
    live = load_fixture("live_feed.json")
    detail = load_fixture("event_detail.json")
    settings = Settings(
        root_dir=tmp_path,
        store_raw_responses=False,
        collector_retry_delays_seconds=(),
        collector_halftime_delays_seconds=(),
    )
    database = Database(settings.database_path)
    feed_client = FakeCollectorClient(live, detail)
    collector = Collector(
        feed_client,  # type: ignore[arg-type]
        database,
        RawStorage(settings.raw_storage_path, enabled=False),
        settings,
        client_factory=lambda: feed_client,  # type: ignore[arg-type]
    )

    status = collector.run_once()

    assert status["status"] == "COMPLETED"
    assert status["feed"]["requests"] == 1
    assert status["detail"]["requests"] == 1
    assert status["snapshot_counts"]["LIVE_PERIODIC"] == 1
    assert database.count_rows("snapshots") == 1
    assert database.count_rows("market_presence") == 3
    history = database.connection.execute("SELECT snapshot_id FROM odds_history").fetchall()
    assert len(history) == 8
    assert {row["snapshot_id"] for row in history} == {1}
    database.close()


def test_collector_enqueues_goal_and_all_halftime_phases(tmp_path: Path) -> None:
    live = load_fixture("live_feed.json")
    detail = load_fixture("event_detail.json")
    halftime_live = copy.deepcopy(live)
    halftime_live["LIVE"]["events"]["721621110"]["date"] = "HZ"
    halftime_live["LIVE"]["scores"]["721621110"]["currentScore"] = ["2", "1"]
    halftime_detail = copy.deepcopy(detail)
    halftime_detail["event"]["date"] = "HZ"
    halftime_detail["event"]["eventScores"]["currentScore"] = ["2", "1"]
    halftime_detail["event"]["eventScores"]["htScore"] = ["1", "1"]
    settings = Settings(
        root_dir=tmp_path,
        store_raw_responses=False,
        collector_retry_delays_seconds=(),
        collector_halftime_delays_seconds=(0, 0, 0),
    )
    database = Database(settings.database_path)
    client = FakeCollectorClient(live, detail)
    collector = Collector(
        client,  # type: ignore[arg-type]
        database,
        RawStorage(settings.raw_storage_path, enabled=False),
        settings,
        client_factory=lambda: client,  # type: ignore[arg-type]
    )
    collector._executor = ThreadPoolExecutor(max_workers=3)
    collector._running = True
    try:
        collector._poll_feed()
        collector._drain_due_jobs()
        while collector._futures:
            collector._collect_finished(block=True)
            collector._drain_due_jobs()

        client.live_payload = halftime_live
        client.detail_payload = halftime_detail
        collector._poll_feed()
        queued_types = [job.snapshot_type for _, _, job in collector._queue]
        assert "EVENT_TRIGGERED" in queued_types
        assert queued_types.count("HALFTIME") == 3

        while collector._queue or collector._futures:
            collector._drain_due_jobs()
            if collector._futures:
                collector._collect_finished(block=True)
        assert collector.snapshot_counts["EVENT_TRIGGERED"] == 1
        assert collector.snapshot_counts["HALFTIME"] == 3
        assert database.count_rows("snapshots") == 5
    finally:
        collector._running = False
        collector._executor.shutdown(wait=True)
        collector._executor = None
        database.close()


def test_prematch_scheduler_queues_only_future_targets(tmp_path: Path) -> None:
    kickoff_ms = int(
        (datetime.now(timezone.utc) + timedelta(minutes=70)).timestamp() * 1000
    )
    upcoming = {
        "UPCOMING": {
            "sportCompetitionMap": {
                "soccer": [
                    {
                        "groupId": 321,
                        "groupIdString": "321",
                        "name": "Testliga",
                        "parentName": "Testland",
                    }
                ]
            },
            "events": {
                "9001": {
                    "id": "9001",
                    "status": "pre_match",
                    "team1": "Heim",
                    "team2": "Gast",
                    "competitionId": 321,
                    "eventStartTime": kickoff_ms,
                    "date": "Heute",
                }
            },
            "eventsBySport": {"soccer": ["9001"]},
            "scores": {},
        }
    }
    settings = Settings(
        root_dir=tmp_path,
        store_raw_responses=False,
        collector_retry_delays_seconds=(),
    )
    database = Database(settings.database_path)
    client = FakeCollectorClient({}, {}, upcoming)
    collector = Collector(
        client,  # type: ignore[arg-type]
        database,
        RawStorage(settings.raw_storage_path, enabled=False),
        settings,
        client_factory=lambda: client,  # type: ignore[arg-type]
    )

    collector._poll_prematch()

    assert {
        job.trigger_reason for _, _, job in collector._queue
    } == {"T_MINUS_60", "T_MINUS_15", "T_MINUS_5", "T_MINUS_1"}
    assert database.count_rows("events") == 1
    competition = database.connection.execute(
        "SELECT country_or_region FROM competitions WHERE competition_id = '321'"
    ).fetchone()
    assert competition["country_or_region"] == "Testland"
    database.close()


def test_collector_retries_temporary_detail_error(tmp_path: Path) -> None:
    live = load_fixture("live_feed.json")
    detail = load_fixture("event_detail.json")

    class FlakyClient(FakeCollectorClient):
        def __init__(self) -> None:
            super().__init__(live, detail)
            self.detail_calls = 0

        def get_event_details(self, event_id: str) -> ApiResponse:
            self.detail_calls += 1
            if self.detail_calls == 1:
                metrics = response("https://tipico.test/fail", detail).metrics
                raise TipicoApiError("temporary", metrics=metrics, status_code=503)
            return super().get_event_details(event_id)

    settings = Settings(
        root_dir=tmp_path,
        store_raw_responses=False,
        collector_retry_delays_seconds=(0,),
        collector_halftime_delays_seconds=(),
    )
    database = Database(settings.database_path)
    client = FlakyClient()
    collector = Collector(
        client,  # type: ignore[arg-type]
        database,
        RawStorage(settings.raw_storage_path, enabled=False),
        settings,
        client_factory=lambda: client,  # type: ignore[arg-type]
    )

    status = collector.run_once()

    assert client.detail_calls == 2
    assert status["retries"] == 1
    assert status["detail"]["requests"] == 2
    assert status["detail"]["errors"] == 1
    assert database.count_rows("snapshots") == 1
    database.close()
