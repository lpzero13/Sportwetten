from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from config import Settings
from fotmob.live import FotMobLiveService, normalize_live_match
from fotmob.matching import normalize_country
from fotmob.models import FotMobFetchResult, FotMobMatch, FotMobStats
from fotmob.service import FotMobService
from fotmob.storage import internal_match_id_for_tipico
from storage.database import Database


class FakeLinkStore:
    def __init__(self, links: dict[str, tuple[str, str]]) -> None:
        self.links = links

    def link_for_internal(self, internal_match_id: str, provider: str = "FOTMOB") -> dict | None:
        value = self.links.get(internal_match_id)
        if value is None or provider.upper() != "FOTMOB":
            return None
        provider_id, status = value
        return {
            "provider_match_id": provider_id,
            "match_status": status,
            "match_confidence": 1.0,
        }


class FakeProviderService:
    enabled = True
    manual_use_allowed = True

    def __init__(self, links: dict[str, tuple[str, str]]) -> None:
        self.store = FakeLinkStore(links)


class FakeLiveClient:
    def __init__(self, responses: list[FotMobFetchResult | Exception]) -> None:
        self.responses = list(responses)
        self.calls: list[str] = []

    def fetch_match_details(self, provider_match_id: str) -> FotMobFetchResult:
        self.calls.append(str(provider_match_id))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def event(event_id: str = "tipico-1") -> SimpleNamespace:
    return SimpleNamespace(event_id=event_id)


def match(
    provider_id: str = "fotmob-1",
    *,
    minute: int | None = 67,
    status: str = "live",
    detailed: bool = True,
) -> FotMobMatch:
    stats = (
        FotMobStats(
            shots_home=12,
            shots_away=7,
            shots_on_target_home=5,
            shots_on_target_away=2,
            big_chances_home=3,
            big_chances_away=1,
            corners_home=6,
            corners_away=2,
            possession_home=58,
            possession_away=42,
        )
        if detailed
        else FotMobStats()
    )
    return FotMobMatch(
        provider_match_id=provider_id,
        kickoff_at="2026-09-01T18:30:00+00:00",
        competition_id="54",
        competition_name="Bundesliga",
        competition_country="Deutschland",
        home_team="Heim",
        away_team="Auswärts",
        home_team_id="1",
        away_team_id="2",
        status=status,
        period="2H" if minute and minute > 45 else "1H",
        minute=minute,
        score_home=2,
        score_away=1,
        stats=stats,
        ht_stats=FotMobStats(shots_home=5, shots_away=2) if detailed else None,
    )


def live_service(
    client: FakeLiveClient,
    *,
    statuses: str = "EXACT",
    clock: list[float] | None = None,
    **kwargs: object,
) -> FotMobLiveService:
    provider = FakeProviderService(
        {internal_match_id_for_tipico("tipico-1"): ("fotmob-1", statuses)}
    )
    return FotMobLiveService(
        provider,
        client=client,
        clock=(lambda: clock[0]) if clock is not None else None,
        **kwargs,
    )


def test_no_selected_match_makes_no_request() -> None:
    client = FakeLiveClient([])
    service = live_service(client)

    result = service.fetch_for_event(None)

    assert result.status == "NO_MATCH"
    assert result.request_made is False
    assert client.calls == []


@pytest.mark.parametrize("link_status", ["EXACT", "HIGH_CONFIDENCE", "MANUALLY_CONFIRMED"])
def test_accepted_matching_status_fetches_selected_match(link_status: str) -> None:
    client = FakeLiveClient([FotMobFetchResult(True, match=match())])
    service = live_service(client, statuses=link_status)

    result = service.fetch_for_event(event())

    assert result.request_made is True
    assert result.provider_match_id == "fotmob-1"
    assert client.calls == ["fotmob-1"]


@pytest.mark.parametrize("link_status", ["AMBIGUOUS", "UNMATCHED", "REJECTED"])
def test_invalid_matching_status_makes_no_request(link_status: str) -> None:
    client = FakeLiveClient([])
    service = live_service(client, statuses=link_status)

    result = service.fetch_for_event(event())

    assert result.status == "NO_MATCH"
    assert result.request_made is False
    assert client.calls == []


