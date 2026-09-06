from __future__ import annotations

import copy
from datetime import datetime, timezone
from pathlib import Path

from config import Settings
from fotmob.history_models import FotMobMatchIndexRecord
from fotmob.history_storage import FotMobHistoryStore
from fotmob.models import FotMobFetchResult
from fotmob.parser import parse_fotmob_payload
from services.result_backfill import ResultBackfillRunner, _scope_status
from storage.database import Database

from models.event import LiveEvent
from tests.test_fotmob import sample_payload
from tests.test_fotmob_history import history_settings


class _DetailClient:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[str] = []

    def fetch_match_details(self, provider_match_id: str) -> FotMobFetchResult:
        self.calls.append(provider_match_id)
        payload = copy.deepcopy(self.payload)
        return FotMobFetchResult(
            success=True,
            match=parse_fotmob_payload(payload, provider_match_id=provider_match_id),
            payload=payload,
            status_code=200,
            endpoint=f"https://www.fotmob.com/api/data/matchDetails?matchId={provider_match_id}",
        )


def _event() -> LiveEvent:
    return LiveEvent(
        event_id="tipico-result-1",
        competition_id="bundesliga-de",
        competition_name="Bundesliga",
        competition_country="Deutschland",
        sport="SOCCER",
        home_team="Bayern München",
        away_team="VfB Stuttgart",
        home_team_id="27",
        away_team_id="28",
        kickoff_time="2026-08-22T16:30:00+00:00",
        status="running",
        period="HALF_TIME",
        display_minute="HZ",
        score_home=1,
        score_away=0,
        ht_score_home=1,
        ht_score_away=0,
        bet_markets_count=2,
    )


def _daily_record() -> FotMobMatchIndexRecord:
    return FotMobMatchIndexRecord(
        provider_match_id="fotmob-result-1",
        league_id="54",
        season_id="calendar-2026-27",
        season_label="2026/27",
        kickoff_at="2026-08-22T16:30:00+00:00",
        home_team_id="27",
        home_team_name="Bayern München",
        away_team_id="28",
        away_team_name="VfB Stuttgart",
        league_name="Bundesliga",
        country="Deutschland",
        country_code="GER",
        country_name="Deutschland",
        match_status="finished",
        first_seen_at="2026-08-23T00:00:00+00:00",
    )


def test_scope_requires_explicit_regular_time_evidence() -> None:
    payload = sample_payload()
    payload["header"]["status"] = {
        "finished": True,
        "reason": {"short": "FT", "longKey": "finished"},
        "halfs": {
            "firstExtraHalfStarted": "",
            "secondExtraHalfStarted": "",
        },
        "whoLostOnPenalties": None,
    }
    assert _scope_status(parse_fotmob_payload(payload))[0] == "REGULATION"

    unknown = sample_payload()
    assert _scope_status(parse_fotmob_payload(unknown))[0] == "UNKNOWN"


def test_apply_inserts_only_verified_missing_result_and_evidence(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "tipico.db")
    event = _event()
    database.upsert_event(event, "2026-08-22T17:00:00+00:00")
    store = FotMobHistoryStore(database, tmp_path / "archive")
    store.upsert_daily_index(
        [_daily_record()],
        observation_date="2026-08-22",
        source_endpoint="/data/matches",
        payload_hash="daily-hash",
        fetched_at="2026-08-23T00:00:00+00:00",
    )

    payload = sample_payload()
    payload["header"]["status"] = {
        "finished": True,
        "reason": {"short": "FT", "longKey": "finished"},
        "halfs": {
            "firstExtraHalfStarted": "",
            "secondExtraHalfStarted": "",
        },
        "whoLostOnPenalties": None,
    }
    client = _DetailClient(payload)
    settings = history_settings(
        tmp_path,
        result_backfill_enabled=True,
        result_backfill_grace_hours=0,
        result_backfill_allow_unknown_scope=False,
        result_backfill_workers=2,
        fotmob_matching_tolerance_minutes=15,
    )
    runner = ResultBackfillRunner(settings, database, client=client)
    result = runner.run(
        apply=True,
        mode="worker",
        workers=2,
        limit=10,
        now=datetime(2026, 8, 23, 1, tzinfo=timezone.utc),
    )

    assert result["status"] == "PASS"
    assert result["applied"] == 1
    assert client.calls == ["fotmob-result-1"]
    match_result = database.match_result_for_event(event.event_id)
    assert match_result is not None
    assert (match_result["ht_home"], match_result["ht_away"]) == (1, 0)
    assert (match_result["ft_home"], match_result["ft_away"]) == (5, 1)
    assert match_result["second_half_goals"] == 5
    assert match_result["result_source"] == "FOTMOB_BACKFILL"
    assert match_result["result_scope_status"] == "REGULATION"
    queue = database.connection.execute(
        "SELECT status, attempt_count FROM result_backfill_queue WHERE event_id = ?",
        (event.event_id,),
    ).fetchone()
    assert (queue["status"], queue["attempt_count"]) == ("APPLIED", 1)
    evidence = database.connection.execute(
        "SELECT validation_status, provider_match_id FROM result_backfill_evidence WHERE event_id = ?",
        (event.event_id,),
    ).fetchone()
    assert (evidence["validation_status"], evidence["provider_match_id"]) == (
        "VALID_REGULATION",
        "fotmob-result-1",
    )
    assert database.result_backfill_status()["missing_final_results"] == 0
    database.close()


