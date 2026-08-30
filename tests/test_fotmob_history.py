from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from config import Settings
from fotmob.history_discovery import extract_match_index, extract_seasons, select_reproducible_sample
from fotmob.history_models import (
    FOTMOB_HISTORICAL_PARSER_VERSION,
    FOTMOB_HISTORICAL_SCHEMA_VERSION,
    FotMobMatchIndexRecord,
    FotMobSeasonRef,
    historical_row_from_match,
    score_target,
)
from fotmob.history_pipeline import (
    FotMobHistoryPipeline,
    historical_automation_allowed,
    manual_history_allowed,
    worker_history_allowed,
)
from fotmob.history_storage import FotMobHistoryStore
from fotmob.client import FotMobClient
from fotmob.history_cli import main as history_cli_main
from fotmob.models import FotMobFetchResult
from fotmob.parser import parse_fotmob_payload
from storage.database import Database

from tests.test_fotmob import sample_payload


def league_payload() -> dict:
    return {
        "league": {
            "id": 54,
            "name": "Bundesliga",
            "country": {"name": "Deutschland"},
        },
        "seasons": [
            {"id": "fot-season-2526", "name": "2025/26"},
            {"id": "fot-season-2425", "name": "2024/25"},
        ],
    }


def matches_payload(count: int = 7) -> dict:
    base = datetime(2025, 8, 22, 16, 30, tzinfo=timezone.utc)
    matches = []
    for number in range(count):
        kickoff = base + timedelta(days=number * 7)
        matches.append(
            {
                "matchId": f"history-{number:02d}",
                "matchTimeUTC": kickoff.isoformat().replace("+00:00", "Z"),
                "homeTeam": {"id": f"home-{number}", "name": f"Heim {number}"},
                "awayTeam": {"id": f"away-{number}", "name": f"Gast {number}"},
                "status": "finished",
                "score": {"home": number % 3, "away": (number + 1) % 2},
                "matchRound": str(number + 1),
            }
        )
    # A duplicate provider ID must not create a second index row.
    matches.append(copy.deepcopy(matches[0]))
    matches.append(
        {
            "matchId": "history-upcoming",
            "matchTimeUTC": (base + timedelta(days=60)).isoformat().replace("+00:00", "Z"),
            "homeTeam": {"id": "home-upcoming", "name": "Heim künftig"},
            "awayTeam": {"id": "away-upcoming", "name": "Gast künftig"},
            "status": "scheduled",
        }
    )
    return {"matches": matches}


def history_index_record(match_id: str = "5881143") -> FotMobMatchIndexRecord:
    return FotMobMatchIndexRecord(
        provider_match_id=match_id,
        league_id="54",
        season_id="fot-season-2526",
        season_label="2025/26",
        kickoff_at="2025-08-22T16:30:00+00:00",
        home_team_id="27",
        home_team_name="Bayern München",
        away_team_id="28",
        away_team_name="VfB Stuttgart",
        round_name="1",
        match_status="finished",
        league_name="Bundesliga",
        country="Deutschland",
        first_seen_at="2026-08-30T10:00:00+00:00",
    )


def history_settings(root: Path, **overrides: object) -> Settings:
    values = {
        "root_dir": root,
        "fotmob_enabled": True,
        "fotmob_history_enabled": True,
        "fotmob_provider_decision": "PRODUCTION_READY",
        "fotmob_automated_usage": "ACCEPTABLE_FOR_PROJECT",
        "fotmob_history_requests_per_second": 1000.0,
        "fotmob_history_max_retry_attempts": 3,
        "store_fotmob_historical_raw": False,
        "fotmob_history_batch_size": 2,
        "fotmob_network_mode": "worker",
    }
    values.update(overrides)
    return Settings(**values)


def test_season_discovery_requires_and_preserves_real_provider_ids() -> None:
    seasons = extract_seasons(league_payload(), league_id="54", discovered_at="now")

    assert [item.season_id for item in seasons] == ["fot-season-2526", "fot-season-2425"]
    assert [item.season_label for item in seasons] == ["2025/26", "2024/25"]
    assert seasons[0].league_name == "Bundesliga"
    assert seasons[0].country == "Deutschland"


