from datetime import datetime, timedelta, timezone
from pathlib import Path
import copy

import pytest

from scripts.local_runtime import service_for, lock, fresh, health
from services.stop_request import StopRequest
from ui.analysis_summary import analysis_summary
from ui.components import text
from intelligence.service import MarketIntelligenceService
from tests.test_market_intelligence import make_details, make_market


NOW = datetime.now(timezone.utc)


def example():
    details = make_details([
        make_market("m05", "points-more-less-rest", "2:0.5", [("u05", "-", 3.8, "-", True), ("o05", "+", 1.29, "+", True)]),
        make_market("m15", "points-more-less-rest", "2:1.5", [("u15", "-", 1.55, "-", True), ("o15", "+", 2.2, "+", True)]),
    ])
    service = MarketIntelligenceService()
    analysis = service.analyze(details, observed_at=NOW.isoformat(), now=NOW, persist=False)
    return details, analysis, service


@pytest.mark.parametrize("service,script", [("collector", "scripts/run_collector.py"), ("paper", "scripts/run_paper.py")])
def test_process_ownership_requires_exact_script_and_root(tmp_path, service, script):
    command = ["python.exe", str(tmp_path / script), "--root", str(tmp_path)]
    assert service_for(command, str(tmp_path), tmp_path) == service
    assert service_for(command[:-1] + [str(tmp_path / "other")], str(tmp_path), tmp_path) is None
    assert service_for(["python.exe", str(tmp_path / (script + ".backup"))], str(tmp_path), tmp_path) is None
    assert service_for(["python.exe", "-c", str(tmp_path / script)], str(tmp_path), tmp_path) is None


def test_ui_process_is_exact_and_relative_worker_resolves_cwd(tmp_path):
    assert service_for(["python", "-m", "streamlit", "run", str(tmp_path / "app.py")], str(tmp_path), tmp_path) == "ui"
    assert service_for(["python", "app.py"], str(tmp_path), tmp_path) is None
    assert service_for(["python", "scripts/run_paper.py"], str(tmp_path), tmp_path) == "paper"
    assert service_for(["python", "other.py", "scripts/run_paper.py"], str(tmp_path), tmp_path) is None
    assert service_for(["python", "-u", "scripts/run_paper.py"], str(tmp_path), tmp_path) == "paper"


def test_singleton_lock_releases_even_after_exception(tmp_path):
    path = tmp_path / "owner.lock"
    with lock(path):
        with pytest.raises(OSError):
            with lock(path):
                pass
    with lock(path):
        pass


def test_readonly_status_does_not_create_database(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.local_runtime.ui_ready", lambda _: False)
    state = health(tmp_path, 9999)
    assert not state["ready"]
    assert not (tmp_path / "data/tipico.db").exists()


def test_stop_request_is_opt_in(tmp_path, monkeypatch):
    monkeypatch.delenv("WETTEN_STOP_FILE", raising=False)
    assert not StopRequest().is_set()
    path = tmp_path / "stop"
    monkeypatch.setenv("WETTEN_STOP_FILE", str(path))
    request = StopRequest()
    assert not request.is_set()
    path.touch()
    assert request.wait(1)


def test_health_never_calls_future_or_unknown_timestamps_fresh():
    assert not fresh(None)
    assert not fresh((datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat())
    assert fresh(datetime.now(timezone.utc).isoformat())


def test_foreign_port_is_rejected_without_stopping_it(tmp_path):
    import socket
    from scripts.local_runtime import start
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        with pytest.raises(RuntimeError, match="andere Anwendung"):
            start(tmp_path, port, False, .1)
        assert listener.fileno() != -1


def test_command_lock_waits_for_simultaneous_start(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import time
    def wait_for_owner():
        with lock(tmp_path / "start.lock", wait_seconds=2):
            return True
    with ThreadPoolExecutor(max_workers=1) as pool:
        with lock(tmp_path / "start.lock"):
            future = pool.submit(wait_for_owner)
            time.sleep(.15)
            assert not future.done()
        assert future.result(timeout=2)


def test_summary_math_reconciles_and_keeps_source_snapshot():
    details, analysis, _ = example()
    before = copy.deepcopy(analysis)
    model = analysis_summary(details, analysis, stake=30, max_age=10, now=NOW)
    strategy = model["strategy"]
    assert model["tone"] == "negative"
    assert strategy.stake_zero + strategy.stake_two_plus == 30
    assert strategy.payout_zero == pytest.approx(41.8)
    assert model["ev"] == pytest.approx(analysis.probability.p0 * strategy.payout_zero + analysis.probability.p2_plus * strategy.payout_two_plus - 30)
    assert analysis == before


@pytest.mark.parametrize("seconds", [11, -1])
def test_stale_or_future_snapshot_is_never_an_entry_label(seconds):
    details, analysis, _ = example()
    model = analysis_summary(details, analysis, stake=100, max_age=10, now=NOW + timedelta(seconds=seconds))
    assert model["stale"]
    assert model["title"] == "Quoten aktualisieren"


def test_partial_unknown_and_nonfinite_data_are_explicit():
    details, analysis, _ = example()
    analysis.probability.status = "MISSING_PAIRS"
    model = analysis_summary(details, analysis, stake=30, max_age=10, now=NOW)
    assert model["ev"] is None
    assert model["tone"] == "neutral"
    analysis.strategy.q_zero = float("nan")
    analysis_summary(details, analysis, stake=30, max_age=10, now=NOW)
    details.event.extra_time = None
    assert analysis_summary(details, analysis, stake=30, max_age=10, now=NOW)["title"] == "Spielzeit nicht eindeutig"


def test_summary_labels_remaining_time_after_halftime():
    details, analysis, _ = example()
    details.event.period, details.event.display_minute = "LIVE", "60"
    assert not analysis_summary(details, analysis, stake=30, max_age=10, now=NOW)["halftime"]


def test_html_escapes_provider_labels():
    assert text('<img src=x onerror="alert(1)">') == '&lt;img src=x onerror=&quot;alert(1)&quot;&gt;'


def test_analysis_ui_stake_changes_without_network_or_persistence():
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_string('''
from tests.test_local_runtime_ui import example
from ui.analysis import render_market_analysis
from ui.device import apply_responsive_style
apply_responsive_style()
details, analysis, service = example()
def forbidden(*args, **kwargs):
    raise AssertionError("The UI must not re-fetch or persist stake scenarios")
service.analyze = forbidden
render_market_analysis(details, analysis, service)
''').run(timeout=20)
    assert not app.exception
    assert any("w-scenario" in element.value for element in app.markdown)
    app.number_input(key="analysis-stake-event-1").set_value(100).run()
    assert not app.exception
    assert any("100,00 €" in element.value for element in app.markdown)
    assert [e.label for e in app.expander] == ["Quellen & Marktvergleich", "Rechenweg & Datenqualität"]
