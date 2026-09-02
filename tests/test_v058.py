from __future__ import annotations

import copy
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from config import Settings
from intelligence.models import CanonicalOutcome
from models.event import LiveEvent
from services.collector import Collector
from services.event_service import EventService
from services.live_universe import (
    FOTMOB_DISCOVERY,
    FOTMOB_FULL,
    LiveUniverse,
    P1_STRATEGY_ELIGIBLE,
    P2_DISCOVERY,
    P3_MINIMAL,
    P4_IGNORE,
    P0_SELECTED,
)
from storage.database import Database, NO_LONGER_LIVE_STATUS, state_from_event
from storage.raw_storage import RawStorage
from tipico.parser import parse_live_feed

from tests.test_collector import FakeCollectorClient, load_fixture, response


FIXTURE_EVENT_ID = "721621110"


def event_from_fixture() -> LiveEvent:
    return parse_live_feed(load_fixture("live_feed.json"))[0]


def two_event_feed() -> dict:
    payload = copy.deepcopy(load_fixture("live_feed.json"))
    base = payload["LIVE"]["events"][FIXTURE_EVENT_ID]
    second = copy.deepcopy(base)
    second.update(
        {
            "id": "second-event",
            "eventName": "C - D",
            "team1": "C",
            "team2": "D",
        }
    )
    payload["LIVE"]["events"]["second-event"] = second
    payload["LIVE"]["scores"]["second-event"] = {
        "currentScore": ["0", "0"],
        "htScore": ["0", "0"],
    }
    payload["LIVE"]["eventsBySport"]["soccer"].append("second-event")
    payload["LIVE"]["competitionEventMap"]["104124301"].append("second-event")
    return payload


def feed_without_first_event() -> dict:
    payload = two_event_feed()
    del payload["LIVE"]["events"][FIXTURE_EVENT_ID]
    del payload["LIVE"]["scores"][FIXTURE_EVENT_ID]
    payload["LIVE"]["eventsBySport"]["soccer"] = ["second-event"]
    payload["LIVE"]["competitionEventMap"]["104124301"] = ["second-event"]
    return payload


def open_current_quote(database: Database, event_id: str) -> None:
    database.replace_current_canonical_outcomes(
        [
            CanonicalOutcome(
                event_id=event_id,
                market_id="market-1",
                outcome_id="outcome-1",
                canonical_type="ZERO_REMAINING_GOALS",
                scope="MATCH",
                period="FULL_MATCH",
                side="UNDER",
                line=0.5,
                team=None,
                odds=2.5,
                status="open",
                available=True,
                observed_at="2026-08-29T10:00:00+00:00",
                raw_market_type="points-more-less-rest",
                raw_market_caption="Resttore",
                raw_fixed_param="0.5",
                raw_choice_param="under",
                raw_outcome_caption="Unter 0,5",
            )
        ]
    )


def test_fresh_service_reconciles_missing_event_and_closes_only_current_odds(tmp_path: Path) -> None:
    settings = Settings(root_dir=tmp_path, store_raw_responses=False)
    database = Database(settings.database_path)
    first_client = FakeCollectorClient(two_event_feed(), {})
    first = EventService(
        first_client, database, RawStorage(settings.raw_storage_path, enabled=False), settings
    )
    assert first.refresh().success is True
    open_current_quote(database, FIXTURE_EVENT_ID)

    # A new service has no in-memory previous feed and must use the persisted
    # active IDs exactly once during its first valid poll.
    second_client = FakeCollectorClient(feed_without_first_event(), {})
    second = EventService(
        second_client, database, RawStorage(settings.raw_storage_path, enabled=False), settings
    )
    result = second.refresh()

    assert result.success is True
    assert second._startup_reconciliation_done is True
    current = database.current_event_state(FIXTURE_EVENT_ID)
    assert current is not None
    assert current["status"] == NO_LONGER_LIVE_STATUS
    assert current["score_home"] == 1
    assert current["ht_score_home"] == 1
    event_row = database.event_info(FIXTURE_EVENT_ID)
    assert event_row is not None
    assert event_row["status"] == NO_LONGER_LIVE_STATUS
    assert database.connection.execute(
        "SELECT COUNT(*) AS n FROM event_states WHERE event_id = ?",
        (FIXTURE_EVENT_ID,),
    ).fetchone()["n"] == 2
    odds = database.connection.execute(
        "SELECT status, available, odds FROM current_canonical_outcomes WHERE event_id = ?",
        (FIXTURE_EVENT_ID,),
    ).fetchone()
    assert tuple(odds) == ("stopped", 0, None)
    database.close()