def test_history_league_paths_use_fotmob_api_base() -> None:
    client = FotMobClient(
        base_url="https://www.fotmob.com",
        api_base_url="https://www.fotmob.com/api",
        min_request_interval_seconds=0,
    )
    assert client._url("/leagues?id=54") == "https://www.fotmob.com/api/leagues?id=54"
    assert client._url("/match/123") == "https://www.fotmob.com/match/123"


def test_index_deduplicates_and_samples_finished_matches() -> None:
    seasons = extract_seasons(league_payload(), league_id="54")
    records = extract_match_index(
        matches_payload(),
        league_id="54",
        season=seasons[0],
        first_seen_at="now",
    )

    assert len(records) == 8
    assert len({item.provider_match_id for item in records}) == 8
    selected = select_reproducible_sample(records, count=5)
    assert [item.provider_match_id for item in selected] == [
        "history-00",
        "history-02",
        "history-03",
        "history-05",
        "history-06",
    ]
    assert all(item.match_status == "finished" for item in selected)


def test_index_handles_nested_fotmob_status_and_score_shapes() -> None:
    season = extract_seasons(league_payload(), league_id="54")[0]
    records = extract_match_index(
        {
            "matches": [
                {
                    "id": "nested-1",
                    "home": {"id": 1, "name": "Heim"},
                    "away": {"id": 2, "name": "Gast"},
                    "status": {"utcTime": 1755870600000, "finished": True},
                    "homeScore": {"current": 2},
                    "awayScore": {"current": 1},
                }
            ]
        },
        league_id="54",
        season=season,
    )

    assert len(records) == 1
    assert records[0].match_status == "finished"
    assert records[0].kickoff_at == "2025-08-22T13:50:00+00:00"


def test_historical_row_keeps_explicit_ht_ft_stats_and_target() -> None:
    match = parse_fotmob_payload(sample_payload())
    row = historical_row_from_match(
        history_index_record(),
        match,
        fetched_at="2026-08-30T10:00:00+00:00",
    )

    assert row["schema_version"] == FOTMOB_HISTORICAL_SCHEMA_VERSION
    assert row["parser_version"] == FOTMOB_HISTORICAL_PARSER_VERSION
    assert row["ht_home"] == 1
    assert row["ht_away"] == 0
    assert row["ft_home"] == 5
    assert row["ft_away"] == 1
    assert row["second_half_goals"] == 5
    assert row["second_half_goal_class"] == "2_PLUS"
    assert row["ht_xg_home"] == 1.34
    assert row["ft_xg_home"] == 4.06
    assert row["data_quality"] == "COMPLETE"
    assert row["ml_eligible"] is True
    assert row["ht_extra_stats_json"] == {}
    assert row["ft_extra_stats_json"]["unknown future metric"] == [4.0, 5.0]


def test_score_target_and_quality_never_correct_invalid_scores() -> None:
    assert score_target(1, 1, 2, 2) == (2, "2_PLUS", None)
    assert score_target(2, 1, 1, 1) == (None, None, "INVALID_SCORE_TOTAL_LT_HALFTIME")

    match = parse_fotmob_payload(sample_payload())
    match.score_home = None
    row = historical_row_from_match(history_index_record(), match, fetched_at="now")
    assert row["data_quality"] == "PARTIAL"
    assert row["ml_eligible"] is False


