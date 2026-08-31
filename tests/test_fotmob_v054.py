from __future__ import annotations

import copy
from pathlib import Path

import pytest

from fotmob.canonical import (
    CANONICAL_MATCH_CORE_SCHEMA_VERSION,
    FotMobCanonicalArchive,
    event_rows,
    period_stat_rows,
    shot_rows,
)
from fotmob.history_models import FotMobMatchIndexRecord
from fotmob.history_pipeline import FotMobHistoryPipeline
from fotmob.models import FotMobFetchResult
from fotmob.parser import extract_period_stats, parse_fotmob_payload
from storage.database import Database

from tests.test_fotmob import sample_payload
from tests.test_fotmob_history import history_settings


def _index() -> FotMobMatchIndexRecord:
    return FotMobMatchIndexRecord(
        provider_match_id="daily-1",
        league_id="54",
        season_id="season-2526",
        season_label="2025/2026",
        kickoff_at="2025-08-22T18:30:00+00:00",
        home_team_id="27",
        home_team_name="Bayern München",
        away_team_id="28",
        away_team_name="VfB Stuttgart",
        league_name="Bundesliga",
        country="GER",
    )


def test_canonical_archive_keeps_core_period_shots_and_events(tmp_path: Path) -> None:
    pytest.importorskip("pyarrow")
    payload = copy.deepcopy(sample_payload(match_id=123))
    payload["content"]["stats"]["periods"]["SecondHalf"] = {
        "stats": [{"title": "Total shots", "stats": [12, 7]}]
    }
    payload["content"]["shotmap"] = {
        "shots": [
            {
                "id": 99,
                "teamId": 27,
                "min": 12,
                "expectedGoals": 0.21,
                "eventType": "AttemptSaved",
                "playerId": 77,
                "playerName": "Testspieler",
            }
        ]
    }
    match = parse_fotmob_payload(payload, provider_match_id="daily-1")
    assert extract_period_stats(payload)["SECOND_HALF"].shots_home == 12
    assert len(shot_rows(_index(), match, payload, fetched_at="now")) == 1
    assert event_rows(_index(), match, fetched_at="now")[0]["event_type"] == "GOAL"
    assert {row["period"] for row in period_stat_rows(_index(), match, fetched_at="now")} == {
        "FIRST_HALF",
        "SECOND_HALF",
        "ALL",
    }

    archive = FotMobCanonicalArchive(tmp_path / "archive")
    first = archive.write_match(_index(), match, payload, fetched_at="now")
    second = archive.write_match(_index(), match, payload, fetched_at="later")
    assert first["written"] == second["written"] == 1
    assert len(list((tmp_path / "archive" / "match_core").rglob("*.parquet"))) == 1
    import pyarrow.parquet as parquet

    core_path = next((tmp_path / "archive" / "match_core").rglob("*.parquet"))
    row = parquet.read_table(core_path).to_pylist()[0]
    assert row["schema_version"] == CANONICAL_MATCH_CORE_SCHEMA_VERSION
    assert row["country_code"] == "GER"
    assert row["country_name"] == "Deutschland"
    assert row["ht_score_source"] == "EXPLICIT_PROVIDER"


class _DailyClient:
    def __init__(self) -> None:
        self.json_calls: list[str] = []
        self.detail_calls: list[str] = []

    def fetch_json(self, endpoint: str) -> FotMobFetchResult:
        self.json_calls.append(endpoint)
        if "?season=" not in endpoint:
            return FotMobFetchResult(
                success=True,
                payload={
                    "details": {"id": 54, "name": "Bundesliga", "country": "GER"},
                    "stats": {
                        "seasonStatLinks": [
                            {"TournamentId": "season-2526", "Name": "2025/2026"}
                        ]
                    },
                },
            )
        return FotMobFetchResult(
            success=True,
            payload={
                "details": {"id": 54, "name": "Bundesliga", "country": "GER"},
                "fixtures": {
                    "allMatches": [
                        {
                            "id": "daily-1",
                            "status": {"utcTime": "2025-08-22T18:30:00Z", "finished": True},
                            "home": {"id": "27", "name": "Bayern München"},
                            "away": {"id": "28", "name": "VfB Stuttgart"},
                        }
                    ]
                },
            },
        )

    def fetch_match_details(self, provider_match_id: str) -> FotMobFetchResult:
        self.detail_calls.append(provider_match_id)
        payload = sample_payload(match_id=123)
        return FotMobFetchResult(
            success=True,
            payload=payload,
            match=parse_fotmob_payload(payload, provider_match_id=provider_match_id),
        )

    def metrics_snapshot(self) -> dict[str, int]:
        return {"requests": len(self.json_calls) + len(self.detail_calls)}