def test_invalid_empty_feed_does_not_reconcile_and_next_valid_poll_retries(tmp_path: Path) -> None:
    settings = Settings(root_dir=tmp_path, store_raw_responses=False)
    database = Database(settings.database_path)
    seed_client = FakeCollectorClient(load_fixture("live_feed.json"), {})
    seed = EventService(
        seed_client, database, RawStorage(settings.raw_storage_path, enabled=False), settings
    )
    assert seed.refresh().success is True

    malformed = {
        "LIVE": {"events": {}, "eventsBySport": {"soccer": []}, "scores": {}}
    }
    client = FakeCollectorClient(malformed, {})
    service = EventService(
        client, database, RawStorage(settings.raw_storage_path, enabled=False), settings
    )
    rejected = service.refresh()
    assert rejected.success is False
    assert service._startup_reconciliation_done is False
    assert database.current_event_state(FIXTURE_EVENT_ID)["status"] == "running"

    client.live_payload = feed_without_first_event()
    accepted = service.refresh()
    assert accepted.success is True
    assert service._startup_reconciliation_done is True
    assert database.current_event_state(FIXTURE_EVENT_ID)["status"] == NO_LONGER_LIVE_STATUS
    database.close()


def test_feed_batch_rolls_back_every_event_on_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database = Database(tmp_path / "data" / "tipico.db")
    first = event_from_fixture()
    second = replace(first, event_id="second", home_team="C", away_team="D")
    original = database._upsert_current_event_state_locked

    def fail_on_second(state: object) -> bool:
        if getattr(state, "event_id", None) == "second":
            raise RuntimeError("injected batch failure")
        return original(state)  # type: ignore[arg-type]

    monkeypatch.setattr(database, "_upsert_current_event_state_locked", fail_on_second)
    with pytest.raises(RuntimeError, match="injected batch failure"):
        database.persist_live_feed_batch(
            [first, second],
            "2026-08-29T10:00:00+00:00",
        )

    for table in ("events", "event_states", "current_event_state"):
        assert database.count_rows(table) == 0
    database.close()


def test_terminal_close_and_public_reconciliation_are_idempotent(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "tipico.db")
    event = event_from_fixture()
    database.persist_live_feed_batch([event], "2026-08-29T10:00:00+00:00")
    open_current_quote(database, event.event_id)

    first = database.mark_event_no_longer_live(event.event_id, "2026-08-29T10:05:00+00:00")
    history_after_first = database.count_rows("event_states")
    second = database.mark_event_no_longer_live(event.event_id, "2026-08-29T10:06:00+00:00")
    assert first is True
    assert second is False
    assert database.count_rows("event_states") == history_after_first
    odds = database.connection.execute(
        "SELECT status, available, odds FROM current_canonical_outcomes WHERE event_id = ?",
        (event.event_id,),
    ).fetchone()
    assert tuple(odds) == ("stopped", 0, None)
    assert database.count_rows("odds_history") == 0
    database.close()


def test_missing_events_row_is_reconstructed_and_finished_is_not_reopened(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "tipico.db")
    event = event_from_fixture()
    state = state_from_event(event, "2026-08-29T10:00:00+00:00")
    database.upsert_current_event_state(state)
    assert database.count_rows("events") == 0

    assert database.mark_event_no_longer_live(event.event_id, "2026-08-29T10:01:00+00:00") is True
    assert database.event_info(event.event_id) is not None

    finished = replace(
        event,
        event_id="finished-event",
        status="finished",
        period="FINISHED",
        display_minute="FT",
    )
    database.persist_live_feed_batch([finished], "2026-08-29T10:02:00+00:00")
    running = replace(finished, status="running", period="RUNNING", display_minute="20'")
    result = database.persist_live_feed_batch([running], "2026-08-29T10:03:00+00:00")
    assert finished.event_id in result["ignored_event_ids"]
    assert database.current_event_state(finished.event_id)["status"] == "finished"
    database.close()


