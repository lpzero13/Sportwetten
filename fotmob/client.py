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
from requests.adapters import HTTPAdapter

from .models import FotMobFetchResult
from .parser import parse_fotmob_payload
from .rate_control import AdaptiveRateController, RateControlConfig, normalize_rate_mode


class FotMobAccessError(RuntimeError):
    """Raised for an unsuccessful or structurally invalid provider response."""


@dataclass(slots=True)
class FotMobAccessMetrics:
    requests: int = 0
    successes: int = 0
    errors: int = 0
    retries: int = 0
    total_response_ms: int = 0
    total_payload_bytes: int = 0
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
    forbidden_responses: int = 0
    server_error_responses: int = 0
    timeout_errors: int = 0
    connection_errors: int = 0
    other_transport_errors: int = 0
    parse_failures: int = 0
    rate_wait_count: int = 0
    rate_wait_ms_total: float = 0.0
    max_rate_wait_ms: float = 0.0
    request_start_count: int = 0
    first_request_monotonic: float | None = field(default=None, repr=False)
    last_request_monotonic: float | None = field(default=None, repr=False)
    first_request_start_monotonic: float | None = field(default=None, repr=False)
    last_request_start_monotonic: float | None = field(default=None, repr=False)
    request_start_times: list[float] = field(default_factory=list, repr=False)
    rate_slot_count: int = 0
    first_rate_slot_monotonic: float | None = field(default=None, repr=False)
    last_rate_slot_monotonic: float | None = field(default=None, repr=False)
    rate_slot_times: list[float] = field(default_factory=list, repr=False)
    detail_call_times_ms: list[int] = field(default_factory=list, repr=False)
    parse_times_ms: list[int] = field(default_factory=list, repr=False)

    @property
    def average_response_ms(self) -> float:
        return (
            self.total_response_ms / len(self.response_times_ms)
            if self.response_times_ms
            else 0.0
        )

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

    @property
    def elapsed_seconds(self) -> float:
        if self.first_request_monotonic is None or self.last_request_monotonic is None:
            return 0.0
        return max(0.0, self.last_request_monotonic - self.first_request_monotonic)

    @property
    def success_rate(self) -> float:
        return self.successes / self.requests if self.requests else 0.0

    @property
    def error_rate(self) -> float:
        return self.errors / self.requests if self.requests else 0.0

    @property
    def effective_rps(self) -> float:
        return self.successes / self.elapsed_seconds if self.elapsed_seconds > 0 else 0.0

    @property
    def request_start_span_seconds(self) -> float:
        if (
            self.first_request_start_monotonic is None
            or self.last_request_start_monotonic is None
        ):
            return 0.0
        return max(
            0.0,
            self.last_request_start_monotonic - self.first_request_start_monotonic,
        )

    @property
    def request_start_rps(self) -> float:
        span = self.request_start_span_seconds
        if span <= 0 or self.request_start_count <= 1:
            return float(self.request_start_count) if span == 0 else 0.0
        return (self.request_start_count - 1) / span

    @property
    def average_rate_wait_ms(self) -> float:
        return self.rate_wait_ms_total / self.rate_wait_count if self.rate_wait_count else 0.0

    @staticmethod
    def _p95(values: list[int]) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1))
        return float(ordered[index])

    @property
    def rate_slot_span_seconds(self) -> float:
        if self.first_rate_slot_monotonic is None or self.last_rate_slot_monotonic is None:
            return 0.0
        return max(0.0, self.last_rate_slot_monotonic - self.first_rate_slot_monotonic)

    @property
    def rate_slot_rps(self) -> float:
        span = self.rate_slot_span_seconds
        if span <= 0 or self.rate_slot_count <= 1:
            return float(self.rate_slot_count) if span == 0 else 0.0
        return (self.rate_slot_count - 1) / span

    @property
    def megabytes_per_minute(self) -> float:
        return (
            self.total_payload_bytes / 1024 / 1024 / self.elapsed_seconds * 60
            if self.elapsed_seconds > 0
            else 0.0
        )

    def as_dict(self, *, include_samples: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "requests": self.requests,
            "requested": self.requests,
            "successes": self.successes,
            "successful": self.successes,
            "errors": self.errors,
            "retries": self.retries,
            "http_failures": self.http_failures,
            "rate_limit_responses": self.rate_limit_responses,
            "429": self.rate_limit_responses,
            "forbidden_responses": self.forbidden_responses,
            "403": self.forbidden_responses,
            "server_error_responses": self.server_error_responses,
            "5xx": self.server_error_responses,
            "timeout_errors": self.timeout_errors,
            "timeouts": self.timeout_errors,
            "connection_errors": self.connection_errors,
            "other_transport_errors": self.other_transport_errors,
            "parse_failures": self.parse_failures,
            "success_rate": round(self.success_rate, 6),
            "error_rate": round(self.error_rate, 6),
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
            "total_payload_bytes": self.total_payload_bytes,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "effective_rps": round(self.effective_rps, 4),
            "effective_requests_per_second": round(self.effective_rps, 4),
            "megabytes_per_minute": round(self.megabytes_per_minute, 4),
            "response_time_count": len(self.response_times_ms),
            "payload_sample_count": len(self.response_sizes),
            "rate_wait_count": self.rate_wait_count,
            "rate_wait_ms_total": round(self.rate_wait_ms_total, 3),
            "average_rate_wait_ms": round(self.average_rate_wait_ms, 3),
            "max_rate_wait_ms": round(self.max_rate_wait_ms, 3),
            "request_start_count": self.request_start_count,
            "request_start_span_seconds": round(self.request_start_span_seconds, 6),
            "request_start_rps": round(self.request_start_rps, 4),
            "rate_slot_count": self.rate_slot_count,
            "rate_slot_span_seconds": round(self.rate_slot_span_seconds, 6),
            "rate_slot_rps": round(self.rate_slot_rps, 4),
            "detail_call_count": len(self.detail_call_times_ms),
            "detail_call_median_ms": round(
                statistics.median(self.detail_call_times_ms)
                if self.detail_call_times_ms
                else 0.0,
                1,
            ),
            "detail_call_p95_ms": round(self._p95(self.detail_call_times_ms), 1),
            "parse_call_count": len(self.parse_times_ms),
            "parse_median_ms": round(
                statistics.median(self.parse_times_ms) if self.parse_times_ms else 0.0,
                1,
            ),
            "parse_p95_ms": round(self._p95(self.parse_times_ms), 1),
        }
        if include_samples:
            result["response_times_ms"] = list(self.response_times_ms)
            result["response_sizes"] = list(self.response_sizes)
            result["request_start_times"] = list(self.request_start_times)
            result["rate_slot_times"] = list(self.rate_slot_times)
            result["detail_call_times_ms"] = list(self.detail_call_times_ms)
            result["parse_times_ms"] = list(self.parse_times_ms)
        return result


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
        min_request_interval_seconds: float | None = 1.0,
        retry_delays_seconds: tuple[int, ...] = (1, 3, 10),
        rate_mode: str = "FIXED",
        fixed_rps: float | None = None,
        initial_rps: float = 5.0,
        rps_step: float = 5.0,
        min_rps: float = 0.5,
        max_rps: float = 30.0,
        rate_window_requests: int = 20,
        rate_cooldown_seconds: float = 5.0,
        max_error_rate: float = 0.10,
        max_5xx_rate: float = 0.05,
        max_timeout_rate: float = 0.05,
        max_connection_error_rate: float = 0.05,
        max_p95_latency_ms: float = 3000.0,
        connection_pool_size: int = 40,
        session: requests.Session | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_base_url = api_base_url.rstrip("/")
        self.match_details_path = match_details_path
        self.timeout_seconds = max(1, int(timeout_seconds))
        self.max_retries = max(0, int(max_retries))
        self.min_request_interval_seconds = (
            max(0.0, float(min_request_interval_seconds))
            if min_request_interval_seconds is not None
            else None
        )
        self.retry_delays_seconds = tuple(float(item) for item in retry_delays_seconds) or (1.0,)
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
                # requests/urllib3 transparently decodes gzip and deflate in
                # the supported runtime.  Do not advertise Brotli unless a
                # decoder is installed; otherwise a valid 200 response can
                # arrive as undecodable compressed bytes.
                "Accept-Encoding": "gzip, deflate",
                "User-Agent": "wetten-fotmob-observer/0.5.6 (public-page client)",
            }
        )
        pool_size = max(1, int(connection_pool_size))
        self.connection_pool_size = pool_size
        if hasattr(self.session, "mount"):
            adapter = HTTPAdapter(
                pool_connections=pool_size,
                pool_maxsize=pool_size,
                max_retries=0,
                pool_block=True,
            )
            self.session.mount("http://", adapter)
            self.session.mount("https://", adapter)
        self.logger = logger or logging.getLogger("tipico.fotmob")
        self.metrics = FotMobAccessMetrics()
        self._lock = threading.RLock()
        self._metrics_lock = threading.RLock()
        self._thread_local = threading.local()
        self._rate_controller = AdaptiveRateController(
            RateControlConfig(
                mode=normalize_rate_mode(rate_mode, default="FIXED"),
                initial_rps=initial_rps,
                rps_step=rps_step,
                min_rps=min_rps,
                max_rps=max_rps,
                stable_window_requests=rate_window_requests,
                cooldown_seconds=rate_cooldown_seconds,
                max_error_rate=max_error_rate,
                max_5xx_rate=max_5xx_rate,
                max_timeout_rate=max_timeout_rate,
                max_connection_error_rate=max_connection_error_rate,
                max_p95_latency_ms=max_p95_latency_ms,
            )
        )
        # An explicitly supplied legacy interval of zero is still useful for
        # unit tests and local fixture runs: it means no pacing at all.
        self._rate_disabled = self.min_request_interval_seconds == 0.0
        if fixed_rps is not None:
            self._rate_controller.set_rate(float(fixed_rps), reset_window=True, reason="fixed_rps")
        elif (
            self._rate_controller.mode == "FIXED"
            and self.min_request_interval_seconds is not None
            and self.min_request_interval_seconds > 0
        ):
            self._rate_controller.set_rate(
                1.0 / self.min_request_interval_seconds,
                reset_window=True,
                reason="legacy_interval",
            )

    def _url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        root = (
            self.api_base_url
            if path.startswith("/matchDetails")
            or path.startswith("/leagues?id=")
            or path.startswith("/leagues?")
            or path.startswith("/data/")
            or path.startswith("/api/")
            else self.base_url
        )
        if root.endswith("/api") and path.startswith("/api/"):
            path = path[4:]
        return f"{root}/{path.lstrip('/')}"

    def _wait_for_rate_limit(self) -> None:
        if self._rate_disabled:
            self._record_rate_slot()
            return
        started = time.perf_counter()
        self._rate_controller.acquire()
        self._record_rate_slot()
        waited_ms = max(0.0, (time.perf_counter() - started) * 1000.0)
        with self._metrics_lock:
            self.metrics.rate_wait_count += 1
            self.metrics.rate_wait_ms_total += waited_ms
            self.metrics.max_rate_wait_ms = max(self.metrics.max_rate_wait_ms, waited_ms)

    def _record_request_start(self) -> None:
        """Record the actual hand-off to ``Session.get`` for stage profiling."""

        now = time.perf_counter()
        with self._metrics_lock:
            if self.metrics.first_request_start_monotonic is None:
                self.metrics.first_request_start_monotonic = now
            self.metrics.last_request_start_monotonic = now
            self.metrics.request_start_count += 1
            # Keep profiling bounded for long-running collectors.  The full
            # counter remains available; the sample list is only diagnostic.
            self.metrics.request_start_times.append(now)
            if len(self.metrics.request_start_times) > 10000:
                del self.metrics.request_start_times[:-10000]

    def _record_rate_slot(self) -> None:
        """Record the controller reservation before the HTTP hand-off."""

        now = time.perf_counter()
        with self._metrics_lock:
            if self.metrics.first_rate_slot_monotonic is None:
                self.metrics.first_rate_slot_monotonic = now
            self.metrics.last_rate_slot_monotonic = now
            self.metrics.rate_slot_count += 1
            self.metrics.rate_slot_times.append(now)
            if len(self.metrics.rate_slot_times) > 10000:
                del self.metrics.rate_slot_times[:-10000]

    def _record_detail_timing(
        self,
        elapsed_ms: int,
        *,
        parse_elapsed_ms: int | None = None,
    ) -> None:
        with self._metrics_lock:
            self.metrics.detail_call_times_ms.append(max(0, int(elapsed_ms)))
            self.metrics.parse_times_ms.append(max(0, int(parse_elapsed_ms or 0)))
            if len(self.metrics.detail_call_times_ms) > 10000:
                del self.metrics.detail_call_times_ms[:-10000]
            if len(self.metrics.parse_times_ms) > 10000:
                del self.metrics.parse_times_ms[:-10000]

    def _record_attempt(
        self,
        *,
        status_code: int | None,
        elapsed_ms: int,
        payload_size: int,
        success: bool,
        error_kind: str | None = None,
        parse_failure: bool = False,
        terminal_error: bool = False,
    ) -> None:
        """Record one HTTP attempt and feed the adaptive controller.

        ``requests`` counts attempts, while ``errors`` counts failed logical
        fetches.  Keeping those concepts separate makes retry and provider
        health metrics useful without inflating the operation error rate.
        """

        elapsed_ms = max(0, int(elapsed_ms))
        payload_size = max(0, int(payload_size))
        with self._metrics_lock:
            now = time.monotonic()
            if self.metrics.first_request_monotonic is None:
                self.metrics.first_request_monotonic = now
            self.metrics.last_request_monotonic = now
            self.metrics.requests += 1
            self.metrics.last_response_ms = elapsed_ms
            self.metrics.last_status_code = status_code
            self.metrics.response_sizes.append(payload_size)
            self.metrics.response_times_ms.append(elapsed_ms)
            self.metrics.total_response_ms += elapsed_ms
            self.metrics.total_payload_bytes += payload_size
            if status_code is not None:
                status_key = str(status_code)
            else:
                status_key = str(error_kind or "ERROR").upper()
            self.metrics.status_counts[status_key] = (
                self.metrics.status_counts.get(status_key, 0) + 1
            )
            if status_code is not None and status_code >= 400:
                self.metrics.http_failures += 1
                if status_code == 429:
                    self.metrics.rate_limit_responses += 1
                if status_code == 403:
                    self.metrics.forbidden_responses += 1
                if status_code >= 500:
                    self.metrics.server_error_responses += 1
            if error_kind == "timeout":
                self.metrics.timeout_errors += 1
            elif error_kind == "connection":
                self.metrics.connection_errors += 1
            elif error_kind:
                self.metrics.other_transport_errors += 1
            if parse_failure:
                self.metrics.parse_failures += 1
            if terminal_error:
                self.metrics.errors += 1
            if success:
                self.metrics.successes += 1
            self._rate_controller.record(
                success=success,
                status_code=status_code,
                elapsed_ms=elapsed_ms,
                error_kind=error_kind,
                parse_failure=parse_failure,
            )

    def _record_retry(self) -> None:
        with self._metrics_lock:
            self.metrics.retries += 1

    def _set_last_error(self, error: str | None) -> None:
        with self._metrics_lock:
            self.metrics.last_error = error

    def _set_endpoint(self, endpoint: str) -> None:
        with self._metrics_lock:
            self.metrics.last_endpoint = endpoint

    def _set_last_success(self) -> None:
        with self._metrics_lock:
            self.metrics.last_error = None
            self.metrics.last_success_at = datetime.now(timezone.utc).isoformat()

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
        attempts = 0
        for attempt in range(self.max_retries + 1):
            attempts += 1
            self._thread_local.last_attempts = attempts
            if attempt:
                self._record_retry()
                delay = self.retry_delays_seconds[min(attempt - 1, len(self.retry_delays_seconds) - 1)]
                time.sleep(delay)
            self._wait_for_rate_limit()
            self._record_request_start()
            with self._metrics_lock:
                self.metrics.last_request_at = datetime.now(timezone.utc).isoformat()
            started = time.perf_counter()
            self._set_endpoint(url)
            self._thread_local.last_status_code = None
            try:
                response = self.session.get(url, timeout=self.timeout_seconds)
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                status_code = int(getattr(response, "status_code", 0) or 0)
                self._thread_local.last_status_code = status_code
                content = getattr(response, "content", b"") or b""
                payload_size = len(content)
                if status_code >= 400:
                    error = FotMobAccessError(f"FotMob HTTP {status_code}")
                    retryable = status_code == 429 or status_code >= 500
                    self._record_attempt(
                        status_code=status_code,
                        elapsed_ms=elapsed_ms,
                        payload_size=payload_size,
                        success=False,
                        terminal_error=not retryable or attempt >= self.max_retries,
                    )
                    last_error = error
                    self._set_last_error(str(error))
                    if not retryable or attempt >= self.max_retries:
                        break
                    continue
                try:
                    payload = response.json()
                except (TypeError, ValueError, json.JSONDecodeError):
                    text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else str(content)
                    payload = self._embedded_json(text)
                if not isinstance(payload, dict):
                    error = FotMobAccessError(
                        "FotMob response is not JSON or embedded page data"
                    )
                    self._record_attempt(
                        status_code=status_code,
                        elapsed_ms=elapsed_ms,
                        payload_size=payload_size,
                        success=False,
                        parse_failure=True,
                        terminal_error=True,
                    )
                    last_error = error
                    self._set_last_error(str(error))
                    break
                self._record_attempt(
                    status_code=status_code,
                    elapsed_ms=elapsed_ms,
                    payload_size=payload_size,
                    success=True,
                )
                self._set_last_success()
                return payload, status_code, elapsed_ms, payload_size
            except requests.Timeout as exc:
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                self._record_attempt(
                    status_code=None,
                    elapsed_ms=elapsed_ms,
                    payload_size=0,
                    success=False,
                    error_kind="timeout",
                    terminal_error=attempt >= self.max_retries,
                )
                last_error = exc
                self._set_last_error(str(exc))
                if attempt >= self.max_retries:
                    break
            except requests.ConnectionError as exc:
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                self._record_attempt(
                    status_code=None,
                    elapsed_ms=elapsed_ms,
                    payload_size=0,
                    success=False,
                    error_kind="connection",
                    terminal_error=attempt >= self.max_retries,
                )
                last_error = exc
                self._set_last_error(str(exc))
                if attempt >= self.max_retries:
                    break
            except requests.RequestException as exc:
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                self._record_attempt(
                    status_code=None,
                    elapsed_ms=elapsed_ms,
                    payload_size=0,
                    success=False,
                    error_kind="other",
                    terminal_error=attempt >= self.max_retries,
                )
                last_error = exc
                self._set_last_error(str(exc))
                if attempt >= self.max_retries:
                    break
        # The loop records terminal errors at the point where they become
        # final.  This fallback only covers an unexpected empty retry loop.
        if last_error is None:
            last_error = FotMobAccessError("FotMob request failed")
            with self._metrics_lock:
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
                attempts=getattr(self._thread_local, "last_attempts", 1),
            )
        except (FotMobAccessError, TypeError, ValueError, KeyError) as exc:
            return FotMobFetchResult(
                success=False,
                status_code=getattr(
                    self._thread_local,
                    "last_status_code",
                    self.metrics.last_status_code,
                ),
                response_time_ms=int((time.perf_counter() - started) * 1000),
                endpoint=self._url(endpoint),
                error=str(exc),
                attempts=getattr(self._thread_local, "last_attempts", 1),
            )

    def fetch_match_details(self, provider_match_id: str) -> FotMobFetchResult:
        endpoint = self.match_details_path.format(match_id=str(provider_match_id))
        detail_started = time.perf_counter()
        fetched = self.fetch_json(endpoint)
        if not fetched.success or fetched.payload is None:
            self._record_detail_timing(int((time.perf_counter() - detail_started) * 1000))
            return fetched
        parse_started = time.perf_counter()
        try:
            match = parse_fotmob_payload(fetched.payload, provider_match_id=str(provider_match_id))
            parse_elapsed_ms = int((time.perf_counter() - parse_started) * 1000)
        except (TypeError, ValueError, KeyError) as exc:
            parse_elapsed_ms = int((time.perf_counter() - parse_started) * 1000)
            self._record_detail_timing(
                int((time.perf_counter() - detail_started) * 1000),
                parse_elapsed_ms=parse_elapsed_ms,
            )
            # The HTTP attempt was successful; parsing is a separate terminal
            # failure and must not be counted as another request.
            with self._metrics_lock:
                self.metrics.parse_failures += 1
                self.metrics.errors += 1
                self.metrics.last_error = str(exc)
            self._rate_controller.record(
                success=False,
                status_code=fetched.status_code,
                elapsed_ms=fetched.response_time_ms or 0,
                parse_failure=True,
            )
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
        self._record_detail_timing(
            int((time.perf_counter() - detail_started) * 1000),
            parse_elapsed_ms=parse_elapsed_ms,
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

    def metrics_snapshot(self, *, include_samples: bool = False) -> dict[str, Any]:
        with self._metrics_lock:
            result = self.metrics.as_dict(include_samples=include_samples)
        result["rate_control"] = self._rate_controller.snapshot()
        result["current_rps"] = result["rate_control"]["current_rps"]
        result["rate_mode"] = result["rate_control"]["mode"]
        result["connection_pool_size"] = self.connection_pool_size
        return result

    def set_rate_mode(
        self,
        mode: str,
        *,
        rps: float | None = None,
        reset_window: bool = True,
        reason: str = "mode_changed",
    ) -> dict[str, Any]:
        return self._rate_controller.set_mode(
            mode, rps=rps, reset_window=reset_window, reason=reason
        )

    def set_rate(
        self,
        rps: float,
        *,
        reset_window: bool = False,
        reason: str = "manual_rate_change",
    ) -> dict[str, Any]:
        return self._rate_controller.set_rate(
            rps, reset_window=reset_window, reason=reason
        )

    def set_benchmark_max_rps(
        self,
        max_rps: float | None,
        *,
        reason: str = "benchmark_max_rps",
    ) -> dict[str, Any]:
        """Raise/reset the controller ceiling for one finite diagnostic run."""

        return self._rate_controller.set_max_rps_override(max_rps, reason=reason)