def test_history_store_queue_claim_retry_and_stale_recovery(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "tipico.db")
    store = FotMobHistoryStore(database, tmp_path / "archive")
    records = [
        history_index_record("queue-0"),
        history_index_record("queue-1"),
    ]
    assert store.upsert_match_index(records)["inserted"] == 2
    assert store.upsert_match_index(records)["existing"] == 2

    first = store.claim_next("54", "fot-season-2526", worker_id="worker-a", max_attempts=2)
    assert first is not None
    assert first["detail_status"] == "IN_PROGRESS"
    assert first["attempt_count"] == 1
    assert store.mark_failure(first["fotmob_match_id"], "temporary", max_attempts=2, worker_id="worker-a") == "NOT_FETCHED"

    retry = store.claim_next("54", "fot-season-2526", worker_id="worker-a", max_attempts=2)
    assert retry is not None
    assert retry["fotmob_match_id"] == first["fotmob_match_id"]
    assert store.mark_failure(retry["fotmob_match_id"], "permanent", max_attempts=2, worker_id="worker-a") == "FAILED"

    second = store.claim_next("54", "fot-season-2526", worker_id="worker-b", max_attempts=2)
    assert second is not None
    database.connection.execute(
        "UPDATE fotmob_match_index SET last_attempt_at = ? WHERE fotmob_match_id = ?",
        ("2000-01-01T00:00:00+00:00", second["fotmob_match_id"]),
    )
    database.connection.commit()
    assert store.recover_stale(30) == 1
    assert store.status("54", "fot-season-2526")["counts"]["NOT_FETCHED"] == 1
    database.close()


def test_core_database_initializes_historical_catalog_tables(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "tipico.db")
    assert database.count_rows("fotmob_seasons") == 0
    assert database.count_rows("fotmob_match_index") == 0
    assert database.count_rows("fotmob_historical_archive_index") == 0
    database.close()


def test_optional_historical_raw_is_zstd_json_under_partition(tmp_path: Path) -> None:
    zstandard = pytest.importorskip("zstandard")
    database = Database(tmp_path / "data" / "tipico.db")
    store = FotMobHistoryStore(
        database,
        tmp_path / "archive",
        raw_root=tmp_path / "raw",
    )
    path, payload_hash = store.save_raw_payload(
        {"matchId": "raw-1", "nested": {"value": 2}},
        league_id="54",
        season_label="2025/26",
        provider_match_id="raw-1",
    )
    compressed = Path(path).read_bytes()
    decoded = zstandard.ZstdDecompressor().decompress(compressed).decode("utf-8")
    assert '"matchId": "raw-1"' in decoded
    assert len(payload_hash) == 64
    assert Path(path).parent.name == "season=2025-26"
    database.close()


class FakeHistoryClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def fetch_match_details(self, provider_match_id: str) -> FotMobFetchResult:
        self.calls.append(provider_match_id)
        payload = sample_payload(match_id=int(provider_match_id.split("-")[-1]) if provider_match_id.split("-")[-1].isdigit() else 1)
        match = parse_fotmob_payload(payload, provider_match_id=provider_match_id)
        return FotMobFetchResult(success=True, match=match, payload=payload)

    def metrics_snapshot(self) -> dict[str, int]:
        return {"requests": len(self.calls)}


def test_history_pipeline_fetches_sample_in_batches_and_is_resumable(tmp_path: Path) -> None:
    pytest.importorskip("pyarrow")
    database = Database(tmp_path / "data" / "tipico.db")
    client = FakeHistoryClient()
    pipeline = FotMobHistoryPipeline(history_settings(tmp_path), database, client=client)
    discovered = pipeline.discover_league("54", payload=league_payload())
    season = discovered.seasons[0]
    indexed = pipeline.index_season("54", season, payload=matches_payload(6))
    assert indexed.counts is not None
    assert indexed.counts["inserted"] == 7
    selected = pipeline.sample_season("54", season.season_id, count=5)
    assert len(selected) == 5

    first = pipeline.fetch_details("54", season.season_id, workers=2, only_sample=True, batch_size=2)
    assert first["status"] == "PASS"
    assert first["processed"] == 5
    assert first["fetched"] == 5
    assert first["failed"] == 0
    assert len(client.calls) == 5
    assert database.count_rows("fotmob_historical_archive_index") == 5
    assert first["progress"]["target"] == 5
    assert first["progress"]["fraction"] == 1.0
    assert first["eta_seconds"] is None

    archive_files = list(
        (tmp_path / "data" / "archive" / "fotmob" / "historical" / "league_id=54" / "season=2025-26").glob("*.parquet")
    )
    assert archive_files
    import pyarrow.parquet as parquet

    rows = parquet.read_table(archive_files[0]).to_pylist()
    assert len(rows) in {1, 2}
    assert all(item["schema_version"] == FOTMOB_HISTORICAL_SCHEMA_VERSION for item in rows)
    assert all(isinstance(item["timeline_json"], str) for item in rows)

    second = pipeline.fetch_details("54", season.season_id, workers=2, only_sample=True)
    assert second["status"] == "PASS"
    assert second["processed"] == 0
    assert len(client.calls) == 5
    assert database.count_rows("fotmob_historical_archive_index") == 5
    database.close()


