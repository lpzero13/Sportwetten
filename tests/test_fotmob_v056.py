from __future__ import annotations

from datetime import date
from pathlib import Path

from config import Settings
from fotmob.history_pipeline import FotMobHistoryPipeline
from fotmob.models import FotMobFetchResult
from fotmob.rate_control import AdaptiveRateController, RateControlConfig
from storage.database import Database


class ProbeClient:
    def __init__(self) -> None:
        self.requests = 0
        self.successes = 0

    def fetch_json(self, endpoint: str) -> FotMobFetchResult:
        self.requests += 1
        self.successes += 1
        if "allLeagues" in endpoint:
            return FotMobFetchResult(
                success=True,
                payload={"countries": [{"ccode": "TST", "name": "Testland", "leagues": [{"id": "league-1", "name": "Testliga"}]}]},
            )
        day = endpoint.split("date=")[-1][:8]
        return FotMobFetchResult(
            success=True,
            payload={
                "leagues": [
                    {
                        "primaryId": "league-1",
                        "ccode": "TST",
                        "localizedName": "Testliga",
                        "matches": [
                            {
                                "matchId": f"match-{day}",
                                "matchTimeUTC": f"{day[:4]}-{day[4:6]}-{day[6:8]}T16:00:00Z",
                                "homeTeam": {"id": "home", "name": "Heim"},
                                "awayTeam": {"id": "away", "name": "Gast"},
                                "status": "finished",
                            }
                        ],
                    }
                ]
            },
        )

    def fetch_match_details(self, provider_match_id: str) -> FotMobFetchResult:
        self.requests += 1
        self.successes += 1
        return FotMobFetchResult(
            success=True,
            match=object(),
            response_time_ms=1,
            payload_size=128,
            attempts=1,
        )

    def metrics_snapshot(self) -> dict[str, int]:
        return {"requests": self.requests, "successes": self.successes}


def test_adaptive_rate_controller_ramps_and_backs_off() -> None:
    controller = AdaptiveRateController(
        RateControlConfig(
            mode="ADAPTIVE",
            initial_rps=5,
            rps_step=5,
            min_rps=0.5,
            max_rps=30,
            stable_window_requests=2,
            cooldown_seconds=0,
        )
    )
    assert controller.record(success=True, status_code=200, elapsed_ms=10) is None
    transition = controller.record(success=True, status_code=200, elapsed_ms=10)
    assert transition is not None
    assert transition["action"] == "RAMP_UP"
    assert controller.current_rps == 10

    controller.record(success=False, status_code=429, elapsed_ms=10)
    transition = controller.record(success=True, status_code=200, elapsed_ms=10)
    assert transition is not None
    assert transition["action"] == "BACKOFF"
    assert transition["status"] == "UNSTABLE"
    assert controller.current_rps == 5


def test_rate_controller_benchmark_ceiling_is_temporary() -> None:
    controller = AdaptiveRateController(
        RateControlConfig(mode="FIXED", initial_rps=5, max_rps=30)
    )
    controller.set_mode("FIXED", rps=100)
    assert controller.current_rps == 30
    controller.set_max_rps_override(100, reason="test")
    controller.set_mode("FIXED", rps=100)
    assert controller.current_rps == 100
    assert controller.snapshot()["configured_max_rps"] == 30
    controller.set_max_rps_override(None, reason="test_complete")
    assert controller.current_rps == 30


def test_old_half_rps_environment_alias_cannot_restore_historical_throttle(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("FOTMOB_HISTORY_REQUESTS_PER_SECOND", "0.5")
    monkeypatch.delenv("FOTMOB_INITIAL_RPS", raising=False)
    settings = Settings.from_env(tmp_path)
    assert settings.fotmob_initial_rps == 5.0
    assert settings.fotmob_history_requests_per_second == 5.0


def test_v056_probe_persists_rps_and_worker_profiles(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "tipico.db")
    settings = Settings(
        root_dir=tmp_path,
        fotmob_enabled=True,
        fotmob_history_enabled=True,
        fotmob_network_mode="manual",
        fotmob_rate_mode="FIXED",
        fotmob_initial_rps=5.0,
        fotmob_rps_step=5.0,
        fotmob_max_rps=10.0,
        fotmob_initial_workers=1,
        fotmob_max_workers=2,
        fotmob_performance_requests_per_level=2,
        fotmob_performance_worker_levels=(1, 2),
        fotmob_performance_stable_confirmations=1,
    )
    pipeline = FotMobHistoryPipeline(settings, database, client=ProbeClient())

    result = pipeline.run_performance_probe(
        date(2026, 8, 26),
        date(2026, 8, 28),
        requests_per_level=2,
        worker_levels=(1, 2),
    )

    assert result["status"] == "PASS"
    assert result["max_tested_rps"] == 10.0
    assert result["max_stable_rps"] == 10.0
    assert result["recommended_workers"] in {1, 2}
    assert database.count_rows("fotmob_performance_profile") == 4
    assert pipeline.store.known_stable_max_rps(confirmations=1) == 10.0
    database.close()


def test_v0561_max_probe_uses_exact_three_day_fixture_and_persists_profiles(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "data" / "tipico.db")
    settings = Settings(
        root_dir=tmp_path,
        fotmob_enabled=True,
        fotmob_history_enabled=True,
        fotmob_network_mode="manual",
        fotmob_rate_mode="FIXED",
        fotmob_initial_rps=5.0,
        fotmob_rps_step=5.0,
        fotmob_max_rps=10.0,
        fotmob_initial_workers=1,
        fotmob_max_workers=2,
        fotmob_performance_worker_levels=(1, 2),
        fotmob_performance_stable_confirmations=1,
    )
    pipeline = FotMobHistoryPipeline(settings, database, client=ProbeClient())

    result = pipeline.run_max_throughput_probe(
        date(2026, 8, 26),
        date(2026, 8, 28),
        requests_per_level=2,
        critical_requests=2,
        max_target_rps=40,
        worker_levels=(1, 2),
    )

    assert result["status"] == "PASS"
    assert result["max_tested_target_rps"] == 40.0
    assert result["max_stable_target_rps"] == 40.0
    assert result["higher_rps_probe"] is True
    assert result["bottleneck"] in {
        "RATE_CONTROLLER",
        "REQUEST_SCHEDULING",
        "CONNECTION_POOL",
        "NETWORK",
        "PROVIDER",
        "CPU",
        "PARSER",
        "SQLITE",
        "PARQUET",
        "UNKNOWN",
    }
    assert database.count_rows("fotmob_performance_profile") == 6
    database.close()
