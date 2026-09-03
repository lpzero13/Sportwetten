from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from config import Settings
from fotmob.client import FotMobClient
from fotmob.matching import MatchIdentity, MatchMatcher
from fotmob.models import FOTMOB_SNAPSHOT_TYPES, FotMobFetchResult, FotMobMatch, FotMobStats
from fotmob.parser import parse_fotmob_payload
from fotmob.service import FotMobService, compare_halftime, compare_results
from storage.database import Database


def sample_payload(
    *,
    match_id: int = 5881143,
    home: str = "Bayern München",
    away: str = "VfB Stuttgart",
    kickoff: str = "2026-08-22T16:30:00Z",
) -> dict:
    return {
        "general": {
            "matchId": match_id,
            "matchTimeUTC": kickoff,
            "homeTeam": {"id": 27, "name": home},
            "awayTeam": {"id": 28, "name": away},
            "leagueId": 54,
            "leagueName": "Bundesliga",
            "country": {"name": "Deutschland"},
            "season": {"name": "2026/2027"},
            "matchRound": "1",
        },
        "header": {
            "status": {"type": "finished"},
            "teams": [
                {"team": {"id": 27, "name": home}, "score": 5, "halfTimeScore": 1},
                {"team": {"id": 28, "name": away}, "score": 1, "halfTimeScore": 0},
            ],
        },
        "content": {
            "stats": {
                "periods": {
                    "All": {
                        "stats": [
                            {"title": "Expected goals (xG)", "stats": ["4,06", "0,79"]},
                            {"title": "Total shots", "stats": [21, 11]},
                            {"title": "Shots on target", "stats": [9, 4]},
                            {"title": "Big chances", "stats": [7, 2]},
                            {"title": "Corners", "stats": [9, 3]},
                            {"title": "Possession", "stats": ["59%", "41%"]},
                            {"title": "Unknown future metric", "stats": [4, 5]},
                        ]
                    },
                    "1": {
                        "stats": [
                            {"title": "Expected goals (xG)", "stats": ["1,34", "0,40"]},
                            {"title": "Total shots", "stats": [9, 4]},
                            {"title": "Corners", "stats": [5, 2]},
                        ]
                    },
                }
            },
            "incidents": [
                {"incidentType": "Goal", "time": "32", "isHome": True, "homeScore": 1, "awayScore": 0},
                {"incidentType": "YellowCard", "time": "44+1", "isHome": False},
            ],
        },
    }