def test_date_range_loader_persists_date_country_league_and_is_resumable(tmp_path: Path) -> None:
    pytest.importorskip("pyarrow")
    database = Database(tmp_path / "data" / "tipico.db")
    client = _DailyClient()
    settings = history_settings(tmp_path, fotmob_network_mode="manual")
    pipeline = FotMobHistoryPipeline(settings, database, client=client)

    result = pipeline.load_date_range(
        "2025-08-22",
        "2025-08-23",
        league_id="54",
        fetch_details=True,
        execution_mode="manual",
    )
    assert result["status"] == "PASS"
    assert result["fixtures"] == 1
    assert result["details"]["fetched"] == 1
    rows = pipeline.store.daily_index(league_id="54", limit=10)
    assert len(rows) == 1
    assert rows[0]["observation_date"] == "2025-08-22"
    assert rows[0]["country_code"] == "GER"
    assert rows[0]["country_name"] == "Deutschland"
    assert rows[0]["league_name"] == "Bundesliga"
    assert rows[0]["detail_status"] == "FETCHED"
    assert rows[0]["canonical_archive_path"]
    assert rows[0]["canonical_archive_path"].endswith("match-daily-1.parquet")
    assert pipeline.store.daily_status("54")["loaded_days"] == 2

    again = pipeline.load_date_range(
        "2025-08-22",
        "2025-08-23",
        league_id="54",
        fetch_details=True,
        execution_mode="manual",
    )
    assert again["status"] == "PASS"
    assert again["details"]["skipped"] == 1
    assert len(list((tmp_path / "data" / "archive" / "fotmob" / "match_core").rglob("*.parquet"))) == 1
    database.close()


def test_date_range_loader_fetches_every_fixture_on_selected_day(tmp_path: Path) -> None:
    pytest.importorskip("pyarrow")

    class MultiFixtureClient(_DailyClient):
        def fetch_json(self, endpoint: str) -> FotMobFetchResult:
            result = super().fetch_json(endpoint)
            if "?season=" in endpoint and result.payload is not None:
                payload = copy.deepcopy(result.payload)
                payload["fixtures"]["allMatches"].append(
                    {
                        "id": "daily-2",
                        "status": {"utcTime": "2025-08-22T20:30:00Z", "finished": True},
                        "home": {"id": "29", "name": "Borussia Dortmund"},
                        "away": {"id": "30", "name": "RB Leipzig"},
                    }
                )
                return FotMobFetchResult(success=True, payload=payload)
            return result

    database = Database(tmp_path / "data" / "tipico.db")
    client = MultiFixtureClient()
    pipeline = FotMobHistoryPipeline(
        history_settings(tmp_path, fotmob_network_mode="manual"),
        database,
        client=client,
    )
    result = pipeline.load_date_range(
        "2025-08-22",
        "2025-08-22",
        league_id="54",
        fetch_details=True,
        execution_mode="manual",
    )

    assert result["status"] == "PASS"
    assert result["fixtures"] == 2
    assert result["details"]["fetched"] == 2
    assert sorted(client.detail_calls) == ["daily-1", "daily-2"]
    rows = pipeline.store.daily_index(league_id="54", limit=10)
    assert sorted(row["fotmob_match_id"] for row in rows) == ["daily-1", "daily-2"]
    database.close()