def test_reconciliation_handles_events_row_without_current_state(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "tipico.db")
    event = event_from_fixture()
    database.upsert_event(event, "2026-08-29T10:00:00+00:00")

    assert database.mark_event_no_longer_live(event.event_id, "2026-08-29T10:01:00+00:00") is True
    current = database.current_event_state(event.event_id)
    assert current is not None
    assert current["status"] == NO_LONGER_LIVE_STATUS
    assert database.event_info(event.event_id)["status"] == NO_LONGER_LIVE_STATUS
    database.close()


def test_stale_prematch_cutoff_no_reopen_and_future_reschedule(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "tipico.db")
    base = event_from_fixture()
    now = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)

    almost_stale = replace(
        base,
        status="pre_match",
        period="PRE_MATCH",
        display_minute="—",
        kickoff_time=(now - timedelta(hours=5, minutes=59)).isoformat(),
    )
    database.persist_live_feed_batch([almost_stale], almost_stale.kickoff_time, now=now)
    assert database.current_event_state(base.event_id)["status"] == "pre_match"

    stale = replace(
        almost_stale,
        kickoff_time=(now - timedelta(hours=6)).isoformat(),
    )
    database.persist_live_feed_batch([stale], now.isoformat(), now=now)
    assert database.current_event_state(base.event_id)["status"] == NO_LONGER_LIVE_STATUS

    # The stale pre-match payload is ignored and cannot oscillate the state.
    old_again = database.persist_live_feed_batch([stale], now.isoformat(), now=now)
    assert base.event_id in old_again["ignored_event_ids"]
    assert database.current_event_state(base.event_id)["status"] == NO_LONGER_LIVE_STATUS

    future = replace(
        stale,
        kickoff_time=(now + timedelta(hours=2)).isoformat(),
    )
    database.persist_live_feed_batch([future], now.isoformat(), now=now)
    assert database.current_event_state(base.event_id)["status"] == "pre_match"

    no_kickoff = replace(future, kickoff_time=None)
    database.persist_live_feed_batch([no_kickoff], now.isoformat(), now=now)
    assert database.current_event_state(base.event_id)["status"] == "pre_match"
    database.close()


def test_collection_metrics_cache_and_force_refresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(
        root_dir=tmp_path,
        smart_universe_enabled=False,
        store_raw_responses=False,
        collection_metrics_cache_ttl_seconds=30,
    )
    database = Database(settings.database_path)
    client = FakeCollectorClient({}, {})
    collector = Collector(
        client, database, RawStorage(settings.raw_storage_path, enabled=False), settings
    )
    calls: list[str | None] = []

    def fake_metrics(date_text: str | None = None) -> dict[str, object]:
        calls.append(date_text)
        return {"date": date_text or "2026-08-29", "calls": len(calls)}

    monkeypatch.setattr(database, "collection_metrics_for_date", fake_metrics)
    assert collector.status()["coverage"]["calls"] == 1
    cached = collector.status()
    assert cached["coverage"]["calls"] == 1
    assert cached["collection_metrics_cached"] is True
    forced = collector.status(force_refresh=True)
    assert forced["coverage"]["calls"] == 2
    assert forced["collection_metrics_cached"] is False
    assert len(calls) == 2
    database.close()


def test_queue_diagnostics_separate_due_and_future_jobs(tmp_path: Path) -> None:
    settings = Settings(root_dir=tmp_path, smart_universe_enabled=False, store_raw_responses=False)
    database = Database(settings.database_path)
    client = FakeCollectorClient({}, {})
    collector = Collector(
        client, database, RawStorage(settings.raw_storage_path, enabled=False), settings
    )
    assert collector._enqueue(
        "future-event", "MINUTE_60", "future", delay_seconds=60, raw_full=False
    ) is True
    assert collector._enqueue(
        "due-event", "HALFTIME", "due", delay_seconds=0, raw_full=False
    ) is True
    status = collector.status()
    assert status["queue_depth"] == 2
    assert status["queue_due"] == 1
    assert status["queue_future"] == 1
    assert status["oldest_due_age_seconds"] >= 0
    assert status["queue_by_snapshot_type"] == {"HALFTIME": 1, "MINUTE_60": 1}
    database.close()