def tipico_event(**overrides: object) -> SimpleNamespace:
    values = {
        "event_id": "tipico-1",
        "kickoff_time": "2026-08-22T16:30:00+00:00",
        "competition_id": "bundesliga-de",
        "competition_name": "Bundesliga",
        "competition_country": "Deutschland",
        "home_team": "Bayern München",
        "away_team": "VfB Stuttgart",
        "home_team_id": "27",
        "away_team_id": "28",
        "status": "LIVE",
        "score_home": 1,
        "score_away": 0,
        "ht_score_home": 1,
        "ht_score_away": 0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_parser_preserves_nullable_stats_and_explicit_first_half() -> None:
    match = parse_fotmob_payload(sample_payload())

    assert match.provider_match_id == "5881143"
    assert match.competition_country == "Deutschland"
    assert match.score_home == 5
    assert match.ht_score_away == 0
    assert match.stats.xg_home == 4.06
    assert match.stats.possession_home == 59
    assert match.ht_stats_available is True
    assert match.ht_stats is not None
    assert match.ht_stats.xg_home == 1.34
    assert match.ht_stats.shots_home == 9
    assert match.stats.extra_stats["unknown future metric"] == [4.0, 5.0]
    assert match.events[0].minute == 32
    assert match.events[1].added_time == 1


def test_parser_handles_current_public_next_payload_shape() -> None:
    payload = {
        "props": {
            "pageProps": {
                "general": {
                    "matchId": "9001",
                    "matchTimeUTCDate": "2025-08-22T18:30:00.000Z",
                    "homeTeam": {"id": 27, "name": "Bayern München"},
                    "awayTeam": {"id": 28, "name": "RB Leipzig"},
                    "leagueId": 54,
                    "leagueName": "Bundesliga",
                    "countryCode": "GER",
                },
                "header": {
                    "status": {"finished": True, "reason": {"longKey": "finished"}},
                    "teams": [
                        {"id": 27, "name": "Bayern München", "score": 2},
                        {"id": 28, "name": "RB Leipzig", "score": 1},
                    ],
                },
                "content": {
                    "matchFacts": {
                        "events": {
                            "events": [
                                {
                                    "type": "Goal",
                                    "time": 25,
                                    "isHome": True,
                                    "homeScore": 0,
                                    "awayScore": 0,
                                    "newScore": [1, 0],
                                },
                                {
                                    "type": "Goal",
                                    "time": 65,
                                    "isHome": False,
                                    "homeScore": 1,
                                    "awayScore": 0,
                                    "newScore": [1, 1],
                                },
                            ]
                        }
                    },
                    "stats": {
                        "Periods": {
                            "All": {
                                "stats": [
                                    {"title": "Expected goals (xG)", "stats": ["1.2", "0.8"]},
                                    {"title": "Total shots", "stats": [12, 8]},
                                ]
                            },
                            "FirstHalf": {
                                "stats": [
                                    {"title": "Expected goals (xG)", "stats": ["0.7", "0.2"]},
                                    {"title": "Total shots", "stats": [7, 3]},
                                ]
                            },
                        }
                    },
                },
            }
        }
    }

    match = parse_fotmob_payload(payload)

    assert match.provider_match_id == "9001"
    assert match.kickoff_at == "2025-08-22T18:30:00+00:00"
    assert match.competition_country == "GER"
    assert match.status == "finished"
    assert (match.ht_score_home, match.ht_score_away) == (1, 0)
    assert (match.score_home, match.score_away) == (2, 1)
    assert (match.stats.xg_home, match.stats.xg_away) == (1.2, 0.8)
    assert (match.ht_stats.xg_home, match.ht_stats.xg_away) == (0.7, 0.2)
    assert len(match.events) == 2
    assert (match.events[0].score_home, match.events[0].score_away) == (1, 0)


def test_parser_reads_current_live_time_from_header_status() -> None:
    payload = sample_payload()
    payload["header"]["status"] = {
        "started": True,
        "finished": False,
        "ongoing": True,
        "liveTime": {
            "short": "HT",
            "shortKey": "halftime_short",
            "maxTime": 45,
            "basePeriod": 45,
            "addedTime": 0,
        },
    }

    match = parse_fotmob_payload(payload)

    assert match.status == "started"
    assert match.period == "HT"
    assert match.minute == 45
    assert match.added_time == 0


def test_matcher_requires_order_country_and_kickoff() -> None:
    tipico = MatchIdentity.from_tipico_event(tipico_event())
    matcher = MatchMatcher(tolerance_minutes=15)
    candidate = parse_fotmob_payload(
        sample_payload(home="Bayern Munich"),
    )
    result = matcher.match(tipico, [candidate])
    assert result.status == "EXACT"
    assert result.provider_match_id == "5881143"

    reversed_match = parse_fotmob_payload(sample_payload(home="VfB Stuttgart", away="Bayern München"))
    assert matcher.match(tipico, [reversed_match]).status == "UNMATCHED"
    wrong_country = parse_fotmob_payload(sample_payload())
    wrong_country.competition_country = "Österreich"
    assert matcher.match(tipico, [wrong_country]).status == "UNMATCHED"
    late = parse_fotmob_payload(sample_payload(kickoff="2026-08-22T20:30:00Z"))
    assert matcher.match(tipico, [late]).status == "UNMATCHED"


def test_matcher_protects_reserves_and_reports_ambiguity() -> None:
    matcher = MatchMatcher(tolerance_minutes=15)
    tipico = MatchIdentity.from_tipico_event(tipico_event(home_team="Ajax", away_team="PSV"))
    reserve = parse_fotmob_payload(sample_payload(home="Ajax II", away="PSV"))
    assert matcher.match(tipico, [reserve]).status == "UNMATCHED"
    first = parse_fotmob_payload(sample_payload(match_id=1))
    second = parse_fotmob_payload(sample_payload(match_id=2))
    result = matcher.match(MatchIdentity.from_tipico_event(tipico_event()), [first, second])
    assert result.status == "AMBIGUOUS"
    assert result.provider_match_id is None


def test_resolver_keeps_german_and_austrian_bundesliga_separate(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "tipico.db")
    settings = Settings(root_dir=tmp_path, fotmob_enabled=True)
    service = FotMobService(settings, database, client=FakeFotMobClient(None, fail=True))

    german = tipico_event(competition_id="42301")
    austrian = tipico_event(
        event_id="tipico-at",
        competition_id="29301",
        competition_country="Österreich",
    )
    assert service.resolver.mapping_for_event(german) is not None
    assert service.resolver.mapping_for_event(austrian) is None
    database.close()


class FakeFotMobClient:
    def __init__(self, match: FotMobMatch | None, *, fail: bool = False) -> None:
        self.match = match
        self.fail = fail
        self.calls = 0

    def fetch_match_details(self, provider_match_id: str) -> FotMobFetchResult:
        self.calls += 1
        if self.fail or self.match is None:
            return FotMobFetchResult(False, error="fake provider failure")
        return FotMobFetchResult(True, match=self.match)

    def metrics_snapshot(self) -> dict:
        return {"requests": self.calls, "successes": 0 if self.fail else self.calls}


class FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None) -> None:
        self.status_code = status_code
        self.content = (
            __import__("json").dumps(payload or {}).encode("utf-8") if payload is not None else b""
        )
        self._payload = payload

    def json(self) -> dict:
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.headers: dict[str, str] = {}
        self.responses = responses

    def get(self, url: str, *, timeout: int) -> FakeResponse:
        return self.responses.pop(0)


