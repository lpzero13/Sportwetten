from dataclasses import replace
import pytest
from datetime import date

from fotmob.history_discovery import extract_daily_match_index, _text, _MISSING, _team
from fotmob.history_storage import FotMobHistoryStore
from fotmob.matching import MatchIdentity, MatchMatcher
from fotmob.models import FotMobFetchResult
from services.result_backfill import ResultBackfillRunner, _match_from_record, _match_from_index
from storage.database import Database
from tests.test_result_backfill import _event, _daily_record
from tests.test_fotmob_history import history_settings


def test_full_names_aliases_and_raw_fixture_survive_cache(tmp_path):
    raw = {"id": 123, "home": {"name": "Man City", "longName": "Manchester City"},
           "away": {"name": "Coventry", "longName": "Coventry City"},
           "status": {"utcTime": "2026-09-05T14:00:00Z", "finished": True}}
    payload = {"leagues": [{"id": 47, "name": "Premier League", "ccode": "ENG", "matches": [raw]}]}
    records = extract_daily_match_index(payload, observation_date=date(2026, 9, 5))
    assert records[0].home_team_name == "Manchester City"
    db = Database(tmp_path / "test.db")
    try:
        store = FotMobHistoryStore(db, tmp_path / "archive")
        store.upsert_daily_index(records, observation_date="2026-09-05")
        match = _match_from_index(db.connection.execute("SELECT * FROM fotmob_daily_index").fetchone())
        assert match.extra_data["identity_aliases"]["home"] == ["Manchester City", "Man City"]
        identity = MatchIdentity.from_fotmob_match(match)
        matcher = MatchMatcher()
        assert matcher.match(replace(identity, home_team="Man City", away_team="Coventry"), [match]).auto_linkable
        assert not matcher.match(replace(identity, home_team="Manchester City U21"), [match]).auto_linkable
        assert not matcher.match(replace(identity, home_team="Coventry City", away_team="Manchester City"), [match]).auto_linkable
        assert not matcher.match(identity, [match, replace(match, provider_match_id="different")]).auto_linkable
    finally:
        db.close()


def test_missing_status_never_becomes_object_string():
    assert _text(_MISSING) is None
    assert _text({"finished": False}) is None
    assert _team({"longName": None, "name": "Valid FC"}) == (None, "Valid FC")
    assert _team({"longName": "", "name": "Valid FC"}) == (None, "Valid FC")


def test_final_evidence_supersedes_stale_live_status_but_not_cancellation():
    from services.result_finalization import canonical_game_status
    assert canonical_game_status("running", "SECOND_HALF", has_final_evidence=True) == "FINISHED"
    assert canonical_game_status("pre_match", has_final_evidence=True) == "FINISHED"
    assert canonical_game_status("running", "SECOND_HALF") == "LIVE"
    assert canonical_game_status("cancelled", has_final_evidence=True) == "CANCELLED"


@pytest.mark.parametrize("country,left,right", [
    ("USA", "MLS", "Major League Soccer"),
    ("Israel", "National League", "Leumit League"),
    ("Schweden", "Division 1, Södra", "Ettan Soedra"),
    ("Spanien", "Primera Division RFEF, Gruppe 2", "Primera Federacion - Group 2"),
])
def test_country_scoped_league_aliases(country, left, right):
    match = replace(_match_from_record(_daily_record()), competition_country=country, competition_name=right)
    identity = replace(MatchIdentity.from_fotmob_match(match), competition_name=left)
    assert MatchMatcher().match(identity, [match]).auto_linkable
    assert not MatchMatcher().match(replace(identity, competition_country="England"), [replace(match, competition_country="England")]).auto_linkable


def test_explicit_league_groups_must_not_fuzzy_match():
    match = replace(_match_from_record(_daily_record()), competition_country="Spanien", competition_name="Primera Federacion - Group 2")
    identity = replace(MatchIdentity.from_fotmob_match(match), competition_name="Primera Division RFEF, Gruppe 1")
    assert not MatchMatcher().match(identity, [match]).auto_linkable


def test_detail_alias_transfer_requires_both_team_ids_and_same_league(tmp_path):
    db = Database(tmp_path / "detail-identity.db")
    try:
        event = replace(_event(), home_team="Manchester City", away_team="Coventry City",
                        competition_name="Premier League", competition_country="England")
        catalog = replace(_match_from_record(_daily_record()), home_team="Manchester City", away_team="Coventry City",
                          competition_name="Premier League", competition_country="England",
                          extra_data={"identity_aliases": {"home": ["Manchester City", "Man City"], "away": ["Coventry City", "Coventry"]}})
        detail = replace(catalog, home_team="Man City", away_team="Coventry", extra_data={})
        runner = ResultBackfillRunner(history_settings(tmp_path), db)
        assert runner._validate(event, detail, catalog).status != "IDENTITY_CONFLICT"
        assert runner._validate(event, replace(detail, home_team_id="wrong"), catalog).status == "IDENTITY_CONFLICT"
        assert runner._validate(event, replace(detail, competition_id="wrong"), catalog).status == "IDENTITY_CONFLICT"
    finally:
        db.close()


