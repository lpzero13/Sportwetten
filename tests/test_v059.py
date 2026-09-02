from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from config import Settings
from fotmob.history_models import FotMobMatchIndexRecord
from fotmob.live import FotMobLiveService
from fotmob.matching import MatchIdentity, MatchMatcher
from fotmob.parser import parse_fotmob_payload
from fotmob.service import FotMobService
from fotmob.models import FotMobFetchResult, FotMobMatch
from services.event_service import EventService
from storage.database import Database
from storage.raw_storage import RawStorage
from storage.repositories import EventRepository
from tipico.parser import parse_live_feed

from tests.test_collector import FakeCollectorClient, load_fixture
from tests.test_fotmob import FakeFotMobClient, sample_payload, tipico_event


def _daily_record(match_id: str, *, home: str = "Bayern München", away: str = "VfB Stuttgart") -> FotMobMatchIndexRecord:
    return FotMobMatchIndexRecord(
        provider_match_id=match_id,
        league_id="54",
        season_id="calendar-2026-27",
        season_label="2026/27",
        kickoff_at="2026-08-22T16:30:00+00:00",
        home_team_id="27",
        home_team_name=home,
        away_team_id="28",
        away_team_name=away,
        league_name="Bundesliga",
        country="Deutschland",
        country_code="GER",
        country_name="Deutschland",
        match_status="live",
    )


def test_daily_index_resolver_links_without_match_details_request(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "tipico.db")
    client = FakeFotMobClient(None, fail=True)
    service = FotMobService(
        Settings(root_dir=tmp_path, fotmob_enabled=True),
        database,
        client=client,
    )
    service.history_pipeline.store.upsert_daily_index(
        [_daily_record("daily-1")],
        observation_date="2026-08-22",
    )

    event = tipico_event(competition_id="42301")
    result = service.resolver.resolve(event)

    assert result.match_result.status == "EXACT"
    assert result.provider_match_id == "daily-1"
    assert client.calls == 0
    link = database.connection.execute(
        "SELECT * FROM provider_event_links WHERE tipico_event_id = ?",
        (event.event_id,),
    ).fetchone()
    assert link is not None
    assert link["fotmob_match_id"] == "daily-1"
    assert link["fotmob_league_id"] == "54"
    assert link["match_method"] == "DAILY_INDEX_EXACT"
    database.close()


def test_unmapped_competition_uses_exact_daily_league_country_scope(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "tipico.db")
    client = FakeFotMobClient(None, fail=True)
    service = FotMobService(
        Settings(root_dir=tmp_path, fotmob_enabled=True),
        database,
        client=client,
    )
    service.history_pipeline.store.upsert_daily_index(
        [
            replace(
                _daily_record("austrian-1"),
                league_id="146",
                league_name="2. Liga",
                country="Österreich",
                country_code="AUT",
                country_name="Österreich",
                home_team_name="Rapid Wien",
                away_team_name="LASK",
            )
        ],
        observation_date="2026-08-22",
    )

    event = tipico_event(
        event_id="tipico-at-1",
        competition_id="29301",
        competition_name="2. Liga",
        competition_country="Österreich",
        home_team="Rapid Wien",
        away_team="LASK",
        home_team_id="27",
        away_team_id="28",
    )
    result = service.resolver.resolve(event)

    assert result.match_result.status == "EXACT"
    assert result.provider_match_id == "austrian-1"
    assert client.calls == 0
    database.close()


def test_ambiguous_link_is_persisted_but_live_panel_makes_no_request(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "tipico.db")
    settings = Settings(root_dir=tmp_path, fotmob_enabled=True)
    service = FotMobService(settings, database, client=FakeFotMobClient(None, fail=True))
    service.history_pipeline.store.upsert_daily_index(
        [_daily_record("daily-a"), _daily_record("daily-b")],
        observation_date="2026-08-22",
    )
    event = tipico_event(competition_id="42301")
    result = service.resolver.resolve(event)
    assert result.match_result.status == "AMBIGUOUS"

    calls: list[str] = []

    class Client:
        def fetch_match_details(self, provider_match_id: str) -> FotMobFetchResult:
            calls.append(provider_match_id)
            return FotMobFetchResult(False, error="must not be called")

    live = FotMobLiveService(service, client=Client())
    assert live.fetch_for_event(event).status == "NO_MATCH"
    assert calls == []
    link = database.connection.execute(
        "SELECT match_status, fotmob_match_id FROM provider_event_links WHERE tipico_event_id = ?",
        (event.event_id,),
    ).fetchone()
    assert tuple(link) == ("AMBIGUOUS", None)
    database.close()


