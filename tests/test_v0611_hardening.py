from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path

import pytest

from config import Settings
from research.ml_v060 import (
    FEATURES_BY_UNIVERSE,
    ExperimentRegistry,
    ExperimentRunner,
    TARGET_CLASSES,
    _catboost_canary_config,
    verify_research_plan,
)
from research.v0611_runtime import (
    AlreadyRunningError,
    ResearchRunLock,
    deployment_status,
    source_tree_hash,
    write_deployment_manifest,
)
from runtime_status import runtime_identity
from services.collector import Collector
from services.event_service import EventService
from storage.database import Database
from storage.raw_storage import RawStorage
from tests.test_collector import FakeCollectorClient, load_fixture, response


def test_ml_lock_reports_owner_and_blocks_second_start(tmp_path: Path) -> None:
    first = ResearchRunLock(tmp_path, run_id="run-one", mode="test", requested_experiments=1)
    first.acquire()
    try:
        inspection = ResearchRunLock(tmp_path).inspect()
        assert inspection.status == "LOCKED"
        assert inspection.owner_alive is True
        with pytest.raises(AlreadyRunningError):
            ResearchRunLock(tmp_path, run_id="run-two").acquire()
        first.update_phase("TRAINING", experiment_id="EXP-1")
        assert json.loads(first.path.read_text(encoding="utf-8"))["phase"] == "TRAINING"
    finally:
        assert first.release() is True
    assert ResearchRunLock(tmp_path).inspect().status == "UNLOCKED"


def test_stale_lock_recovery_requires_identity_mismatch_and_is_recoverable(tmp_path: Path) -> None:
    path = tmp_path / "research" / "runtime" / "ml_run.lock.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "v0611",
                "run_id": "stale",
                "pid": os.getpid(),
                "ppid": os.getppid(),
                "hostname": "test",
                "process_start_time": 1.0,
                "lock_time": "2026-01-01T00:00:00+00:00",
                "command_line": ["research.ml_v060"],
                "phase": "TRAINING",
            }
        ),
        encoding="utf-8",
    )
    inspection = ResearchRunLock(tmp_path).inspect()
    assert inspection.status == "STALE_LOCK_DETECTED"
    result = ResearchRunLock.clear_stale_lock(tmp_path)
    assert result["status"] == "PASS"
    assert result["removed"] is True


def test_deployment_manifest_is_deterministic_and_integrity_is_explicit(tmp_path: Path) -> None:
    source = tmp_path / "app.py"
    source.write_text("print('one')\n", encoding="utf-8")
    manifest = write_deployment_manifest(tmp_path, settings=Settings(root_dir=tmp_path))
    assert manifest["deployment_schema_version"] == "v0611"
    assert manifest["source_tree_hash"] == source_tree_hash(tmp_path)
    assert deployment_status(tmp_path, check_integrity=True)["status"] == "PASS"
    source.write_text("print('two')\n", encoding="utf-8")
    checked = deployment_status(tmp_path, check_integrity=True)
    assert checked["status"] == "FAIL"
    assert checked["tree_hash_match"] is False


def test_verify_plan_freezes_identity_and_rejects_current_tree_drift(tmp_path: Path) -> None:
    registry = ExperimentRegistry(tmp_path / "registry.sqlite")
    try:
        config = {
            "experiment_id": "V0611_PLAN_TEST",
            "model_family": "LOGISTIC",
            "target_type": "MULTICLASS",
            "feature_universe": "CORE",
        }
        run_id = "run-plan-test"
        row = registry.insert_planned(
            config,
            "dataset-hash",
            runtime_identity(tmp_path)["git_commit"],
            tree_hash=source_tree_hash(tmp_path),
        )
        registry.update(str(row["experiment_id"]), status="PLANNED", research_run_id=run_id)
        result = verify_research_plan(tmp_path, registry, run_id)
        assert result["status"] == "PASS"
        assert registry.get(str(row["experiment_id"]))["plan_verified_at"]
    finally:
        registry.close()


def test_heartbeat_and_full_status_are_separate_paths(tmp_path: Path) -> None:
    settings = Settings(root_dir=tmp_path, store_raw_responses=False)
    database = Database(settings.database_path)
    try:
        collector = Collector(
            object(),  # type: ignore[arg-type]
            database,
            RawStorage(settings.raw_storage_path, enabled=False),
            settings,
        )
        heartbeat = collector.heartbeat()
        full = collector.status(force_refresh=True)
        assert heartbeat["process_alive"] is True
        assert full["status_generation_breakdown"]["total_ms"] >= 0
        assert "status_generation_breakdown" in full["slow_operations"]
        cached = collector.status()
        assert cached["status_cache"]["cached"] is True
        assert "heartbeat" in cached
    finally:
        database.close()


