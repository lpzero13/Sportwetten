from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace

from config import Settings
from fotmob.parser import parse_fotmob_payload
from fotmob.service import FotMobService
from runtime_status import feature_runtime_matrix, runtime_warnings
from services.collector import Collector
from services.event_service import EventRefreshResult, EventService
from storage.database import Database
from storage.raw_storage import RawStorage
from tipico.parser import parse_event_details, parse_live_feed

from tests.test_collector import FakeCollectorClient, load_fixture
from tests.test_fotmob import FakeFotMobClient, sample_payload, tipico_event


def _matrix_item(settings: Settings, feature: str) -> dict:
    return next(
        item for item in feature_runtime_matrix(settings) if item["feature"] == feature
    )


def test_configured_but_blocked_fotmob_gate_is_machine_readable(tmp_path: Path) -> None:
    settings = Settings(
        root_dir=tmp_path,
        fotmob_enabled=True,
        fotmob_history_enabled=True,
        fotmob_network_mode="manual",
        fotmob_provider_decision="LIMITED_USE",
        fotmob_automated_usage="UNCLEAR",
    )

    daily = _matrix_item(settings, "fotmob_daily_index")
    auto_link = _matrix_item(settings, "fotmob_auto_link")

    assert daily["configured_enabled"] is True
    assert daily["effective_enabled"] is False
    assert daily["blocking_gate"] == "FOTMOB_NETWORK_MODE"
    assert auto_link["effective_enabled"] is False
    assert any("fotmob_auto_link" in warning for warning in runtime_warnings(feature_runtime_matrix(settings)))


def test_collector_status_marks_blocked_configured_feature_degraded(tmp_path: Path) -> None:
    settings = Settings(
        root_dir=tmp_path,
        store_raw_responses=False,
        fotmob_network_mode="manual",
        fotmob_provider_decision="LIMITED_USE",
        fotmob_automated_usage="UNCLEAR",
    )
    database = Database(settings.database_path)
    client = FakeCollectorClient(load_fixture("live_feed.json"), {})
    fotmob = FotMobService(
        settings,
        database,
        client=FakeFotMobClient(None, fail=True),
    )
    collector = Collector(
        client,  # type: ignore[arg-type]
        database,
        RawStorage(settings.raw_storage_path, enabled=False),
        settings,
        client_factory=lambda: client,  # type: ignore[arg-type]
        fotmob_service=fotmob,
    )

    status = collector.status(force_refresh=True)

    assert status["status"] == "COMPLETED_DEGRADED"
    assert status["runtime_warnings"]
    assert status["startup_runtime_warnings"] == status["runtime_warnings"]
    auto_link = next(
        item for item in status["feature_runtime_matrix"] if item["feature"] == "fotmob_auto_link"
    )
    assert auto_link["configured_enabled"] is True
    assert auto_link["effective_enabled"] is False
    assert status["feature_health"]["fotmob_auto_link"]["status"] == "DEGRADED"
    database.close()


def test_production_defaults_enable_integrated_fotmob_path(tmp_path: Path) -> None:
    settings = Settings(root_dir=tmp_path)

    assert settings.fotmob_enabled is True
    assert settings.fotmob_network_mode == "worker"
    assert settings.fotmob_provider_decision == "PRODUCTION_READY"
    assert settings.fotmob_automated_usage == "ACCEPTABLE_FOR_PROJECT"
    assert _matrix_item(settings, "fotmob_daily_index")["effective_enabled"] is True
    assert _matrix_item(settings, "fotmob_auto_link")["effective_enabled"] is True
    assert _matrix_item(settings, "fotmob_ht_enrichment")["effective_enabled"] is True
    assert runtime_warnings(feature_runtime_matrix(settings)) == []


def test_clean_non_soccer_live_feed_without_soccer_bucket_is_accepted(tmp_path: Path) -> None:
    """Tipico may return a valid live response containing only another sport."""

    settings = Settings(root_dir=tmp_path, store_raw_responses=False)
    database = Database(settings.database_path)
    payload = copy.deepcopy(load_fixture("live_feed.json"))
    event = payload["LIVE"]["events"]["721621110"]
    event["sport"] = "tennis"
    payload["LIVE"]["eventsBySport"] = {"tennis": ["721621110"]}
    client = FakeCollectorClient(payload, {})
    service = EventService(
        client,  # type: ignore[arg-type]
        database,
        RawStorage(settings.raw_storage_path, enabled=False),
        settings,
    )

    result = service.refresh()

    assert result.success is True
    assert result.events == []
    assert service.plausibility_error_count == 0
    database.close()


def test_collector_status_contains_identity_gates_and_outbox_contract(tmp_path: Path) -> None:
    settings = Settings(root_dir=tmp_path, store_raw_responses=False)
    database = Database(settings.database_path)
    client = FakeCollectorClient(load_fixture("live_feed.json"), load_fixture("event_detail.json"))
    collector = Collector(
        client,  # type: ignore[arg-type]
        database,
        RawStorage(settings.raw_storage_path, enabled=False),
        settings,
        client_factory=lambda: client,  # type: ignore[arg-type]
    )

    status = collector.status(force_refresh=True)

    assert status["app_version"] == "0.5.9.1"
    assert status["config_fingerprint"].startswith("sha256:")
    assert "git_commit" in status and "git_branch" in status
    assert "feature_runtime_matrix" in status
    assert "feature_health" in status
    assert status["outbox_pending"] == 0
    assert status["oldest_outbox_age_seconds"] is None
    assert status["validation_canary"]["CT110_LIVE_CANARY"] == "PENDING"
    assert status["deployment_consistency"]["separate_fotmob_worker_effective"] is False
    database.close()