def test_access_metrics_expose_latency_http_and_parse_failures() -> None:
    session = FakeSession(
        [
            FakeResponse(200, sample_payload(match_id=1)),
            FakeResponse(200, sample_payload(match_id=2)),
            FakeResponse(429),
        ]
    )
    client = FotMobClient(session=session, max_retries=0, min_request_interval_seconds=0)
    assert client.fetch_match_details("1").success
    assert client.fetch_match_details("2").success
    assert client.fetch_match_details("3").success is False
    metrics = client.metrics_snapshot()
    assert metrics["requests"] == 3
    assert metrics["successes"] == 2
    assert metrics["http_failures"] == 1
    assert metrics["rate_limit_responses"] == 1
    assert metrics["median_response_ms"] >= 0
    assert metrics["p95_response_ms"] >= 0
    assert metrics["status_counts"]["429"] == 1

    parse_failure_client = FotMobClient(
        session=FakeSession([FakeResponse(200, {"general": {}})]),
        max_retries=0,
        min_request_interval_seconds=0,
    )
    assert parse_failure_client.fetch_match_details("bad").success is False
    assert parse_failure_client.metrics_snapshot()["parse_failures"] == 1


def test_provider_policy_disables_automatic_worker_but_keeps_explicit_service_capability(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "data" / "tipico.db")
    settings = Settings(
        root_dir=tmp_path,
        fotmob_enabled=True,
        fotmob_network_mode="manual",
        fotmob_provider_decision="LIMITED_USE",
        fotmob_automated_usage="UNCLEAR",
    )
    service = FotMobService(settings, database, client=FakeFotMobClient(None, fail=True))
    assert service.provider_decision == "LIMITED_USE"
    assert service.automated_usage == "UNCLEAR"
    assert service.automated_worker_allowed is False
    metrics = service.metrics()
    assert metrics["provider_decision"] == "LIMITED_USE"
    assert metrics["manual_use_allowed"] is True
    assert metrics["automated_worker_allowed"] is False
    blocked = service.refresh_for_tipico_event(tipico_event(), snapshot_type="HALFTIME")
    assert blocked.success is False
    assert "Provider-Policy" in (blocked.error or "")
    database.close()


def test_service_uses_same_db_and_keeps_current_refresh_out_of_history(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "tipico.db")
    match = parse_fotmob_payload(sample_payload())
    settings = Settings(root_dir=tmp_path, fotmob_enabled=True, fotmob_min_request_interval_seconds=0)
    service = FotMobService(settings, database, client=FakeFotMobClient(match))

    matching = service.match_tipico_event(tipico_event(), [match])
    assert matching.status == "EXACT"
    internal_id = service.store.match_row_for_tipico_event("tipico-1")["internal_match_id"]
    for _ in range(100):
        result = service.refresh_link(internal_id)
        assert result.success
    assert database.count_rows("events") == 0
    assert service.store.current_state(internal_id) is not None
    assert service.has_current_state_for_tipico_event("tipico-1") is True
    assert len(service.store.snapshots_for_match(internal_id)) == 0
    database.close()


