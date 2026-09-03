"""Small, bounded slow-operation telemetry shared by runtime services.

The collector is long-lived, so diagnostics must be cheap and bounded.  This
module records only operations above the configured threshold and keeps a
short tail of structured samples; it never changes control flow or durability
settings.
"""

from __future__ import annotations

import threading
import time
from collections import Counter, deque
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, Mapping


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SlowOperationTelemetry:
    """Record unusually slow operations without producing log spam."""

    def __init__(self, *, threshold_ms: float = 500.0, max_samples: int = 100) -> None:
        self.threshold_ms = max(0.0, float(threshold_ms))
        self.max_samples = max(1, int(max_samples))
        self._lock = threading.RLock()
        self._samples: deque[dict[str, Any]] = deque(maxlen=self.max_samples)
        self._counts: Counter[str] = Counter()
        self._total = 0

    def record(
        self,
        operation: str,
        duration_ms: float,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> bool:
        duration = max(0.0, float(duration_ms))
        if duration < self.threshold_ms:
            return False
        sample = {
            "operation": str(operation),
            "duration_ms": round(duration, 3),
            "recorded_at": _now(),
        }
        if details:
            sample.update({str(key): value for key, value in details.items()})
        with self._lock:
            self._counts[str(operation)] += 1
            self._total += 1
            self._samples.append(sample)
        return True

    @contextmanager
    def timed(
        self,
        operation: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> Iterator[None]:
        started = time.perf_counter()
        try:
            yield
        finally:
            self.record(
                operation,
                (time.perf_counter() - started) * 1000.0,
                details=details,
            )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "threshold_ms": self.threshold_ms,
                "slow_operation_count": self._total,
                "by_operation": dict(self._counts),
                "samples": list(self._samples),
            }


__all__ = ["SlowOperationTelemetry"]