def test_explicit_match_id_is_validated_cached_in_ram_and_not_persisted() -> None:
    client = FakeLiveClient([FotMobFetchResult(True, match=match("6003655"))])
    provider = FakeProviderService({})
    service = FotMobLiveService(provider, client=client)
    tipico = SimpleNamespace(
        event_id="tipico-manual",
        kickoff_time="2026-09-01T18:30:00+00:00",
        competition_id="54",
        competition_name="Bundesliga",
        competition_country="Deutschland",
        home_team="Heim",
        away_team="Auswärts",
        home_team_id="1",
        away_team_id="2",
        status="running",
        period="LIVE",
    )

    binding = service.bind_manual_match_id(tipico, "6003655")

    assert binding.success is True
    assert binding.match_status == "EXACT"
    assert binding.live_result is not None
    assert binding.live_result.status == "AVAILABLE"
    assert service.provider_match_id_for_event(tipico) == "6003655"
    assert client.calls == ["6003655"]
    assert provider.store.links == {}

    cached = service.fetch_for_event(tipico, allow_network=False)
    assert cached.request_made is False
    assert cached.cache_hit is True
    assert cached.data is not None


def test_provider_country_codes_match_tipico_localized_countries() -> None:
    assert normalize_country("ITA") == normalize_country("Italien")
    assert normalize_country("ESP") == normalize_country("Spanien")
    assert normalize_country("DEU") == normalize_country("Deutschland")


def test_explicit_match_url_extracts_fragment_id() -> None:
    client = FakeLiveClient([FotMobFetchResult(True, match=match("6003655"))])
    service = live_service(client)
    tipico = SimpleNamespace(
        event_id="tipico-1",
        kickoff_time="2026-09-01T18:30:00+00:00",
        competition_id="54",
        competition_name="Bundesliga",
        competition_country="Deutschland",
        home_team="Heim",
        away_team="Auswärts",
        home_team_id="1",
        away_team_id="2",
    )

    binding = service.bind_manual_match_id(
        tipico,
        "https://www.fotmob.com/de/matches/cremonese-vs-parma/2o48wh#6003655",
    )

    assert binding.success is True
    assert client.calls == ["6003655"]


def test_cache_blocks_duplicate_until_ttl_then_fetches_again() -> None:
    clock = [100.0]
    client = FakeLiveClient([
        FotMobFetchResult(True, match=match()),
        FotMobFetchResult(True, match=match()),
    ])
    service = live_service(client, clock=clock, cache_ttl_seconds=8)

    first = service.fetch_for_event(event())
    clock[0] = 105.0
    cached = service.fetch_for_event(event())
    clock[0] = 109.0
    second = service.fetch_for_event(event())

    assert first.request_made is True
    assert cached.cache_hit is True
    assert cached.request_made is False
    assert second.request_made is True
    assert len(client.calls) == 2


def test_partial_but_sufficient_stats_are_available_and_missing_metric_stays_none() -> None:
    partial = match()
    partial.stats = FotMobStats(
        shots_home=10,
        shots_away=4,
        shots_on_target_home=4,
        shots_on_target_away=1,
        corners_home=3,
        corners_away=2,
    )
    data = normalize_live_match(partial)

    assert data.stats["shots"] == (10, 4)
    assert data.stats["xg"] == (None, None)
    assert data.stats["xgot"] == (None, None)
    assert data.stats["possession"] == (None, None)

    client = FakeLiveClient([FotMobFetchResult(True, match=partial)])
    result = live_service(client).fetch_for_event(event())
    assert result.status == "AVAILABLE"