def test_unknown_scope_is_recorded_but_not_promoted(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "tipico.db")
    event = _event()
    database.upsert_event(event, "2026-08-22T17:00:00+00:00")
    store = FotMobHistoryStore(database, tmp_path / "archive")
    store.upsert_daily_index(
        [_daily_record()],
        observation_date="2026-08-22",
        source_endpoint="/data/matches",
        payload_hash="daily-hash",
        fetched_at="2026-08-23T00:00:00+00:00",
    )
    client = _DetailClient(sample_payload())
    settings = history_settings(
        tmp_path,
        result_backfill_enabled=True,
        result_backfill_grace_hours=0,
        result_backfill_allow_unknown_scope=False,
    )
    result = ResultBackfillRunner(settings, database, client=client).run(
        apply=True,
        mode="worker",
        limit=10,
        now=datetime(2026, 8, 23, 1, tzinfo=timezone.utc),
    )

    assert result["applied"] == 0
    assert database.match_result_for_event(event.event_id) is None
    queue = database.connection.execute(
        "SELECT status, validation_status FROM result_backfill_queue WHERE event_id = ?",
        (event.event_id,),
    ).fetchone()
    assert (queue["status"], queue["validation_status"]) == ("SCOPE_UNKNOWN", "SCOPE_UNKNOWN")
    database.close()


def test_later_tipico_final_supersedes_backfill_provenance(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "tipico.db")
    event = _event()
    database.upsert_event(event, "2026-08-22T17:00:00+00:00")
    store = FotMobHistoryStore(database, tmp_path / "archive")
    store.upsert_daily_index(
        [_daily_record()],
        observation_date="2026-08-22",
        source_endpoint="/data/matches",
        payload_hash="daily-hash",
        fetched_at="2026-08-23T00:00:00+00:00",
    )
    payload = sample_payload()
    payload["header"]["status"] = {
        "finished": True,
        "reason": {"short": "FT", "longKey": "finished"},
        "halfs": {"firstExtraHalfStarted": "", "secondExtraHalfStarted": ""},
        "whoLostOnPenalties": None,
    }
    settings = history_settings(
        tmp_path,
        result_backfill_enabled=True,
        result_backfill_grace_hours=0,
        result_backfill_allow_unknown_scope=False,
    )
    ResultBackfillRunner(settings, database, client=_DetailClient(payload)).run(
        apply=True,
        mode="worker",
        limit=10,
        now=datetime(2026, 8, 23, 1, tzinfo=timezone.utc),
    )
    database.upsert_match_result(
        {
            "event_id": event.event_id,
            "competition_id": event.competition_id,
            "competition_name": event.competition_name,
            "competition_country": event.competition_country,
            "home_team": event.home_team,
            "away_team": event.away_team,
            "kickoff_at": event.kickoff_time,
            "ht_home": 1,
            "ht_away": 0,
            "ft_home": 2,
            "ft_away": 0,
            "first_half_goals": 1,
            "second_half_goals": 1,
            "second_half_goal_class": "1",
            "final_status": "finished",
            "finished_at": "2026-08-23T02:00:00+00:00",
            "extra_time": 0,
            "penalties": 0,
        }
    )
    row = database.match_result_for_event(event.event_id)
    assert row is not None
    assert (row["ft_home"], row["ft_away"]) == (2, 0)
    assert row["result_source"] == "TIPICO_ORIGINAL"
    assert row["result_evidence_id"] is None
    database.close()