class _AllLeaguesClient:
    """Small daily-feed fixture covering multiple countries and no-HZ skips."""

    def __init__(self) -> None:
        self.json_calls: list[str] = []
        self.detail_calls: list[str] = []

    def fetch_json(self, endpoint: str) -> FotMobFetchResult:
        self.json_calls.append(endpoint)
        if "/data/allLeagues" in endpoint:
            return FotMobFetchResult(
                success=True,
                payload={
                    "countries": [
                        {
                            "ccode": "GER",
                            "localizedName": "Deutschland",
                            "leagues": [{"id": 54, "localizedName": "Bundesliga"}],
                        },
                        {
                            "ccode": "AUT",
                            "localizedName": "Österreich",
                            "leagues": [{"id": 146, "localizedName": "2. Liga"}],
                        },
                    ],
                    "international": [
                        {
                            "ccode": "INT",
                            "name": "International",
                            "leagues": [{"id": 45, "localizedName": "Copa Libertadores"}],
                        }
                    ],
                },
            )
        if "/data/matches" in endpoint:
            return FotMobFetchResult(
                success=True,
                payload={
                    "leagues": [
                        {
                            "primaryId": 54,
                            "ccode": "GER",
                            "name": "Bundesliga",
                            "matches": [
                                {
                                    "id": "all-early",
                                    "status": {"utcTime": "2025-08-22T00:05:00Z", "finished": True},
                                    "home": {"id": "g-home-1", "name": "Frühes Heimteam"},
                                    "away": {"id": "g-away-1", "name": "Frühes Gastteam"},
                                },
                                {
                                    "id": "all-no-ht",
                                    "status": {"utcTime": "2025-08-22T18:30:00Z", "finished": True},
                                    "home": {"id": "g-home-2", "name": "Ohne HZ Heimteam"},
                                    "away": {"id": "g-away-2", "name": "Ohne HZ Gastteam"},
                                },
                            ],
                        },
                        {
                            "primaryId": 146,
                            "ccode": "AUT",
                            "name": "2. Liga",
                            "matches": [
                                {
                                    "id": "all-next-day",
                                    "isNextDay": True,
                                    "status": {"utcTime": "2025-08-23T00:15:00Z", "finished": True},
                                    "home": {"id": "a-home-1", "name": "Österreich Heimteam"},
                                    "away": {"id": "a-away-1", "name": "Österreich Gastteam"},
                                }
                            ],
                        },
                    ]
                },
            )
        return FotMobFetchResult(success=False, error=f"unexpected endpoint: {endpoint}")

    def fetch_match_details(self, provider_match_id: str) -> FotMobFetchResult:
        self.detail_calls.append(provider_match_id)
        payload = copy.deepcopy(sample_payload(match_id=123))
        if provider_match_id == "all-no-ht":
            payload["content"]["stats"]["periods"].pop("1", None)
        return FotMobFetchResult(
            success=True,
            payload=payload,
            match=parse_fotmob_payload(payload, provider_match_id=provider_match_id),
        )

    def metrics_snapshot(self) -> dict[str, int]:
        return {"requests": len(self.json_calls) + len(self.detail_calls)}


def test_all_leagues_daily_feed_indexes_every_game_and_skips_missing_first_half(tmp_path: Path) -> None:
    pytest.importorskip("pyarrow")
    database = Database(tmp_path / "data" / "tipico.db")
    client = _AllLeaguesClient()
    pipeline = FotMobHistoryPipeline(
        history_settings(tmp_path, fotmob_network_mode="manual"),
        database,
        client=client,
    )

    result = pipeline.load_date_range(
        "2025-08-22",
        "2025-08-22",
        fetch_details=True,
        execution_mode="manual",
    )

    assert result["status"] == "PASS"
    assert result["scope"] == "ALL_LEAGUES"
    assert result["fixtures"] == result["unique_fixtures"] == 3
    assert result["leagues"] == 2
    assert result["countries"] == 2
    assert result["details"]["fetched"] == 2
    assert result["details"]["skipped_no_halftime"] == 1
    assert result["feed"] == {
        "feed_group_count": 2,
        "feed_entry_count": 3,
        "feed_unique_count": 3,
        "next_day_count": 1,
        "duplicates_removed_count": 0,
        "invalid_entry_count": 0,
    }
    assert result["country_catalog"]["countries"] == 3
    assert sorted(client.detail_calls) == ["all-early", "all-next-day", "all-no-ht"]

    rows = pipeline.store.daily_index(start_date="2025-08-22", end_date="2025-08-22", limit=10)
    assert len(rows) == 3
    assert {row["country_name"] for row in rows} == {"Deutschland", "Österreich"}
    assert {row["season_label"] for row in rows} == {"2025/26"}
    assert next(row for row in rows if row["fotmob_match_id"] == "all-next-day")["is_next_day"] == 1
    assert next(row for row in rows if row["fotmob_match_id"] == "all-no-ht")["detail_status"] == "SKIPPED_NO_HALFTIME"
    assert len(pipeline.store.daily_index(country_code="AUT", limit=10)) == 1
    assert len(pipeline.store.daily_index(league_name="Bundesliga", limit=10)) == 2
    run = pipeline.store.daily_load_runs(start_date="2025-08-22", end_date="2025-08-22", limit=10)[0]
    assert run["skipped_no_halftime_count"] == 1
    assert run["feed_group_count"] == 2
    assert run["feed_entry_count"] == 3
    assert run["feed_unique_count"] == 3
    assert run["next_day_count"] == 1
    assert run["duplicates_removed_count"] == 0
    assert len(list((tmp_path / "data" / "archive" / "fotmob" / "match_core").rglob("*.parquet"))) == 2

    index_refresh = pipeline.load_date_range(
        "2025-08-22",
        "2025-08-22",
        fetch_details=False,
        execution_mode="manual",
    )
    assert index_refresh["status"] == "PASS"
    refreshed_run = pipeline.store.daily_load_runs(
        start_date="2025-08-22", end_date="2025-08-22", limit=10
    )[0]
    assert refreshed_run["detail_count"] == 2
    assert refreshed_run["skipped_no_halftime_count"] == 1
    database.close()