def test_fotmob_runtime_counters_distinguish_no_halftime_data(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "tipico.db")
    payload = copy.deepcopy(sample_payload())
    del payload["content"]["stats"]["periods"]["1"]
    match = parse_fotmob_payload(payload)
    service = FotMobService(
        Settings(root_dir=tmp_path, fotmob_min_request_interval_seconds=0),
        database,
        client=FakeFotMobClient(match),
    )
    event = tipico_event()

    assert service.match_tipico_event(event, [match]).status == "EXACT"
    result = service.refresh_for_tipico_event(event, snapshot_type="HALFTIME")
    metrics = service.runtime_metrics()

    assert result.success is True
    assert result.ht_stats_available is False
    assert metrics["link_attempts"] == 1
    assert metrics["links_exact"] == 1
    assert metrics["detail_requests"] == 1
    assert metrics["detail_errors"] == 0
    assert metrics["ht_attempts"] == 1
    assert metrics["ht_success"] == 0
    assert metrics["ht_no_data"] == 1
    assert metrics["ht_errors"] == 0
    assert service.metrics()["enhanced_ml_allowed_count"] == 0
    readiness = service.ml_ht_readiness_for_event(event)
    assert readiness["fotmob_ht_status"] == "NO_HALFTIME"
    assert readiness["enhanced_ml_allowed"] is False
    database.close()


def test_ml_ht_readiness_exposes_no_link_without_inventing_provider_data(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "tipico.db")
    service = FotMobService(
        Settings(root_dir=tmp_path),
        database,
        client=FakeFotMobClient(None, fail=True),
    )

    readiness = service.ml_ht_readiness_for_event(tipico_event())

    assert readiness["link_status"] is None
    assert readiness["fotmob_match_id"] is None
    assert readiness["fotmob_ht_status"] == "NO_LINK"
    assert readiness["enhanced_ml_allowed"] is False
    database.close()


def test_halftime_disappearance_and_recovery_are_counted_without_grace_window(tmp_path: Path) -> None:
    live = parse_live_feed(load_fixture("live_feed.json"))[0]
    live.period = "HALF_TIME"
    live.display_minute = "HZ"
    event_id = str(live.event_id)

    class FakeEventService:
        def __init__(self) -> None:
            self.results = [
                EventRefreshResult(True, [live]),
                EventRefreshResult(True, [], reconciled_event_ids=(event_id,)),
                EventRefreshResult(True, [live]),
            ]
            self.repository = SimpleNamespace()

        def refresh(self) -> EventRefreshResult:
            return self.results.pop(0)

    settings = Settings(
        root_dir=tmp_path,
        store_raw_responses=False,
        collector_retry_delays_seconds=(),
        snapshot_ht_enabled=False,
    )
    database = Database(settings.database_path)
    client = FakeCollectorClient(load_fixture("live_feed.json"), load_fixture("event_detail.json"))
    collector = Collector(
        client,  # type: ignore[arg-type]
        database,
        RawStorage(settings.raw_storage_path, enabled=False),
        settings,
        event_service=FakeEventService(),  # type: ignore[arg-type]
        client_factory=lambda: client,  # type: ignore[arg-type]
    )

    collector._poll_feed()
    collector._poll_feed()
    collector._poll_feed()

    assert collector.halftime_disappearance_count == 1
    assert collector.halftime_recovery_count == 1
    assert collector.status()["halftime_missing_events"] == 0
    database.close()


def test_v03_public_analysis_path_persists_current_state_and_tracks_sql(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "tipico.db")
    details = parse_event_details(load_fixture("event_detail.json"))
    settings = Settings(root_dir=tmp_path, store_raw_responses=False)
    client = FakeCollectorClient(load_fixture("live_feed.json"), load_fixture("event_detail.json"))
    collector = Collector(
        client,  # type: ignore[arg-type]
        database,
        RawStorage(settings.raw_storage_path, enabled=False),
        settings,
        client_factory=lambda: client,  # type: ignore[arg-type]
    )

    with database.trace_sql() as sql_metrics:
        analysis, accepted = collector._update_current_state(
            details,
            "2026-08-29T10:00:00+00:00",
        )

    assert accepted is True
    assert analysis is not None
    assert sql_metrics["statements"] > 0
    assert database.count_rows("current_canonical_outcomes") > 0
    assert database.count_rows("current_strategy_evaluations") == 1
    database.close()


def test_deployment_keeps_one_integrated_fotmob_worker(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    install = (root / "deploy" / "install_proxmox.sh").read_text(encoding="utf-8")
    activate = (root / "deploy" / "activate_fotmob.sh").read_text(encoding="utf-8")
    env = (root / "deploy" / "tipico-observer.env.example").read_text(encoding="utf-8")

    assert "ExecStart" in (root / "deploy" / "wetten-collector.service").read_text(encoding="utf-8")
    assert "disable --now wetten-fotmob.service" in install
    assert "disable --now wetten-fotmob.service" in activate
    assert "FOTMOB_NETWORK_MODE worker" in activate
    assert "FOTMOB_PROVIDER_DECISION PRODUCTION_READY" in activate
    assert "FOTMOB_AUTOMATED_USAGE ACCEPTABLE_FOR_PROJECT" in activate
    assert "FOTMOB_NETWORK_MODE=worker" in env
    assert "FOTMOB_PROVIDER_DECISION=PRODUCTION_READY" in env
    assert "FOTMOB_AUTOMATED_USAGE=ACCEPTABLE_FOR_PROJECT" in env
    assert "/var/log/wetten/tipico.log" in (root / "deploy" / "wetten.logrotate").read_text(encoding="utf-8")
    assert "install -d -o \"$SERVICE_USER\" -g \"$SERVICE_GROUP\" -m 0750 /var/log/wetten" in install