def test_real_data_catboost_canary_round_trips_probabilities(tmp_path: Path) -> None:
    pytest.importorskip("catboost")
    dataset_path = tmp_path / "dataset.bin"
    dataset_path.write_bytes(b"v0611-canary-dataset")
    dataset_hash = hashlib.sha256(dataset_path.read_bytes()).hexdigest()
    rows = []
    for index in range(12):
        rows.append(
            {
                "row_id": str(index),
                "kickoff": f"2026-01-{(index % 9) + 1:02d}T12:00:00+00:00",
                "ml_eligible": True,
                "target_h2_goal_class": TARGET_CLASSES[index % 3],
                **{
                    feature: float(index % 5)
                    for feature in FEATURES_BY_UNIVERSE["CORE"]
                },
            }
        )
    registry = ExperimentRegistry(tmp_path / "registry.sqlite")
    try:
        config = _catboost_canary_config("LOCAL")
        record = registry.insert_planned(config, dataset_hash, "test-commit")
        result = ExperimentRunner(
            tmp_path,
            {"dataset_path": str(dataset_path), "dataset_hash": dataset_hash},
            rows,
            registry,
            research_run_id="run-canary-test",
            model_threads=1,
        ).run(record)
        checks = result["metrics"]["catboost_canary"]
        assert result["effective_model_family"] == "CATBOOST"
        assert checks["class_module"] == "catboost.core"
        assert checks["predict_after_reload"] == "PASS"
        assert checks["probability_reproduction_max_abs_error"] == 0.0
    finally:
        registry.close()


def test_feed_reconciliation_requires_repeated_structural_evidence(tmp_path: Path) -> None:
    live = load_fixture("live_feed.json")
    missing = json.loads(json.dumps(live))
    missing["LIVE"]["events"] = {}
    missing["LIVE"]["eventsBySport"] = {"soccer": []}
    settings = Settings(
        root_dir=tmp_path,
        store_raw_responses=False,
        feed_stale_reconciliation_min_observations=2,
        feed_stale_reconciliation_min_seconds=0,
    )
    database = Database(settings.database_path)
    client = FakeCollectorClient(live, {})
    service = EventService(
        client, database, RawStorage(settings.raw_storage_path, enabled=False), settings
    )
    try:
        assert service.refresh().success is True
        client.live_payload = missing
        assert service.refresh().success is False
        assert service.feed_state == "STALE_SUSPECTED"
        assert service.refresh().success is True
        assert service.feed_state == "STALE_RECONCILED"
        assert service.stale_state_reconciliations == 1
        assert service.events == []
    finally:
        database.close()


def test_feed_failure_injection_does_not_mass_close_and_then_reconciles(tmp_path: Path) -> None:
    live = load_fixture("live_feed.json")
    missing = json.loads(json.dumps(live))
    missing["LIVE"]["events"] = {}
    missing["LIVE"]["eventsBySport"] = {"soccer": []}
    provider_error = json.loads(json.dumps(live))
    provider_error["LIVE"]["error"] = "simulated provider error"
    without_old_event = json.loads(json.dumps(missing))

    class SequenceClient:
        def __init__(self) -> None:
            self.responses = [live, missing, missing, provider_error, without_old_event, without_old_event]

        def get_live_football_events(self):
            value = self.responses.pop(0)
            if isinstance(value, Exception):
                raise value
            return response("https://tipico.test/live", value)

    settings = Settings(
        root_dir=tmp_path,
        store_raw_responses=False,
        feed_stale_reconciliation_min_observations=3,
        feed_stale_reconciliation_min_seconds=0,
    )
    database = Database(settings.database_path)
    service = EventService(
        SequenceClient(), database, RawStorage(settings.raw_storage_path, enabled=False), settings
    )
    try:
        assert service.refresh().success is True
        assert service.refresh().success is False
        assert service.events
        assert service.refresh().success is False
        assert service.events
        outage = service.refresh()
        assert outage.success is False
        assert outage.feed_state == "STALE_SUSPECTED"
        reconciled = service.refresh()
        assert reconciled.success is True
        assert reconciled.feed_state == "STALE_RECONCILED"
        assert service.refresh().success is True
        assert service.events == []
        assert service.feed_provider_errors == 1
        assert service.feed_plausibility_rejects == 3
        assert service.stale_state_reconciliations == 1
        assert service.feed_window_metrics()["last_60m"]["feed_provider_errors"] == 1
    finally:
        database.close()