def test_live_universe_derives_full_coverage_and_priorities(tmp_path: Path) -> None:
    settings = Settings(
        root_dir=tmp_path,
        fotmob_coverage_min_sample_size=5,
        fotmob_coverage_full_ratio=0.90,
        tipico_market_capability_min_sample_size=5,
        tipico_market_capability_min_ratio=0.50,
    )
    database = Database(settings.database_path)
    now = "2026-08-29T10:00:00+00:00"
    database.connection.execute(
        """
        INSERT INTO competition_provider_links (
            internal_competition_id, provider, provider_competition_id,
            tipico_competition_name, tipico_country, created_at
        ) VALUES ('tipico-1', 'FOTMOB', 'fm-1', 'Senior League', 'Testland', ?)
        """,
        (now,),
    )
    database.connection.commit()
    for index in range(5):
        database.connection.execute(
            """
            INSERT INTO fotmob_match_index (
                fotmob_match_id, provider, league_id, season_id, season_label,
                league_name, country, home_team_name, away_team_name,
                first_seen_at, last_seen_at, detail_status, last_checked_at
            ) VALUES (?, 'FOTMOB', 'fm-1', 'season-1', '2025/26',
                      'Senior League', 'Testland', ?, ?, ?, ?, 'FETCHED', ?)
            """,
            (
                f"fm-match-{index}",
                f"Home {index}",
                f"Away {index}",
                now,
                now,
                now,
            ),
        )
        database.connection.execute(
            """
            INSERT INTO snapshots (
                event_id, observed_at, snapshot_type, snapshot_quality,
                competition_id, competition_name, competition_country,
                q_zero_best, q_two_plus_best
            ) VALUES (?, ?, 'HALFTIME', 'COMPLETE', 'tipico-1',
                      'Senior League', 'Testland', 2.0, 3.0)
            """,
            (f"tipico-match-{index}", now),
        )
    database.connection.commit()

    universe = LiveUniverse(database, settings)
    normal = replace(
        event_from_fixture(),
        event_id="live-1",
        competition_id="tipico-1",
        competition_name="Senior League",
        competition_country="Testland",
    )
    decision = universe.decide(normal)
    assert decision.priority == P1_STRATEGY_ELIGIBLE
    assert decision.fotmob_status == FOTMOB_FULL
    assert decision.eligible_for_strategy is True
    assert database.count_rows("fotmob_coverage_catalog") == 1
    assert database.count_rows("tipico_market_capability") == 1
    new_season = replace(normal, raw_data={"season_id": "season-2"})
    assert universe.decide(new_season).priority == P2_DISCOVERY

    unknown = replace(normal, competition_id="unknown", competition_name="New League")
    assert universe.decide(unknown).priority == P2_DISCOVERY
    youth = replace(normal, competition_name="National U19 League")
    assert universe.decide(youth).priority == P4_IGNORE
    assert universe.decide(youth, selected_event_id=youth.event_id).priority == P0_SELECTED

    no_data = replace(normal, competition_id="no-data", competition_name="No Data League")
    database.upsert_fotmob_coverage_catalog_rows(
        [
            {
                "fotmob_league_id": "no-data",
                "season_id": "season-1",
                "season_label": "2025/26",
                "league_name": "No Data League",
                "country": "Testland",
                "observed_matches": 10,
                "detailed_matches": 0,
                "sample_size": 10,
                "coverage_ratio": 0.0,
                "last_checked": now,
                "status": "NO_DATA",
            }
        ]
    )
    database.upsert_tipico_market_capability_rows(
        [
            {
                "competition_id": "no-data",
                "competition_name": "No Data League",
                "competition_country": "Testland",
                "observed_matches": 10,
                "matches_with_strategy_markets": 0,
                "coverage_ratio": 0.0,
                "last_checked": now,
                "status": "NO_DATA",
            }
        ]
    )
    universe.refresh(force=True)
    no_data_decision = universe.decide(no_data)
    assert no_data_decision.priority == P3_MINIMAL
    assert no_data_decision.fotmob_probe_allowed is False
    database.close()