def test_manual_invalidation_is_not_revived_by_a_later_daily_index_match(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "data" / "tipico.db")
    client = FakeFotMobClient(None, fail=True)
    service = FotMobService(
        Settings(root_dir=tmp_path, fotmob_enabled=True),
        database,
        client=client,
    )
    service.history_pipeline.store.upsert_daily_index(
        [_daily_record("daily-1")],
        observation_date="2026-08-22",
    )
    event = tipico_event(competition_id="42301")
    service.reject_match(event, "daily-1")

    resolved = service.resolver.resolve(event)

    assert resolved.match_result.status == "INVALIDATED"
    assert resolved.match_result.reasons == ["persisted_manual_invalidation"]
    assert client.calls == 0
    link = database.connection.execute(
        "SELECT match_status, match_method FROM provider_event_links WHERE tipico_event_id = ?",
        (event.event_id,),
    ).fetchone()
    assert tuple(link) == ("INVALIDATED", "MANUAL_INVALIDATED")

    live = FotMobLiveService(service, client=client)
    assert live.fetch_for_event(event).status == "NO_MATCH"
    assert client.calls == 0
    database.close()


def test_manual_confirmation_persists_identity_only(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "tipico.db")
    match = parse_fotmob_payload(sample_payload())
    service = FotMobService(
        Settings(root_dir=tmp_path, fotmob_enabled=True),
        database,
        client=FakeFotMobClient(match),
    )
    event = tipico_event()

    internal_id = service.confirm_manual(event, match)

    assert service.store.provider_event_link_for_tipico_event(event.event_id)["match_status"] == "MANUAL"
    assert service.store.current_state(internal_id) is None
    assert service.store.snapshots_for_match(internal_id) == []
    database.close()


def test_missing_first_half_does_not_create_empty_live_ht_snapshot(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "tipico.db")
    payload = sample_payload()
    del payload["content"]["stats"]["periods"]["1"]
    match = parse_fotmob_payload(payload)
    settings = Settings(
        root_dir=tmp_path,
        fotmob_enabled=True,
        fotmob_network_mode="worker",
        fotmob_provider_decision="PRODUCTION_READY",
        fotmob_automated_usage="ACCEPTABLE_FOR_PROJECT",
        fotmob_min_request_interval_seconds=0,
    )
    service = FotMobService(settings, database, client=FakeFotMobClient(match))
    event = tipico_event()
    assert service.match_tipico_event(event, [match]).status == "EXACT"

    result = service.refresh_for_tipico_event(event, snapshot_type="HALFTIME")
    internal_id = service.store.match_row_for_tipico_event(event.event_id)["internal_match_id"]

    assert result.success is True
    assert result.ht_stats_available is False
    assert "NO_HALFTIME" in (result.error or "")
    assert result.snapshot_created is False
    assert service.store.snapshots_for_match(internal_id) == []
    quality = service.store.quality(internal_id)
    assert quality is not None and quality["fotmob_ht_stats_available"] == 0
    assert service.ml_ht_readiness_for_event(event)["enhanced_ml_allowed"] is False
    database.close()


def test_missing_team_names_never_score_as_a_match() -> None:
    tipico = MatchIdentity(
        provider="TIPICO",
        provider_match_id="tipico-empty",
        kickoff_at="2026-08-22T16:30:00+00:00",
        competition_id="1",
        competition_name="Liga",
        competition_country="Deutschland",
        home_team="",
        away_team="",
    )
    candidate = FotMobMatch(
        provider_match_id="fotmob-empty",
        kickoff_at="2026-08-22T16:30:00+00:00",
        competition_id="1",
        competition_name="Liga",
        competition_country="Deutschland",
        home_team="",
        away_team="",
    )

    result = MatchMatcher().match(tipico, [candidate])

    assert result.status == "UNMATCHED"
    assert result.provider_match_id is None


def test_same_service_suspicious_empty_feed_is_rejected_after_startup(tmp_path: Path) -> None:
    settings = Settings(root_dir=tmp_path, store_raw_responses=False)
    database = Database(settings.database_path)
    client = FakeCollectorClient(load_fixture("live_feed.json"), {})
    service = EventService(
        client,
        database,
        RawStorage(settings.raw_storage_path, enabled=False),
        settings,
    )

    first = service.refresh()
    assert first.success is True
    assert service._startup_reconciliation_done is True

    client.live_payload = {
        "LIVE": {"events": {}, "eventsBySport": {"soccer": []}, "scores": {}}
    }
    rejected = service.refresh()

    assert rejected.success is False
    assert service.plausibility_error_count == 1
    assert [event.event_id for event in service.events] == ["721621110"]
    assert database.current_event_state("721621110")["status"] == "running"
    database.close()


def test_repository_terminal_gate_blocks_stale_detail_state(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "tipico.db")
    settings = Settings(root_dir=tmp_path, store_raw_responses=False)
    event = parse_live_feed(load_fixture("live_feed.json"))[0]
    repository = EventRepository(database)
    assert repository.save_observation(event, "2026-08-22T16:00:00+00:00") is True
    finished = replace(event, status="finished", period="FINISHED", display_minute="FT")
    assert repository.save_observation(finished, "2026-08-22T18:30:00+00:00") is True
    stale = replace(event, status="running", period="RUNNING", display_minute="20'")
    assert repository.save_observation(stale, "2026-08-22T18:31:00+00:00") is False
    current = database.current_event_state(event.event_id)
    assert current is not None and current["status"] == "finished"
    database.close()
