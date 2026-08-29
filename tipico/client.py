"""HTTP client for the public Tipico Program Gateway endpoints."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import requests

from config import Settings


@dataclass(frozen=True, slots=True)
class RequestMetrics:
    endpoint: str
    method: str
    request_started_at: str
    response_received_at: str
    response_time_ms: int
    status_code: int | None
    payload_size: int


@dataclass(frozen=True, slots=True)
class ApiResponse:
    payload: dict[str, Any]
    metrics: RequestMetrics


class TipicoApiError(RuntimeError):
    """Raised when Tipico cannot return a valid JSON response."""

    def __init__(
        self,
        message: str,
        *,
        metrics: RequestMetrics | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.metrics = metrics
        self.status_code = status_code


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class TipicoClient:
    """Small, reusable, read-only client for the verified Tipico REST API."""

    LIVE_EVENTS_PATH = "/v1/tpapi/programgateway/program/events/live"
    UPCOMING_EVENTS_PATH = "/v1/tpapi/programgateway/program/events/hourEvents/{upcoming_time}"
    EVENT_DETAILS_PATH = "/v1/tpapi/programgateway/program/events/{event_id}"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        session: requests.Session | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.settings = settings or Settings.from_env()
        self.logger = logger or logging.getLogger("tipico")
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/139.0.0.0 Safari/537.36"
                ),
            }
        )
        self.last_metrics: RequestMetrics | None = None

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "TipicoClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _request_json(self, path: str, params: dict[str, str]) -> ApiResponse:
        url = f"{self.settings.tipico_base_url}{path}"
        request_started_at = _now_iso()
        started = time.perf_counter()
        response: requests.Response | None = None
        try:
            response = self.session.get(
                url,
                params=params,
                timeout=self.settings.request_timeout_seconds,
            )
            response_time_ms = int((time.perf_counter() - started) * 1000)
            response_received_at = _now_iso()
            metrics = RequestMetrics(
                endpoint=response.url,
                method="GET",
                request_started_at=request_started_at,
                response_received_at=response_received_at,
                response_time_ms=response_time_ms,
                status_code=response.status_code,
                payload_size=len(response.content),
            )
            self.last_metrics = metrics
            response.raise_for_status()
            try:
                payload = response.json()
            except ValueError as exc:
                raise TipicoApiError(
                    f"Tipico returned non-JSON data for {path}",
                    metrics=metrics,
                    status_code=response.status_code,
                ) from exc
            if not isinstance(payload, dict):
                raise TipicoApiError(
                    f"Tipico returned an unexpected JSON root for {path}",
                    metrics=metrics,
                    status_code=response.status_code,
                )
            return ApiResponse(payload=payload, metrics=metrics)
        except requests.RequestException as exc:
            response_time_ms = int((time.perf_counter() - started) * 1000)
            metrics = RequestMetrics(
                endpoint=response.url if response is not None else url,
                method="GET",
                request_started_at=request_started_at,
                response_received_at=_now_iso(),
                response_time_ms=response_time_ms,
                status_code=response.status_code if response is not None else None,
                payload_size=len(response.content) if response is not None else 0,
            )
            self.last_metrics = metrics
            status = f"HTTP {metrics.status_code}" if metrics.status_code else "network error"
            raise TipicoApiError(
                f"Tipico request failed for {path}: {status}",
                metrics=metrics,
                status_code=metrics.status_code,
            ) from exc

    def get_live_football_events(self) -> ApiResponse:
        """Fetch the complete currently offered live-football overview."""

        params = {
            "selectedGroupIds": self.settings.soccer_group_id,
            "regionTreeSport": self.settings.region_tree_sport,
            "isLoggedIn": "0",
            "licenseRegion": self.settings.tipico_license_region,
            "language": self.settings.tipico_language,
            "maxMarkets": "1",
        }
        result = self._request_json(self.LIVE_EVENTS_PATH, params)
        live = result.payload.get("LIVE", {})
        event_count = (
            len(live.get("eventsBySport", {}).get("soccer", []))
            if isinstance(live, dict)
            and isinstance(live.get("eventsBySport"), dict)
            else 0
        )
        self.logger.info(
            "Live feed fetched: %s events in %s ms",
            event_count,
            result.metrics.response_time_ms,
        )
        return result

    def get_event_details(self, event_id: str) -> ApiResponse:
        """Fetch every currently returned market for one event only."""

        resolved_event_id = str(event_id)
        path = self.EVENT_DETAILS_PATH.format(event_id=resolved_event_id)
        params = {
            "language": self.settings.tipico_language,
            "isLoggedIn": "0",
            "licenseRegion": self.settings.tipico_license_region,
        }
        result = self._request_json(path, params)
        self.logger.info(
            "Event detail fetched: event=%s in %s ms (%s bytes)",
            resolved_event_id,
            result.metrics.response_time_ms,
            result.metrics.payload_size,
        )
        return result

    def get_upcoming_football_events(
        self,
        upcoming_time: str = "today",
        *,
        max_markets: int = 1,
    ) -> ApiResponse:
        """Fetch upcoming football events from Tipico's verified hour feed."""

        resolved_time = str(upcoming_time).strip()
        if resolved_time not in {"1", "2", "3", "6", "today", "24", "48", "tomorrow-12"}:
            raise ValueError(f"Unsupported upcoming time filter: {resolved_time}")
        path = self.UPCOMING_EVENTS_PATH.format(upcoming_time=resolved_time)
        params = {
            "selectedGroupIds": self.settings.soccer_group_id,
            "regionTreeSport": self.settings.region_tree_sport,
            "isLoggedIn": "0",
            "licenseRegion": self.settings.tipico_license_region,
            "language": self.settings.tipico_language,
            "maxMarkets": str(max(1, int(max_markets))),
        }
        result = self._request_json(path, params)
        container = result.payload.get("UPCOMING") or result.payload.get("TOMORROW") or {}
        events = container.get("events", {}) if isinstance(container, dict) else {}
        self.logger.info(
            "Upcoming feed fetched: %s events in %s ms",
            len(events) if isinstance(events, dict) else 0,
            result.metrics.response_time_ms,
        )
        return result
