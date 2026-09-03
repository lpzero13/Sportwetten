from __future__ import annotations

import builtins
import copy
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from config import Settings
from fotmob.history_pipeline import FotMobHistoryPipeline
from fotmob.models import FotMobFetchResult, FotMobMatch
from fotmob.service import FotMobService
from research.ml_v060 import DependencyMissing, ExperimentRegistry, _make_base_model
from storage.database import Database


def test_catboost_missing_dependency_is_not_replaced(monkeypatch: pytest.MonkeyPatch) -> None:
    original_import = builtins.__import__

    def blocked(name: str, *args: object, **kwargs: object):
        if name == "catboost" or name.startswith("catboost."):
            raise ImportError("test: catboost unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    with pytest.raises(DependencyMissing) as raised:
        _make_base_model(
            {
                "model_family": "CATBOOST",
                "target_type": "MULTICLASS",
                "hyperparameters": {"iterations": 2},
            },
            3,
        )
    assert raised.value.dependency == "catboost"
    assert raised.value.requested_model_family == "CATBOOST"


def test_registry_migration_preserves_new_planned_catboost_identity(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry.sqlite"
    config = {
        "experiment_id": "V061_PLANNED_CATBOOST",
        "model_family": "CATBOOST",
        "target_type": "MULTICLASS",
        "feature_universe": "CORE",
        "training_window": "2Y",
        "time_decay": "NONE",
        "calibration": "NONE",
        "league_scope": "GLOBAL",
        "hyperparameters": {"iterations": 2},
    }
    first_registry = ExperimentRegistry(registry_path)
    try:
        planned = first_registry.insert_planned(config, "dataset-v061", "commit-v061")
        assert planned["status"] == "PLANNED"
    finally:
        first_registry.close()

    reopened_registry = ExperimentRegistry(registry_path)
    try:
        reopened = reopened_registry.get("V061_PLANNED_CATBOOST")
        assert reopened is not None
        assert reopened["requested_model_family"] == "CATBOOST"
        assert reopened["effective_model_family"] is None
        assert reopened["requested_feature_universe"] == "CORE"
        assert reopened["effective_feature_universe"] is None
    finally:
        reopened_registry.close()


class _DailyIndexClient:
    def __init__(self) -> None:
        self.calls = 0
        self._lock = threading.Lock()

    def fetch_json(self, endpoint: str) -> FotMobFetchResult:
        with self._lock:
            self.calls += 1
        time.sleep(0.04)
        return FotMobFetchResult(success=True, payload={})


def _worker_settings(tmp_path: Path) -> Settings:
    return Settings(
        root_dir=tmp_path,
        fotmob_enabled=True,
        fotmob_history_enabled=True,
        fotmob_network_mode="worker",
        fotmob_provider_decision="PRODUCTION_READY",
        fotmob_automated_usage="ACCEPTABLE_FOR_PROJECT",
        fotmob_daily_index_cache_ttl_seconds=60,
        fotmob_negative_resolve_no_candidate_ttl_seconds=60,
        fotmob_negative_resolve_ambiguous_ttl_seconds=60,
        fotmob_negative_resolve_no_data_ttl_seconds=60,
    )


def test_daily_index_singleflight_allows_one_refresh(tmp_path: Path) -> None:
    settings = _worker_settings(tmp_path)
    database = Database(settings.database_path)
    client = _DailyIndexClient()
    pipeline = FotMobHistoryPipeline(settings, database, client=client)
    try:
        with ThreadPoolExecutor(max_workers=20) as executor:
            results = list(
                executor.map(
                    lambda _: pipeline.load_daily_fixture_index(
                        "2026-08-22", allow_network=True
                    ),
                    range(20),
                )
            )
        assert all(records == [] and error is None for records, error in results)
        metrics = pipeline.runtime_metrics()
        assert client.calls == 1
        assert metrics["daily_index_network_requests"] == 1
        assert metrics["daily_index_refreshes"] == 1
        assert metrics["daily_index_singleflight_waiters"] >= 1
    finally:
        database.close()


class _EmptyFotMobClient:
    def __init__(self) -> None:
        self.fetch_json_calls = 0
        self._lock = threading.Lock()

    def fetch_json(self, endpoint: str) -> FotMobFetchResult:
        with self._lock:
            self.fetch_json_calls += 1
        return FotMobFetchResult(success=True, payload={})


def _resolver_event(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "event_id": "v061-negative-event",
        "kickoff_time": "2026-08-22T16:30:00+00:00",
        "competition_id": "v061-test-league",
        "competition_name": "V061 Test League",
        "competition_country": "Testland",
        "home_team": "Alpha",
        "away_team": "Beta",
        "home_team_id": "a",
        "away_team_id": "b",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_negative_resolver_cache_and_material_change(tmp_path: Path) -> None:
    settings = _worker_settings(tmp_path)
    database = Database(settings.database_path)
    client = _EmptyFotMobClient()
    service = FotMobService(settings, database, client=client)
    event = _resolver_event()
    try:
        first = service.resolver.resolve(event)
        assert first.cache_state in {"NO_CANDIDATE", "NO_DATA"}
        for _ in range(19):
            cached = service.resolver.resolve(event)
            assert cached.from_cache is True
        metrics = service.runtime_metrics()
        assert metrics["resolver_attempts"] == 1
        assert metrics["resolver_negative_cache_hits"] == 19
        assert client.fetch_json_calls == 3  # the -1/0/+1 date window, once

        changed = copy.copy(event)
        changed.kickoff_time = (
            datetime.fromisoformat(event.kickoff_time) + timedelta(hours=2)
        ).isoformat()
        service.resolver.resolve(changed)
        metrics = service.runtime_metrics()
        assert metrics["resolver_attempts"] == 2
        assert metrics["resolver_negative_cache_invalidations"] >= 1
    finally:
        database.close()


def test_confirmed_link_uses_fast_path_without_candidate_scan(tmp_path: Path) -> None:
    settings = _worker_settings(tmp_path)
    database = Database(settings.database_path)
    client = _EmptyFotMobClient()
    service = FotMobService(settings, database, client=client)
    event = _resolver_event(event_id="v061-confirmed-event")
    match = FotMobMatch(
        provider_match_id="9001",
        kickoff_at=event.kickoff_time,
        competition_id="54",
        competition_name=event.competition_name,
        competition_country=event.competition_country,
        home_team=event.home_team,
        away_team=event.away_team,
    )
    try:
        service.confirm_manual(event, match)
        result = service.resolver.resolve(event)
        assert result.from_cache is True
        assert result.cache_state == "CONFIRMED"
        metrics = service.runtime_metrics()
        assert metrics["confirmed_link_fast_path"] == 1
        assert metrics["resolver_attempts"] == 0
        assert metrics["resolver_candidate_scans"] == 0
        assert client.fetch_json_calls == 0
    finally:
        database.close()
