from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

from config import Settings
from fotmob.history_models import FotMobMatchIndexRecord
from fotmob.history_storage import FotMobHistoryStore
from models.event import LiveEvent
from services.result_finalization import (
    start_full_result_recovery,
    status_full_result_recovery,
    resume_full_result_recovery,
)
from services.result_backfill import ResultBackfillRunner, _event_from_row
from fotmob.models import FotMobFetchResult
from storage.database import Database


AS_OF = "2026-09-07T12:00:00+00:00"


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "root_dir": tmp_path,
        "result_backfill_enabled": True,
        "result_backfill_grace_hours": 0,
        "result_backfill_workers": 2,
        "fotmob_network_mode": "cached",
        "fotmob_matching_tolerance_minutes": 15,
    }
    values.update(overrides)
    return Settings(**values)


def _event(
    event_id: str,
    *,
    kickoff: str = "2026-09-01T18:00:00+00:00",
    ht: tuple[int, int] | None = (1, 0),
    ft: tuple[int, int] | None = (2, 1),
    status: str = "ended",
    period: str = "FINISHED",
) -> LiveEvent:
    return LiveEvent(
        event_id=event_id,
        competition_id="test-league",
        competition_name="Recovery League",
        competition_country="Deutschland",
        sport="SOCCER",
        home_team_id="home-1",
        home_team=f"Home {event_id}",
        away_team_id="away-1",
        away_team=f"Away {event_id}",
        kickoff_time=kickoff,
        status=status,
        period=period,
        display_minute="FT" if status == "ended" else "—",
        score_home=ft[0] if ft else None,
        score_away=ft[1] if ft else None,
        ht_score_home=ht[0] if ht else None,
        ht_score_away=ht[1] if ht else None,
        extra_time=False,
        penalties=False,
        bet_markets_count=0,
    )


def _cached_index(event: LiveEvent) -> FotMobMatchIndexRecord:
    return FotMobMatchIndexRecord(
        provider_match_id=f"fm-{event.event_id}",
        league_id="test-league",
        season_id="season-2026",
        season_label="2026/27",
        kickoff_at=event.kickoff_time,
        home_team_id=event.home_team_id,
        home_team_name=event.home_team,
        away_team_id=event.away_team_id,
        away_team_name=event.away_team,
        league_name=event.competition_name,
        country=event.competition_country,
        country_code="DEU",
        country_name=event.competition_country,
        match_status="finished",
        first_seen_at=AS_OF,
    )


def test_full_inventory_processes_more_than_one_batch_and_reports_every_event(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "tipico.db")
    try:
        for index in range(1201):
            event = _event(f"event-{index:04d}")
            database.upsert_event(event, AS_OF)

        output = tmp_path / "reports"
        result = start_full_result_recovery(
            database,
            _settings(tmp_path),
            output_dir=output,
            apply=True,
            workers=2,
            mode="cached",
            refresh_index=False,
            as_of_utc=AS_OF,
        )

        assert result["status"] == "COMPLETED"
        assert result["processing_complete"] is True
        assert result["coverage"]["global"]["n_all"] == 1201
        assert result["coverage"]["global"]["n_historical"] == 1201
        assert result["coverage"]["global"]["n_eligible"] == 1201
        assert result["coverage"]["global"]["n_ft"] == 1201
        assert result["coverage"]["global"]["n_h2"] == 1201
        assert result["coverage"]["global"]["processing_complete"] == 1
        assert database.connection.execute(
            "SELECT COUNT(*) FROM result_finalization_items WHERE run_id = ?",
            (result["run_id"],),
        ).fetchone()[0] == 1201
        assert database.connection.execute(
            "SELECT COUNT(DISTINCT event_id) FROM result_finalization_items WHERE run_id = ?",
            (result["run_id"],),
        ).fetchone()[0] == 1201

        with Path(result["report_paths"]["audit"]).open(encoding="utf-8", newline="") as handle:
            assert sum(1 for _ in csv.DictReader(handle)) == 1201
        with Path(result["report_paths"]["changes"]).open(encoding="utf-8", newline="") as handle:
            assert sum(1 for _ in csv.DictReader(handle)) == 1201

        changes_before = database.connection.execute(
            "SELECT COUNT(*) FROM result_finalization_changes"
        ).fetchone()[0]
        repeated = start_full_result_recovery(
            database,
            _settings(tmp_path),
            output_dir=tmp_path / "reports-repeat",
            apply=True,
            workers=2,
            mode="cached",
            refresh_index=False,
            as_of_utc=AS_OF,
        )
        assert repeated["status"] == "COMPLETED"
        assert database.connection.execute(
            "SELECT COUNT(*) FROM result_finalization_changes"
        ).fetchone()[0] == changes_before
    finally:
        database.close()