def test_http_200_basic_data_is_pending_early_and_no_data_after_threshold() -> None:
    clock = [0.0]
    responses = [
        FotMobFetchResult(True, match=match(minute=2, detailed=False)),
        FotMobFetchResult(True, match=match(minute=11, detailed=False)),
        FotMobFetchResult(True, match=match(minute=11, detailed=False)),
        FotMobFetchResult(True, match=match(minute=11, detailed=False)),
    ]
    client = FakeLiveClient(responses)
    service = live_service(client, clock=clock, cache_ttl_seconds=0)

    first = service.fetch_for_event(event())
    assert first.status == "PENDING"
    assert first.availability_status == "DETAILED_DATA_PENDING"
    clock[0] += 1
    second = service.fetch_for_event(event())
    clock[0] += 1
    third = service.fetch_for_event(event())
    clock[0] += 1
    fourth = service.fetch_for_event(event())

    assert second.status == "PENDING"
    assert third.status == "NO_DATA"
    assert fourth.status == "NO_DATA"
    assert third.availability_status == "NO_DETAILED_DATA"
    calls_before_terminal_retry = len(client.calls)
    terminal = service.fetch_for_event(event(), force=True)
    assert terminal.status == "NO_DATA"
    assert len(client.calls) == calls_before_terminal_retry


def test_request_error_keeps_previous_good_data_and_backoff() -> None:
    clock = [0.0]
    client = FakeLiveClient([
        FotMobFetchResult(True, match=match()),
        RuntimeError("timeout"),
        FotMobFetchResult(True, match=match(minute=68)),
    ])
    service = live_service(client, clock=clock, cache_ttl_seconds=0)
    first = service.fetch_for_event(event())
    clock[0] = 1.0
    failed = service.fetch_for_event(event())
    clock[0] = 5.0
    during_backoff = service.fetch_for_event(event())
    clock[0] = 12.0
    recovered = service.fetch_for_event(event())

    assert first.status == "AVAILABLE"
    assert failed.status == "ERROR"
    assert failed.data is not None
    assert failed.data.minute == 67
    assert failed.availability_status == "DETAILED_DATA_AVAILABLE"
    assert during_backoff.request_made is False
    assert recovered.status == "AVAILABLE"
    assert recovered.data is not None
    assert recovered.data.minute == 68


def test_finished_match_stops_further_requests() -> None:
    client = FakeLiveClient([
        FotMobFetchResult(True, match=match(status="finished")),
        FotMobFetchResult(True, match=match(status="finished")),
    ])
    service = live_service(client)

    finished = service.fetch_for_event(event())
    again = service.fetch_for_event(event(), force=True)

    assert finished.status == "FINISHED"
    assert finished.should_auto_refresh is False
    assert again.status == "FINISHED"
    assert len(client.calls) == 1


def test_tipico_finished_state_also_stops_live_requests() -> None:
    client = FakeLiveClient([FotMobFetchResult(True, match=match(status="live"))])
    service = live_service(client)
    finished_event = SimpleNamespace(event_id="tipico-1", status="finished", period="FINISHED")

    result = service.fetch_for_event(finished_event)
    again = service.fetch_for_event(finished_event, force=True)

    assert result.status == "FINISHED"
    assert again.request_made is False
    assert client.calls == ["fotmob-1"]


def test_match_switch_uses_new_provider_id_and_cache() -> None:
    clock = [0.0]
    provider = FakeProviderService(
        {
            internal_match_id_for_tipico("tipico-1"): ("fotmob-1", "EXACT"),
            internal_match_id_for_tipico("tipico-2"): ("fotmob-2", "HIGH_CONFIDENCE"),
        }
    )
    client = FakeLiveClient([
        FotMobFetchResult(True, match=match("fotmob-1")),
        FotMobFetchResult(True, match=match("fotmob-2")),
    ])
    service = FotMobLiveService(provider, client=client, clock=lambda: clock[0])

    first = service.fetch_for_event(event("tipico-1"))
    clock[0] = 9.0
    second = service.fetch_for_event(event("tipico-2"))

    assert first.provider_match_id == "fotmob-1"
    assert second.provider_match_id == "fotmob-2"
    assert client.calls == ["fotmob-1", "fotmob-2"]


