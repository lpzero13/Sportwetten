"""Adaptive, provider-friendly request pacing for FotMob.

The controller deliberately stays inside the normal public HTTP client.  It
does not rotate identities, bypass challenges or try to hide traffic.  It only
controls the start time of requests made by one shared client and reacts to
observable provider/transport health signals.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field
from typing import Any


RATE_MODES = ("ADAPTIVE", "FIXED", "CONSERVATIVE")


def normalize_rate_mode(value: Any, default: str = "ADAPTIVE") -> str:
    normalized = str(value or default).strip().upper()
    return normalized if normalized in RATE_MODES else default


@dataclass(frozen=True, slots=True)
class RateControlConfig:
    """All adaptive thresholds are configuration, not provider assumptions."""

    mode: str = "ADAPTIVE"
    initial_rps: float = 5.0
    rps_step: float = 5.0
    min_rps: float = 0.5
    max_rps: float = 30.0
    stable_window_requests: int = 20
    cooldown_seconds: float = 5.0
    max_error_rate: float = 0.10
    max_5xx_rate: float = 0.05
    max_timeout_rate: float = 0.05
    max_connection_error_rate: float = 0.05
    max_p95_latency_ms: float = 3000.0


@dataclass(slots=True)
class RateWindow:
    requests: int = 0
    successes: int = 0
    errors: int = 0
    rate_limit_responses: int = 0
    forbidden_responses: int = 0
    server_error_responses: int = 0
    timeout_errors: int = 0
    connection_errors: int = 0
    parse_failures: int = 0
    response_times_ms: list[int] = field(default_factory=list)

    def record(
        self,
        *,
        success: bool,
        status_code: int | None,
        elapsed_ms: int,
        error_kind: str | None = None,
        parse_failure: bool = False,
    ) -> None:
        self.requests += 1
        self.successes += int(bool(success))
        self.errors += int(not success)
        if elapsed_ms >= 0:
            self.response_times_ms.append(int(elapsed_ms))
        code = int(status_code or 0)
        if code == 429:
            self.rate_limit_responses += 1
        if code == 403:
            self.forbidden_responses += 1
        if code >= 500:
            self.server_error_responses += 1
        if error_kind == "timeout":
            self.timeout_errors += 1
        elif error_kind == "connection":
            self.connection_errors += 1
        if parse_failure:
            self.parse_failures += 1

    @property
    def p95_latency_ms(self) -> float:
        if not self.response_times_ms:
            return 0.0
        ordered = sorted(self.response_times_ms)
        index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1))
        return float(ordered[index])

    def summary(self, config: RateControlConfig) -> dict[str, Any]:
        requests = self.requests
        error_rate = self.errors / requests if requests else 0.0
        five_xx_rate = self.server_error_responses / requests if requests else 0.0
        timeout_rate = self.timeout_errors / requests if requests else 0.0
        connection_error_rate = self.connection_errors / requests if requests else 0.0
        hard_failure = bool(
            self.rate_limit_responses
            or self.forbidden_responses
            or self.parse_failures
        )
        soft_failure = bool(
            error_rate > config.max_error_rate
            or five_xx_rate > config.max_5xx_rate
            or timeout_rate > config.max_timeout_rate
            or connection_error_rate > config.max_connection_error_rate
            or self.p95_latency_ms > config.max_p95_latency_ms
        )
        status = "UNSTABLE" if hard_failure else "DEGRADED" if soft_failure else "STABLE"
        return {
            "status": status,
            "requests": requests,
            "successes": self.successes,
            "errors": self.errors,
            "success_rate": self.successes / requests if requests else 0.0,
            "error_rate": error_rate,
            "429": self.rate_limit_responses,
            "403": self.forbidden_responses,
            "5xx": self.server_error_responses,
            "timeout": self.timeout_errors,
            "connection_errors": self.connection_errors,
            "parse_failures": self.parse_failures,
            "5xx_rate": five_xx_rate,
            "timeout_rate": timeout_rate,
            "connection_error_rate": connection_error_rate,
            "p95_latency_ms": self.p95_latency_ms,
        }

    def reset(self) -> None:
        self.requests = 0
        self.successes = 0
        self.errors = 0
        self.rate_limit_responses = 0
        self.forbidden_responses = 0
        self.server_error_responses = 0
        self.timeout_errors = 0
        self.connection_errors = 0
        self.parse_failures = 0
        self.response_times_ms.clear()


class AdaptiveRateController:
    """Thread-safe global pacing and bounded adaptive ramp-up/backoff."""

    def __init__(self, config: RateControlConfig) -> None:
        self.config = RateControlConfig(
            mode=normalize_rate_mode(config.mode),
            initial_rps=max(0.0, float(config.initial_rps)),
            rps_step=max(0.0, float(config.rps_step)),
            min_rps=max(0.0, float(config.min_rps)),
            max_rps=max(0.0, float(config.max_rps)),
            stable_window_requests=max(1, int(config.stable_window_requests)),
            cooldown_seconds=max(0.0, float(config.cooldown_seconds)),
            max_error_rate=max(0.0, float(config.max_error_rate)),
            max_5xx_rate=max(0.0, float(config.max_5xx_rate)),
            max_timeout_rate=max(0.0, float(config.max_timeout_rate)),
            max_connection_error_rate=max(0.0, float(config.max_connection_error_rate)),
            max_p95_latency_ms=max(0.0, float(config.max_p95_latency_ms)),
        )
        self._lock = threading.RLock()
        self._mode = self.config.mode
        # A max-throughput probe may temporarily raise the ceiling on this
        # live controller.  The configured ceiling remains unchanged until a
        # measured result is deliberately promoted by the caller.
        self._max_rps_override: float | None = None
        self._current_rps = self._initial_rate()
        self._last_stable_rps: float | None = None
        self._next_request_at: float | None = None
        self._cooldown_until = 0.0
        self._window = RateWindow()
        self._last_window: dict[str, Any] = {}
        self._last_transition: dict[str, Any] | None = None

    def _initial_rate(self) -> float:
        if self._mode == "CONSERVATIVE":
            return min(self.config.min_rps, self._effective_max_rps())
        return min(self._effective_max_rps(), max(0.0, self.config.initial_rps))

    def _effective_max_rps(self) -> float:
        return (
            self._max_rps_override
            if self._max_rps_override is not None
            else self.config.max_rps
        )

    @property
    def current_rps(self) -> float:
        with self._lock:
            return self._current_rps

    @property
    def mode(self) -> str:
        with self._lock:
            return self._mode

    def acquire(self) -> None:
        """Reserve the next request start slot without holding the lock asleep."""

        while True:
            with self._lock:
                now = time.monotonic()
                wait_for_cooldown = max(0.0, self._cooldown_until - now)
                if wait_for_cooldown > 0:
                    wait = wait_for_cooldown
                else:
                    rps = self._current_rps
                    if rps <= 0:
                        return
                    interval = 1.0 / rps
                    next_slot = max(now, self._next_request_at or now)
                    wait = next_slot - now
                    if wait <= 0:
                        self._next_request_at = next_slot + interval
                        return
            time.sleep(wait)

    def set_mode(
        self,
        mode: str,
        *,
        rps: float | None = None,
        reset_window: bool = True,
        reason: str = "mode_changed",
    ) -> dict[str, Any]:
        normalized = normalize_rate_mode(mode)
        with self._lock:
            previous = self._mode
            self._mode = normalized
            if rps is None:
                self._current_rps = self._initial_rate()
            else:
                self._current_rps = self._clamp_rate(rps)
            self._next_request_at = None
            self._cooldown_until = 0.0
            if reset_window:
                self._window.reset()
            self._last_transition = {
                "action": "MODE_CHANGE",
                "reason": reason,
                "previous_mode": previous,
                "mode": normalized,
                "current_rps": self._current_rps,
                "at": time.time(),
            }
            return dict(self._last_transition)

    def set_rate(
        self,
        rps: float,
        *,
        reset_window: bool = False,
        reason: str = "manual_rate_change",
    ) -> dict[str, Any]:
        with self._lock:
            previous = self._current_rps
            self._current_rps = self._clamp_rate(rps)
            self._next_request_at = None
            if reset_window:
                self._window.reset()
            self._last_transition = {
                "action": "SET_RATE",
                "reason": reason,
                "previous_rps": previous,
                "current_rps": self._current_rps,
                "mode": self._mode,
                "at": time.time(),
            }
            return dict(self._last_transition)

    def set_max_rps_override(
        self,
        max_rps: float | None,
        *,
        reason: str = "benchmark_max_rps",
    ) -> dict[str, Any]:
        """Temporarily change the runtime ceiling for a finite benchmark.

        This is intentionally separate from :class:`RateControlConfig`: a
        probe can measure above the production ceiling without silently
        changing the deployed default.  Passing ``None`` restores the
        configured ceiling.
        """

        with self._lock:
            previous_max = self._effective_max_rps()
            if max_rps is None:
                self._max_rps_override = None
            else:
                self._max_rps_override = max(0.0, float(max_rps))
            current = self._current_rps
            self._current_rps = self._clamp_rate(current)
            self._next_request_at = None
            self._last_transition = {
                "action": "MAX_RPS_OVERRIDE",
                "reason": reason,
                "previous_max_rps": previous_max,
                "max_rps": self._effective_max_rps(),
                "current_rps": self._current_rps,
                "at": time.time(),
            }
            return dict(self._last_transition)

    def _clamp_rate(self, value: float) -> float:
        return min(self._effective_max_rps(), max(0.0, float(value)))

    def record(
        self,
        *,
        success: bool,
        status_code: int | None,
        elapsed_ms: int,
        error_kind: str | None = None,
        parse_failure: bool = False,
    ) -> dict[str, Any] | None:
        with self._lock:
            self._window.record(
                success=success,
                status_code=status_code,
                elapsed_ms=elapsed_ms,
                error_kind=error_kind,
                parse_failure=parse_failure,
            )
            if self._mode != "ADAPTIVE" or self._window.requests < self.config.stable_window_requests:
                return None
            summary = self._window.summary(self.config)
            self._last_window = summary
            previous = self._current_rps
            if summary["status"] == "STABLE":
                self._last_stable_rps = previous
                next_rate = min(
                    self._effective_max_rps(), previous + self.config.rps_step
                )
                action = "RAMP_UP" if next_rate > previous else "HOLD"
                self._current_rps = next_rate
                self._cooldown_until = 0.0
            else:
                if summary["status"] == "UNSTABLE":
                    backoff = previous / 2.0 if previous > 0 else self.config.min_rps
                else:
                    backoff = previous * 0.75 if previous > 0 else self.config.min_rps
                floor = min(self.config.min_rps, self._effective_max_rps())
                next_rate = max(floor, min(previous, backoff))
                self._current_rps = next_rate
                self._cooldown_until = time.monotonic() + self.config.cooldown_seconds
                action = "BACKOFF"
            self._window.reset()
            self._next_request_at = None
            self._last_transition = {
                "action": action,
                "reason": summary["status"].lower(),
                "status": summary["status"],
                "previous_rps": previous,
                "current_rps": self._current_rps,
                "cooldown_seconds": self.config.cooldown_seconds
                if action == "BACKOFF"
                else 0.0,
                "window": summary,
                "at": time.time(),
            }
            return dict(self._last_transition)

    def evaluate_window(self, *, reset: bool = False) -> dict[str, Any]:
        with self._lock:
            summary = self._window.summary(self.config)
            if reset:
                self._window.reset()
            return summary

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            now = time.monotonic()
            return {
                "mode": self._mode,
                "current_rps": round(self._current_rps, 4),
                "initial_rps": self.config.initial_rps,
                "rps_step": self.config.rps_step,
                "min_rps": self.config.min_rps,
                "max_rps": self._effective_max_rps(),
                "configured_max_rps": self.config.max_rps,
                "last_stable_rps": self._last_stable_rps,
                "cooldown_remaining_seconds": round(
                    max(0.0, self._cooldown_until - now), 3
                ),
                "window": self._window.summary(self.config),
                "last_window": dict(self._last_window),
                "last_transition": dict(self._last_transition)
                if self._last_transition
                else None,
            }