def test_provider_block_keeps_fixed_inventory_open_and_resume_reuses_run(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "tipico.db")
    try:
        event = _event("provider-block", ht=None)
        database.upsert_event(event, AS_OF)
        FotMobHistoryStore(database, tmp_path / "archive").upsert_daily_index(
            [_cached_index(event)],
            observation_date="2026-09-01",
            source_endpoint="/data/matches",
            payload_hash="cached-payload",
            fetched_at=AS_OF,
        )

        result = start_full_result_recovery(
            database,
            _settings(tmp_path),
            output_dir=tmp_path / "reports",
            apply=True,
            workers=2,
            mode="cached",
            refresh_index=False,
            as_of_utc=AS_OF,
        )
        run_id = result["run_id"]
        assert result["status"] == "BLOCKED_PROVIDER"
        assert result["processing_complete"] is False
        assert result["coverage"]["global"]["provider_blocked"] == 1
        item = database.connection.execute(
            "SELECT stage, provider_status FROM result_finalization_items WHERE run_id = ? AND event_id = ?",
            (run_id, event.event_id),
        ).fetchone()
        assert (item["stage"], item["provider_status"]) == ("BLOCKED_PROVIDER", "CACHED_ONLY")

        resumed = resume_full_result_recovery(
            database,
            _settings(tmp_path),
            run_id,
            workers=2,
            mode="cached",
            refresh_index=False,
        )
        assert resumed["run_id"] == run_id
        assert resumed["status"] == "BLOCKED_PROVIDER"
        assert database.connection.execute(
            "SELECT COUNT(*) FROM result_finalization_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()[0] == 1
        final_run = database.result_finalization_run(run_id)
        assert final_run["status"] == "BLOCKED_PROVIDER"
        assert final_run["run_status"] == "BLOCKED_PROVIDER"
        status = status_full_result_recovery(database.connection, run_id)
        assert status["coverage"]["global"]["processing_complete"] == 0
    finally:
        database.close()


def test_recovery_filter_query_covers_rows_beyond_default_display_page(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "tipico.db")
    try:
        for index in range(12):
            event = _event(
                f"filter-{index}",
                kickoff=f"2026-08-{index + 1:02d}T18:00:00+00:00",
            )
            database.upsert_event(event, AS_OF)
        options = database.result_finalization_filter_options()
        assert "Deutschland" in options["country"]
        assert database.result_finalization_count(country="Deutschland") == 12
        rows = database.result_finalization_rows(
            limit=5,
            offset=10,
            country="Deutschland",
        )
        assert len(rows) == 2
        assert all(row["competition_country"] == "Deutschland" for row in rows)
    finally:
        database.close()


def test_forced_provider_day_refresh_replaces_stale_cache_rows(tmp_path: Path) -> None:
    class EmptyDailyClient:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def fetch_json(self, endpoint: str) -> FotMobFetchResult:
            self.calls.append(endpoint)
            return FotMobFetchResult(
                success=True,
                payload={"leagues": []},
                status_code=200,
                endpoint=endpoint,
            )

    database = Database(tmp_path / "data" / "tipico.db")
    try:
        event = _event("stale-day")
        database.upsert_event(event, AS_OF)
        store = FotMobHistoryStore(database, tmp_path / "archive")
        store.upsert_daily_index(
            [_cached_index(event)],
            observation_date="2026-09-01",
            source_endpoint="/old",
            payload_hash="old",
            fetched_at="2026-09-02T00:00:00+00:00",
        )
        row = database.connection.execute(
            "SELECT e.*, 0 AS backfill_attempt_count, NULL AS backfill_status, NULL AS next_attempt_at, NULL AS last_provider_match_id, NULL AS backfill_identity_status, NULL AS backfill_validation_status, NULL AS backfill_last_error FROM events e WHERE event_id = ?",
            (event.event_id,),
        ).fetchone()
        client = EmptyDailyClient()
        runner = ResultBackfillRunner(_settings(tmp_path), database, client=client)
        runner._refresh_missing_days(
            [_event_from_row(row)],
            database.connection.execute(
                "SELECT * FROM fotmob_daily_index WHERE observation_date = '2026-09-01'"
            ).fetchall(),
            allow_network=True,
            persist=True,
            force=True,
        )
        assert len(client.calls) == 3
        assert database.connection.execute(
            "SELECT COUNT(*) FROM fotmob_daily_index WHERE observation_date = '2026-09-01'"
        ).fetchone()[0] == 0
    finally:
        database.close()


def test_missing_kickoff_uses_durable_age_or_explicit_age_unknown(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "tipico.db")
    try:
        old = _event("no-kickoff-old", kickoff=None, status="running", period="HALF_TIME")
        unknown = _event("no-kickoff-unknown", kickoff=None, status="running", period="HALF_TIME")
        database.upsert_event(old, "2026-09-01T12:00:00+00:00")
        database.upsert_event(unknown, AS_OF)
        run_id = database.create_result_finalization_run({"mode": "full_inventory"})

        inventory = database.seed_result_finalization_inventory(run_id, as_of_utc=AS_OF)
        assert inventory["frozen_event_count"] == 2
        rows = {
            row["event_id"]: row
            for row in database.result_finalization_items(run_id)
        }
        assert (rows[old.event_id]["cohort"], rows[old.event_id]["stage"]) == (
            "HISTORICAL", "PENDING_LOCAL"
        )
        assert rows[old.event_id]["eligibility_reason"] == "KICKOFF_MISSING_HISTORICAL_OBSERVATION"
        assert (rows[unknown.event_id]["cohort"], rows[unknown.event_id]["stage"]) == (
            "AGE_UNKNOWN", "AGE_UNKNOWN"
        )
    finally:
        database.close()
