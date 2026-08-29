from __future__ import annotations

from pathlib import Path

from config import Settings
from tipico.client import TipicoClient


class FakeResponse:
    status_code = 200
    content = b'{"ok": true}'
    url = "https://sports.tipico.de/fake"

    def json(self) -> dict:
        return {"LIVE": {"events": {}}}

    def raise_for_status(self) -> None:
        return None


class FakeSession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.calls: list[tuple[str, dict[str, str], int]] = []

    def get(self, url: str, *, params: dict[str, str], timeout: int) -> FakeResponse:
        self.calls.append((url, params, timeout))
        return FakeResponse()

    def close(self) -> None:
        return None


def test_client_uses_verified_paths_and_public_context(tmp_path: Path) -> None:
    session = FakeSession()
    settings = Settings(root_dir=tmp_path)
    client = TipicoClient(settings, session=session)

    client.get_live_football_events()
    client.get_event_details("721621110")

    live_url, live_params, live_timeout = session.calls[0]
    detail_url, detail_params, _ = session.calls[1]
    assert live_url.endswith("/v1/tpapi/programgateway/program/events/live")
    assert live_params["selectedGroupIds"] == "1101"
    assert live_params["regionTreeSport"] == "1101"
    assert live_params["isLoggedIn"] == "0"
    assert live_params["licenseRegion"] == "DE"
    assert live_params["language"] == "de"
    assert live_params["maxMarkets"] == "1"
    assert detail_url.endswith("/v1/tpapi/programgateway/program/events/721621110")
    assert detail_params == {
        "language": "de",
        "isLoggedIn": "0",
        "licenseRegion": "DE",
    }
    assert live_timeout == 10
    assert session.headers["Accept"] == "application/json"


def test_client_uses_verified_upcoming_hour_events_path(tmp_path: Path) -> None:
    session = FakeSession()
    client = TipicoClient(Settings(root_dir=tmp_path), session=session)

    client.get_upcoming_football_events("today")

    url, params, _ = session.calls[0]
    assert url.endswith(
        "/v1/tpapi/programgateway/program/events/hourEvents/today"
    )
    assert params["selectedGroupIds"] == "1101"
    assert params["regionTreeSport"] == "1101"
    assert params["maxMarkets"] == "1"
