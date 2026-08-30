"""Polite, optional FotMob HTTP client.

The client performs no login, cookie harvesting or aggressive discovery.  It
is a small adapter around one configurable public match-details path and is
disabled by the service unless the deployment explicitly opts in.
"""

from __future__ import annotations

import json
import logging
import math
import re
import statistics
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import requests

from .models import FotMobFetchResult
from .parser import parse_fotmob_payload


class FotMobAccessError(RuntimeError):
    """Raised for an unsuccessful or structurally invalid provider response."""


@dataclass(slots=True)
class FotMobAccessMetrics:
    requests: int = 0
    successes: int = 0
    errors: int = 0
    retries: int = 0
    total_response_ms: int = 0
    last_response_ms: int | None = None
    last_status_code: int | None = None
    last_endpoint: str | None = None
    last_error: str | None = None
    last_request_at: str | None = None
    last_success_at: str | None = None
    response_sizes: list[int] = field(default_factory=list)
    response_times_ms: list[int] = field(default_factory=list)
    status_counts: dict[str, int] = field(default_factory=dict)
    http_failures: int = 0
    rate_limit_responses: int = 0
    parse_failures: int = 0

    @property
    def average_response_ms(self) -> float:
        return self.total_response_ms / self.successes if self.successes else 0.0

    @property
    def average_payload_bytes(self) -> float:
        return sum(self.response_sizes) / len(self.response_sizes) if self.response_sizes else 0.0

    @property
    def median_response_ms(self) -> float:
        return statistics.median(self.response_times_ms) if self.response_times_ms else 0.0

    @property
    def p95_response_ms(self) -> float:
        if not self.response_times_ms:
            return 0.0
        ordered = sorted(self.response_times_ms)
        index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1))
        return float(ordered[index])

    def as_dict(self) -> dict[str, Any]:
        return {
            "requests": self.requests,
            "successes": self.successes,
            "errors": self.errors,
            "retries": self.retries,
            "http_failures": self.http_failures,
            "rate_limit_responses": self.rate_limit_responses,
            "parse_failures": self.parse_failures,
            "average_response_ms": round(self.average_response_ms, 1),
            "median_response_ms": round(self.median_response_ms, 1),
            "p95_response_ms": round(self.p95_response_ms, 1),
            "last_response_ms": self.last_response_ms,
            "last_status_code": self.last_status_code,
            "status_counts": dict(self.status_counts),
            "last_endpoint": self.last_endpoint,
            "last_error": self.last_error,
            "last_request_at": self.last_request_at,
            "last_success_at": self.last_success_at,
            "average_payload_bytes": round(self.average_payload_bytes, 1),
        }


