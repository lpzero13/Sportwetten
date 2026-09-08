from __future__ import annotations

from pathlib import Path

from config import Settings
from models.event import LiveEvent
from services.result_finalization import ResultFinalizationRunner
from storage.database import Database


def _event(
    event_id: str,
    *,
    status: str = "ended",
    period: str = "FINISHED",
    ft: tuple[int, int] | None = (2, 1),
    ht: tuple[int, int] | None = (1, 0),
    extra_time: bool | None = False,
    penalties: bool | None = False,
) -> LiveEvent:
    return LiveEvent(
        event_id=event_id,
        competition_id="league-1",
        competition_name="Test League",
        competition_country="Deutschland",
        sport="SOCCER",
        home_team_id="home-1",
        home_team="Home FC",
        away_team_id="away-1",
        away_team="Away FC",
        kickoff_time="2026-09-01T18:00:00+00:00",
        status=status,
        period=period,
        display_minute="FT" if status == "ended" else "—",
        score_home=ft[0] if ft else None,
        score_away=ft[1] if ft else None,
        ht_score_home=ht[0] if ht else None,
        ht_score_away=ht[1] if ht else None,
        extra_time=extra_time,
        penalties=penalties,
        bet_markets_count=0,
    )


def _runner(tmp_path: Path, database: Database) -> ResultFinalizationRunner:
    return ResultFinalizationRunner(
        Settings(root_dir=tmp_path, result_backfill_grace_hours=0),
        database,
    )


def test_ended_is_normalized_and_local_result_is_inserted(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "tipico.db")
    event = _event("ended-1")
    database.upsert_event(event, "2026-09-02T20:00:00+00:00")

    result = _runner(tmp_path, database).local_pass(
        apply=True, limit=10
    )

    assert result["local_results"] == 1
    row = database.match_result_for_event(event.event_id)
    assert row is not None
    assert row["final_status"] == "finished"
    assert row["result_status"] == "VERIFIED"
    assert row["result_use_ft"] == 1
    assert row["result_use_h2"] == 1
    assert row["result_source"] == "TIPICO_LOCAL_EVIDENCE"
    assert database.event_info(event.event_id)["canonical_status"] == "FINISHED"
    assert database.connection.execute(
        "SELECT COUNT(*) FROM result_backfill_evidence WHERE provider='TIPICO'"
    ).fetchone()[0] == 1
    database.close()


def test_no_longer_live_interim_score_is_not_promoted(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "tipico.db")
    event = _event("nll-1", status="no_longer_live", period="NO_LONGER_LIVE")
    database.upsert_event(event, "2026-09-02T20:00:00+00:00")

    result = _runner(tmp_path, database).local_pass(apply=True, limit=10)

    assert result["local_results"] == 0
    assert database.match_result_for_event(event.event_id) is None
    queue = database.connection.execute(
        "SELECT status, validation_status FROM result_backfill_queue WHERE event_id = ?",
        (event.event_id,),
    ).fetchone()
    assert queue["status"] == "PENDING_LOCAL"
    assert "NO_LONGER_LIVE" in queue["validation_status"]
    assert database.event_info(event.event_id)["canonical_status"] == "UNKNOWN"
    database.close()


def test_final_snapshot_with_break_status_is_rejected(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "tipico.db")
    event = _event("snapshot-break", status="no_longer_live", period="NO_LONGER_LIVE")
    database.upsert_event(event, "2026-09-02T20:00:00+00:00")
    database.connection.execute(
        """INSERT INTO snapshots (
               event_id, observed_at, snapshot_type, match_status,
               display_time, score_home, score_away, ht_score_home,
               ht_score_away, market_count, outcome_count, snapshot_quality,
               kickoff_time, home_team, away_team, competition_name,
               competition_country
           ) VALUES (?, ?, 'FINAL', 'BREAK', '73', 2, 1, 1, 0, 0, 0,
                     'OK', ?, ?, ?, ?, ?)""",
        (
            event.event_id, "2026-09-02T20:00:00+00:00", event.kickoff_time,
            event.home_team, event.away_team, event.competition_name,
            event.competition_country,
        ),
    )
    database.connection.commit()

    result = _runner(tmp_path, database).local_pass(apply=True, limit=10)

    assert result["local_results"] == 0
    assert database.match_result_for_event(event.event_id) is None
    evidence = database.connection.execute(
        "SELECT validation_status, source_record_type FROM result_backfill_evidence WHERE event_id = ?",
        (event.event_id,),
    ).fetchone()
    assert evidence["validation_status"] == "PENDING"
    assert evidence["source_record_type"] == "EVENT"
    database.close()