def test_policy_gate_blocks_network_without_calling_client(tmp_path: Path) -> None:
    class CountingClient:
        calls = 0

        def fetch_json(self, endpoint: str) -> FotMobFetchResult:
            self.calls += 1
            return FotMobFetchResult(False, error="must not be called")

    database = Database(tmp_path / "data" / "tipico.db")
    client = CountingClient()
    settings = Settings(root_dir=tmp_path)
    assert historical_automation_allowed(settings) is False
    assert manual_history_allowed(settings) is False
    assert worker_history_allowed(settings) is False
    pipeline = FotMobHistoryPipeline(settings, database, client=client)
    result = pipeline.discover_league("54")
    assert result.success is False
    assert "gesperrt" in (result.error or "")
    assert client.calls == 0
    database.close()


def test_manual_history_mode_does_not_need_worker_provider_gates(tmp_path: Path) -> None:
    manual = Settings(
        root_dir=tmp_path,
        fotmob_enabled=True,
        fotmob_history_enabled=True,
        fotmob_network_mode="manual",
        fotmob_provider_decision="LIMITED_USE",
        fotmob_automated_usage="UNCLEAR",
    )
    assert manual_history_allowed(manual) is True
    assert worker_history_allowed(manual) is False
    assert historical_automation_allowed(manual) is False

    worker = Settings(
        root_dir=tmp_path,
        fotmob_enabled=True,
        fotmob_history_enabled=True,
        fotmob_network_mode="worker",
        fotmob_provider_decision="PRODUCTION_READY",
        fotmob_automated_usage="ACCEPTABLE_FOR_PROJECT",
    )
    assert worker_history_allowed(worker) is True


def test_manual_history_mode_allows_explicit_discovery(tmp_path: Path) -> None:
    class CountingClient:
        def __init__(self) -> None:
            self.calls = 0

        def fetch_json(self, endpoint: str) -> FotMobFetchResult:
            self.calls += 1
            return FotMobFetchResult(success=True, payload=league_payload())

    database = Database(tmp_path / "data" / "tipico.db")
    client = CountingClient()
    settings = Settings(
        root_dir=tmp_path,
        fotmob_enabled=True,
        fotmob_history_enabled=True,
        fotmob_network_mode="manual",
        fotmob_provider_decision="LIMITED_USE",
        fotmob_automated_usage="UNCLEAR",
    )
    result = FotMobHistoryPipeline(settings, database, client=client).discover_league(
        "54",
        execution_mode="manual",
    )
    assert result.success is True
    assert client.calls == 1
    database.close()


def test_history_cli_supports_offline_discovery_and_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv("WETTEN_ARCHIVE_PATH", raising=False)
    league_file = tmp_path / "league.json"
    index_file = tmp_path / "index.json"
    league_file.write_text(json.dumps(league_payload()), encoding="utf-8")
    index_file.write_text(json.dumps(matches_payload(2)), encoding="utf-8")

    assert history_cli_main([
        "seasons", "--league", "54", "--payload", str(league_file), "--root", str(tmp_path),
    ]) == 0
    seasons_result = json.loads(capsys.readouterr().out)
    assert seasons_result["status"] == "PASS"
    assert {item["season_id"] for item in seasons_result["seasons"]} == {"fot-season-2526", "fot-season-2425"}

    assert history_cli_main([
        "index", "--league", "54", "--season", "2025/26", "--payload", str(index_file), "--root", str(tmp_path),
    ]) == 0
    index_result = json.loads(capsys.readouterr().out)
    assert index_result["status"] == "PASS"
    assert index_result["counts"]["inserted"] == 3