def test_all_due_includes_local_pending_inventory(tmp_path, monkeypatch):
    from services.result_finalization import ResultFinalizationRunner
    from services.result_backfill import _event_from_row
    db = Database(tmp_path / "all-due.db")
    try:
        for index in range(5):
            db.upsert_event(replace(_event(), event_id=f"due-{index}", status="no_longer_live", period="NO_LONGER_LIVE"), "2026-08-23T00:00:00+00:00")
        captured = {}
        def provider_run(self, **kwargs):
            captured.update(kwargs)
            rows = self._candidate_rows(current=kwargs["now"], limit=kwargs["limit"], event_ids=kwargs["event_ids"])
            assert len(rows) == 5
            return {"status": "PARTIAL", "applied": 0}
        monkeypatch.setattr(ResultBackfillRunner, "run", provider_run)
        result = ResultFinalizationRunner(history_settings(tmp_path, result_backfill_limit=2), db).run(apply=True, all_due=True)
        assert result["events_selected"] == 5
        assert set(captured["event_ids"]) == {f"due-{i}" for i in range(5)}
    finally:
        db.close()


def test_local_recheck_keeps_provider_result_provenance(tmp_path):
    from tests.test_result_finalization import _event as terminal_event
    from services.result_finalization import ResultFinalizationRunner, decide_event, _persist_decision
    db = Database(tmp_path / "provenance.db")
    try:
        db.upsert_event(terminal_event("source-test"), "2026-09-02T20:00:00+00:00")
        ResultFinalizationRunner(history_settings(tmp_path), db).local_pass(apply=True, limit=1)
        db.connection.execute("UPDATE match_results SET result_source='FOTMOB_BACKFILL', result_ft_source='FOTMOB_BACKFILL', result_ht_source='FOTMOB_BACKFILL'")
        db.connection.execute("UPDATE events SET status='no_longer_live', period='NO_LONGER_LIVE'")
        db.connection.execute("DELETE FROM event_states")
        db.connection.execute("DELETE FROM current_event_state")
        db.connection.commit()
        before = dict(db.match_result_for_event("source-test"))
        event = dict(db.event_info("source-test"))
        decision = decide_event(db.connection, event)
        assert decision.evidence.source_record_type == "MATCH_RESULT"
        _persist_decision(db, event, decision, apply=True, checked_at="2026-09-08T10:00:00+00:00")
        after = dict(db.match_result_for_event("source-test"))
        for key in ("result_source", "result_ft_source", "result_ht_source", "result_evidence_id"):
            assert after[key] == before[key]
    finally:
        db.close()


def test_refresh_replaces_in_memory_names_and_invalid_feed_preserves_cache(tmp_path):
    db = Database(tmp_path / "test.db")
    try:
        store = FotMobHistoryStore(db, tmp_path / "archive")
        old = replace(_daily_record(), home_team_name="Old team")
        store.upsert_daily_index([old], observation_date="2026-08-22")
        runner = ResultBackfillRunner(history_settings(tmp_path), db)
        def refreshed(*args, **kwargs):
            runner._provider_days = [{"observation_date": "2026-08-22", "status": "COMPLETE"}]
            return [_match_from_record(_daily_record())]
        runner._refresh_missing_days = refreshed
        catalog = runner._catalog([_event()], allow_network=True, persist=False, force_refresh=True)
        assert catalog.by_id[old.provider_match_id].home_team == "Bayern München"
        # Full-name/fuzzy candidates must reach the real validator, even when
        # the pair is not exactly equal in the prefilter.
        event = replace(_event(), home_team="Bayern Munchen FC", away_team="VfB Stuttgar")
        assert runner._candidate_matches(event, catalog)
        class BadFeed:
            def fetch_json(self, endpoint):
                return FotMobFetchResult(success=True, payload={"message": "unavailable"}, status_code=200)
        runner = ResultBackfillRunner(history_settings(tmp_path), db, client=BadFeed())
        catalog = runner._catalog([_event()], allow_network=True, persist=True, force_refresh=True)
        assert catalog.by_id[old.provider_match_id].home_team == "Old team"
        assert all(day["status"] == "PARSER_ERROR" for day in runner._provider_days)
    finally:
        db.close()