def test_ht_snapshot_is_idempotent_and_archive_is_zstd_parquet(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "tipico.db")
    match = parse_fotmob_payload(sample_payload())
    settings = Settings(root_dir=tmp_path, fotmob_enabled=True, fotmob_min_request_interval_seconds=0)
    service = FotMobService(settings, database, client=FakeFotMobClient(match))
    service.match_tipico_event(tipico_event(), [match])
    internal_id = service.store.match_row_for_tipico_event("tipico-1")["internal_match_id"]
    first = service.refresh_link(internal_id, snapshot_type="HALFTIME")
    second = service.refresh_link(internal_id, snapshot_type="HALFTIME")
    assert first.snapshot_created is True
    assert second.snapshot_created is False
    assert len(service.store.snapshots_for_match(internal_id)) == 1
    exported = service.export_pending()
    assert exported["snapshots_exported"] == 1
    assert list((tmp_path / "data" / "archive").rglob("*.parquet"))
    database.close()


def test_halftime_snapshot_parquet_keeps_live_provenance(tmp_path: Path) -> None:
    pytest.importorskip("pyarrow")
    database = Database(tmp_path / "data" / "tipico.db")
    settings = Settings(
        root_dir=tmp_path,
        fotmob_enabled=True,
        fotmob_network_mode="worker",
        fotmob_provider_decision="PRODUCTION_READY",
        fotmob_automated_usage="ACCEPTABLE_FOR_PROJECT",
        fotmob_min_request_interval_seconds=0,
    )
    service = FotMobService(settings, database, client=FakeFotMobClient(parse_fotmob_payload(sample_payload())))
    event = tipico_event()
    assert service.match_tipico_event(event, [parse_fotmob_payload(sample_payload())]).status == "EXACT"

    refreshed = service.refresh_for_tipico_event(event, snapshot_type="HALFTIME")
    assert refreshed.success is True
    assert service.export_pending()["snapshots_exported"] == 1

    parquet_files = list((tmp_path / "data" / "archive" / "fotmob" / "snapshots").rglob("*.parquet"))
    assert len(parquet_files) == 1
    import pyarrow.parquet as parquet

    rows = parquet.read_table(parquet_files[0]).to_pylist()
    assert len(rows) == 1
    row = rows[0]
    assert row["provider"] == "FOTMOB"
    assert row["stats_period"] == "FIRST_HALF"
    assert row["source_context"] == "LIVE_HT"
    assert row["captured_live"] == 1
    assert row["tipico_event_id"] == event.event_id
    database.close()


def test_snapshot_contract_has_exactly_seven_slots(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "tipico.db")
    match = parse_fotmob_payload(sample_payload())
    settings = Settings(root_dir=tmp_path, fotmob_enabled=True, fotmob_min_request_interval_seconds=0)
    service = FotMobService(settings, database, client=FakeFotMobClient(match))
    service.match_tipico_event(tipico_event(), [match])
    internal_id = service.store.match_row_for_tipico_event("tipico-1")["internal_match_id"]
    for snapshot_type in FOTMOB_SNAPSHOT_TYPES:
        assert service.refresh_link(internal_id, snapshot_type=snapshot_type).success
    rows = service.store.snapshots_for_match(internal_id)
    assert len(rows) == 7
    assert {row["snapshot_type"] for row in rows} == set(FOTMOB_SNAPSHOT_TYPES)
    database.close()


def test_result_consistency_is_explicit() -> None:
    match = parse_fotmob_payload(sample_payload())
    result = {"ft_home": 5, "ft_away": 1, "ht_home": 1, "ht_away": 0}
    assert compare_results(result, match) == "RESULT_MATCH"
    assert compare_halftime(result, match) == "HT_MATCH"
    result["ft_home"] = 4
    assert compare_results(result, match) == "RESULT_CONFLICT"
    assert compare_results(None, match) == "TIPICO_RESULT_MISSING"
    missing = parse_fotmob_payload(sample_payload())
    missing.score_home = None
    assert compare_results(result, missing) == "FOTMOB_RESULT_MISSING"


def test_provider_failure_does_not_break_tipico_database(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "tipico.db")
    settings = Settings(root_dir=tmp_path, fotmob_enabled=True)
    service = FotMobService(settings, database, client=FakeFotMobClient(None, fail=True))
    event = tipico_event()
    result = service.discover_and_match(event, "missing")
    assert result.success is False
    assert result.internal_match_id is not None
    assert service.store.match_row_for_tipico_event(event.event_id) is not None
    assert database.count_rows("events") == 0
    database.close()
