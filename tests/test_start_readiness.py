"""The desktop UI may open before the first collector cycle finishes."""
from unittest.mock import Mock

import pytest

from scripts import local_runtime as runtime


@pytest.mark.parametrize("ready", [False, True])
def test_browser_opens_when_ui_ready_not_when_collector_finishes(tmp_path, monkeypatch, ready):
    monkeypatch.setattr(runtime, "services", lambda _: {name: [] for name in runtime.ENTRYPOINTS})
    monkeypatch.setattr(runtime, "spawn", Mock())
    state = {"ready": ready, "ui_ready": True, "url": "http://127.0.0.1:8506",
             "services": {"ui": [1], "collector": [2], "paper": [3]}}
    monkeypatch.setattr(runtime, "health", lambda *a: state)
    open_browser = Mock(return_value=True)
    monkeypatch.setattr(runtime.webbrowser, "open", open_browser)
    # No actual port, browser, worker or production database is used.
    assert runtime.start(tmp_path, 0, True, .1) == 0
    open_browser.assert_called_once_with(state["url"])
    assert state["ready"] is ready  # Never relabel pending workers as healthy.


def test_no_browser_option_is_respected(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "services", lambda _: {name: [] for name in runtime.ENTRYPOINTS})
    monkeypatch.setattr(runtime, "spawn", Mock())
    monkeypatch.setattr(runtime, "health", lambda *a: {"ready": True, "ui_ready": True, "url": "local"})
    browser = Mock()
    monkeypatch.setattr(runtime.webbrowser, "open", browser)
    assert runtime.start(tmp_path, 0, False, .1) == 0
    browser.assert_not_called()


def test_browser_failure_reports_url_without_killing_services(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(runtime, "services", lambda _: {name: [] for name in runtime.ENTRYPOINTS})
    monkeypatch.setattr(runtime, "spawn", Mock())
    monkeypatch.setattr(runtime, "health", lambda *a: {"ready": True, "ui_ready": True, "url": "http://127.0.0.1:8506"})
    monkeypatch.setattr(runtime.webbrowser, "open", Mock(side_effect=OSError("No browser")))
    assert runtime.start(tmp_path, 0, True, .1) == 0
    assert "Bitte öffnen: http://127.0.0.1:8506" in capsys.readouterr().out