class FotMobClient:
    """Rate-limited client for one match-details response at a time."""

    def __init__(
        self,
        *,
        base_url: str = "https://www.fotmob.com",
        api_base_url: str = "https://www.fotmob.com/api",
        match_details_path: str = "/match/{match_id}",
        timeout_seconds: int = 10,
        max_retries: int = 3,
        min_request_interval_seconds: float = 1.0,
        retry_delays_seconds: tuple[int, ...] = (1, 3, 10),
        session: requests.Session | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_base_url = api_base_url.rstrip("/")
        self.match_details_path = match_details_path
        self.timeout_seconds = max(1, int(timeout_seconds))
        self.max_retries = max(0, int(max_retries))
        self.min_request_interval_seconds = max(0.0, float(min_request_interval_seconds))
        self.retry_delays_seconds = tuple(float(item) for item in retry_delays_seconds) or (1.0,)
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
                "User-Agent": "wetten-fotmob-observer/0.5 (moderate public-page client)",
            }
        )
        self.logger = logger or logging.getLogger("tipico.fotmob")
        self.metrics = FotMobAccessMetrics()
        self._lock = threading.RLock()
        self._last_request_monotonic: float | None = None
        self._last_attempts = 0

    def _url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        root = (
            self.api_base_url
            if path.startswith("/matchDetails")
            or path.startswith("/leagues?id=")
            or path.startswith("/leagues?")
            or path.startswith("/api/")
            else self.base_url
        )
        if root.endswith("/api") and path.startswith("/api/"):
            path = path[4:]
        return f"{root}/{path.lstrip('/')}"

    def _wait_for_rate_limit(self) -> None:
        if self.min_request_interval_seconds <= 0:
            return
        with self._lock:
            now = time.monotonic()
            if self._last_request_monotonic is not None:
                wait = self.min_request_interval_seconds - (now - self._last_request_monotonic)
                if wait > 0:
                    time.sleep(wait)
            self._last_request_monotonic = time.monotonic()

    @staticmethod
    def _embedded_json(text: str) -> dict[str, Any] | None:
        match = re.search(
            r"<script[^>]+id=[\"']__NEXT_DATA__[\"'][^>]*>(.*?)</script>",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not match:
            return None
        try:
            value = json.loads(match.group(1))
        except (TypeError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    def _get_json(self, endpoint: str) -> tuple[dict[str, Any], int, int, int]:
        url = self._url(endpoint)
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self._last_attempts = attempt + 1
            if attempt:
                self.metrics.retries += 1
                delay = self.retry_delays_seconds[min(attempt - 1, len(self.retry_delays_seconds) - 1)]
                time.sleep(delay)
            self._wait_for_rate_limit()
            self.metrics.requests += 1
            self.metrics.last_request_at = datetime.now(timezone.utc).isoformat()
            started = time.perf_counter()
            self.metrics.last_endpoint = url
            try:
                response = self.session.get(url, timeout=self.timeout_seconds)
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                status_code = int(getattr(response, "status_code", 0) or 0)
                content = getattr(response, "content", b"") or b""
                payload_size = len(content)
                self.metrics.last_response_ms = elapsed_ms
                self.metrics.last_status_code = status_code
                self.metrics.response_sizes.append(payload_size)
                self.metrics.response_times_ms.append(elapsed_ms)
                status_key = str(status_code)
                self.metrics.status_counts[status_key] = self.metrics.status_counts.get(status_key, 0) + 1
                if status_code >= 400:
                    self.metrics.http_failures += 1
                    if status_code == 429:
                        self.metrics.rate_limit_responses += 1
                    raise FotMobAccessError(f"FotMob HTTP {status_code}")
                try:
                    payload = response.json()
                except (TypeError, ValueError, json.JSONDecodeError):
                    text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else str(content)
                    payload = self._embedded_json(text)
                if not isinstance(payload, dict):
                    raise FotMobAccessError("FotMob response is not JSON or embedded page data")
                self.metrics.successes += 1
                self.metrics.total_response_ms += elapsed_ms
                self.metrics.last_error = None
                self.metrics.last_success_at = datetime.now(timezone.utc).isoformat()
                return payload, status_code, elapsed_ms, payload_size
            except (requests.RequestException, FotMobAccessError, ValueError, TypeError) as exc:
                last_error = exc
                self.metrics.last_error = str(exc)
                # Retry transient transport and server/rate-limit failures;
                # client-side schema/status failures are not made noisier.
                status = self.metrics.last_status_code or 0
                retryable = isinstance(exc, requests.RequestException) or status == 429 or status >= 500
                if not retryable or attempt >= self.max_retries:
                    break
        self.metrics.errors += 1
        raise FotMobAccessError(str(last_error or "FotMob request failed"))

    def fetch_json(self, endpoint: str) -> FotMobFetchResult:
        """Fetch one configured public endpoint without assuming its payload shape."""

        started = time.perf_counter()
        try:
            payload, status_code, response_ms, payload_size = self._get_json(endpoint)
            return FotMobFetchResult(
                success=True,
                payload=payload,
                status_code=status_code,
                response_time_ms=response_ms,
                payload_size=payload_size,
                endpoint=self._url(endpoint),
                attempts=self._last_attempts,
            )
        except (FotMobAccessError, TypeError, ValueError, KeyError) as exc:
            return FotMobFetchResult(
                success=False,
                status_code=self.metrics.last_status_code,
                response_time_ms=int((time.perf_counter() - started) * 1000),
                endpoint=self._url(endpoint),
                error=str(exc),
                attempts=self._last_attempts,
            )

    def fetch_match_details(self, provider_match_id: str) -> FotMobFetchResult:
        endpoint = self.match_details_path.format(match_id=str(provider_match_id))
        fetched = self.fetch_json(endpoint)
        if not fetched.success or fetched.payload is None:
            return fetched
        try:
            match = parse_fotmob_payload(fetched.payload, provider_match_id=str(provider_match_id))
        except (TypeError, ValueError, KeyError) as exc:
            self.metrics.parse_failures += 1
            self.metrics.errors += 1
            self.metrics.last_error = str(exc)
            return FotMobFetchResult(
                success=False,
                payload=fetched.payload,
                status_code=fetched.status_code,
                response_time_ms=fetched.response_time_ms,
                payload_size=fetched.payload_size,
                endpoint=fetched.endpoint,
                error=str(exc),
                attempts=fetched.attempts,
            )
        return FotMobFetchResult(
            success=True,
            match=match,
            payload=fetched.payload,
            status_code=fetched.status_code,
            response_time_ms=fetched.response_time_ms,
            payload_size=fetched.payload_size,
            endpoint=fetched.endpoint,
            attempts=fetched.attempts,
        )

    def metrics_snapshot(self) -> dict[str, Any]:
        return self.metrics.as_dict()