def test_partial_final_snapshot_fallback_is_rejected(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "tipico.db")
    event = _event("snapshot-fallback", status="no_longer_live", period="NO_LONGER_LIVE")
    database.upsert_event(event, "2026-09-02T20:00:00+00:00")
    database.connection.execute(
        """INSERT INTO snapshots (
               event_id, observed_at, snapshot_type, match_status,
               display_time, score_home, score_away, ht_score_home,
               ht_score_away, market_count, outcome_count, snapshot_quality,
               kickoff_time, home_team, away_team, competition_name,
               competition_country
           ) VALUES (?, ?, 'FINAL', 'FINISHED', 'FT', 2, 1, 1, 0, 0, 0,
                     'PARTIAL', ?, ?, ?, ?, ?)""",
        (
            event.event_id, "2026-09-02T20:00:00+00:00", event.kickoff_time,
            event.home_team, event.away_team, event.competition_name,
            event.competition_country,
        ),
    )
    database.connection.commit()

    _runner(tmp_path, database).local_pass(apply=True, limit=10)

    assert database.match_result_for_event(event.event_id) is None
    evidence = database.connection.execute(
        "SELECT validation_flags_json FROM result_backfill_evidence WHERE event_id = ?",
        (event.event_id,),
    ).fetchone()
    assert "FINAL_SNAPSHOT_QUALITY_UNSUITABLE" in evidence["validation_flags_json"]
    database.close()


def test_ft_without_ht_is_visible_but_not_h2_usable(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "tipico.db")
    event = _event("ft-only", ht=None)
    database.upsert_event(event, "2026-09-02T20:00:00+00:00")

    _runner(tmp_path, database).local_pass(apply=True, limit=10)

    row = database.match_result_for_event(event.event_id)
    assert row is not None
    assert row["result_status"] == "VERIFIED"
    assert row["result_use_ft"] == 1
    assert row["result_use_h2"] == 0
    assert row["result_reason"] == "HT_MISSING"
    database.close()


def test_ft_below_ht_is_rejected_even_when_total_is_positive(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "tipico.db")
    event = _event("invalid-score", ft=(1, 3), ht=(2, 0))
    database.upsert_event(event, "2026-09-02T20:00:00+00:00")

    _runner(tmp_path, database).local_pass(apply=True, limit=10)

    row = database.match_result_for_event(event.event_id)
    assert row is None
    queue = database.connection.execute(
        "SELECT validation_status FROM result_backfill_queue WHERE event_id = ?",
        (event.event_id,),
    ).fetchone()
    assert queue["validation_status"] == "INVALID_FT_BELOW_HT"
    database.close()


def test_repeated_run_is_idempotent_and_scope_unknown_blocks_release(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "tipico.db")
    event = _event("unknown-scope", extra_time=None, penalties=None)
    database.upsert_event(event, "2026-09-02T20:00:00+00:00")
    runner = _runner(tmp_path, database)

    runner.local_pass(apply=True, limit=10)
    before = database.connection.execute(
        "SELECT COUNT(*) FROM result_finalization_changes"
    ).fetchone()[0]
    runner.local_pass(apply=True, limit=10)
    after = database.connection.execute(
        "SELECT COUNT(*) FROM result_finalization_changes"
    ).fetchone()[0]
    row = database.match_result_for_event(event.event_id)
    assert row is not None
    assert row["result_status"] == "PARTIAL"
    assert row["result_use_ft"] == 0
    assert row["result_use_h2"] == 0
    assert before == after
    database.close()


def test_event_scoped_local_recheck_does_not_touch_other_candidates(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "tipico.db")
    first = _event("recheck-first")
    second = _event("recheck-second")
    database.upsert_event(first, "2026-09-02T20:00:00+00:00")
    database.upsert_event(second, "2026-09-02T20:00:00+00:00")

    result = _runner(tmp_path, database).local_pass(
        apply=True, limit=10, event_id=first.event_id
    )

    assert result["events_selected"] == 1
    assert database.match_result_for_event(first.event_id) is not None
    assert database.match_result_for_event(second.event_id) is None
    database.close()