def test_last_15_aggregates_shots_sot_and_xg_without_persistence() -> None:
    payload = {
        "content": {
            "shotmap": {
                "shots": [
                    {"teamId": "1", "min": 53, "expectedGoals": 0.3, "eventType": "Goal"},
                    {"teamId": "1", "min": 60, "expectedGoals": 0.2, "eventType": "AttemptSaved"},
                    {"teamId": "2", "min": 55, "expectedGoals": 0.1, "eventType": "Miss"},
                    {"teamId": "1", "min": 51, "expectedGoals": 0.9, "eventType": "Goal"},
                ]
            }
        }
    }
    live_match = match(minute=67)
    live_match.raw_data = payload
    data = normalize_live_match(live_match, payload)

    assert data.shotmap_available is True
    assert data.last_15["shots"] == (2, 1)
    assert data.last_15["shots_on_target"] == (2, 0)
    assert data.last_15["xg"] == (0.5, 0.1)


def test_period_view_keeps_provider_periods_and_live_only_metrics() -> None:
    payload = {
        "content": {
            "stats": {
                "Periods": {
                    "All": {
                        "stats": [
                            {"title": "Total shots", "stats": [14, 8]},
                            {"title": "Shots on target", "stats": [6, 3]},
                            {"title": "Corners", "stats": [7, 2]},
                            {"title": "Expected goals on target", "stats": [1.5, 0.6]},
                            {"title": "Big chances missed", "stats": [2, 1]},
                        ]
                    },
                    "FirstHalf": {"stats": [{"title": "Total shots", "stats": [7, 4]}]},
                    "SecondHalf": {"stats": [{"title": "Total shots", "stats": [7, 4]}]},
                }
            }
        }
    }
    live_match = match()
    live_match.raw_data = payload
    data = normalize_live_match(live_match, payload)

    assert data.periods["ALL"]["shots"] == (14, 8)
    assert data.periods["FIRST_HALF"]["shots"] == (7, 4)  # type: ignore[index]
    assert data.periods["SECOND_HALF"]["shots"] == (7, 4)  # type: ignore[index]
    assert data.stats["xgot"] == (1.5, 0.6)
    assert data.stats["big_chances_missed"] == (2, 1)


def test_live_refresh_does_not_change_sqlite_rows_or_archive_files(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "tipico.db")
    settings = Settings(root_dir=tmp_path, fotmob_enabled=True)
    candidate = match("fotmob-1")
    provider_client = FakeLiveClient([FotMobFetchResult(True, match=candidate)])
    persistent_service = FotMobService(settings, database, client=provider_client)
    tipico = SimpleNamespace(
        event_id="tipico-1",
        kickoff_time="2026-09-01T18:30:00+00:00",
        competition_id="54",
        competition_name="Bundesliga",
        competition_country="Deutschland",
        home_team="Heim",
        away_team="Auswärts",
        home_team_id="1",
        away_team_id="2",
    )
    assert persistent_service.match_tipico_event(tipico, [candidate]).status == "EXACT"

    tables = [
        "matches",
        "match_provider_links",
        "fotmob_current_state",
        "fotmob_snapshots",
        "fotmob_snapshot_outbox",
    ]
    before_rows = {table: database.count_rows(table) for table in tables}
    archive_root = tmp_path / "data" / "archive"
    before_files = {
        str(path.relative_to(tmp_path)): path.stat().st_size
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    clock = [0.0]
    live_client = FakeLiveClient([
        FotMobFetchResult(True, match=match(minute=67)),
        FotMobFetchResult(True, match=match(minute=68)),
    ])
    live = FotMobLiveService(persistent_service, client=live_client, clock=lambda: clock[0], cache_ttl_seconds=8)
    live.fetch_for_event(tipico)
    clock[0] = 9.0
    live.fetch_for_event(tipico)

    after_rows = {table: database.count_rows(table) for table in tables}
    after_files = {
        str(path.relative_to(tmp_path)): path.stat().st_size
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert before_rows == after_rows
    assert before_files == after_files
    assert not list(archive_root.rglob("*.parquet"))
    database.close()
