"""Max-throughput and bottleneck probe for FotMob V0.5.6.1.

This module deliberately sits beside the normal V0.5.6 performance probe.
It raises the controller ceiling only for one finite, manually started run,
uses the same public HTTP client, and restores the deployed rate afterwards.
The runner measures request scheduling separately from response completion so
an apparent plateau can be attributed to the controller, scheduling, network,
provider health, or application resources with evidence.
"""

from __future__ import annotations

import logging
import os
import statistics
import time
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .performance import FotMobPerformanceProbe, _counter_delta


BOTTLENECK_CLASSES = (
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
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _numeric_delta(
    after: Mapping[str, Any], before: Mapping[str, Any], key: str
) -> float:
    try:
        return max(0.0, float(after.get(key, 0) or 0) - float(before.get(key, 0) or 0))
    except (TypeError, ValueError):
        return 0.0


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, (len(ordered) * 95 + 99) // 100 - 1))
    return float(ordered[index])


def _median_interval_ms(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    return float(
        statistics.median(
            (right - left) * 1000.0 for left, right in zip(values, values[1:])
        )
    )


def _process_rss_bytes() -> int | None:
    """Return current process RSS where the host exposes a portable API."""

    try:
        import psutil

        return int(psutil.Process(os.getpid()).memory_info().rss)
    except (ImportError, OSError, ValueError):
        pass

    if os.name == "nt":
        try:
            import ctypes

            class ProcessMemoryCounters(ctypes.Structure):
                _fields_ = [
                    ("cb", ctypes.c_ulong),
                    ("PageFaultCount", ctypes.c_ulong),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            counters = ProcessMemoryCounters()
            counters.cb = ctypes.sizeof(ProcessMemoryCounters)
            process = ctypes.windll.kernel32.GetCurrentProcess()
            result = ctypes.windll.psapi.GetProcessMemoryInfo(
                process,
                ctypes.byref(counters),
                counters.cb,
            )
            if result:
                return int(counters.WorkingSetSize)
        except Exception:  # pragma: no cover - Windows API availability varies
            return None
        return None
    try:
        import resource

        value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        # Linux reports KiB; macOS reports bytes.  The production target is
        # Windows, but the fallback keeps the diagnostic useful in CI.
        return value * 1024 if value < 1024 * 1024 * 1024 else value
    except (ImportError, AttributeError, OSError, ValueError):
        return None


def _process_resource_snapshot() -> dict[str, Any]:
    return {
        "cpu_time_seconds": time.process_time(),
        "rss_bytes": _process_rss_bytes(),
    }


def _strict_stage_status(stage: Mapping[str, Any]) -> str:
    """Apply the V0.5.6.1 acceptance rule to one measured stage."""

    requests = max(1, int(stage.get("requests", 0) or 0))
    successful = max(0, int(stage.get("successful", 0) or 0))
    success_rate = successful / requests
    hard_failures = any(
        int(stage.get(key, 0) or 0) > 0
        for key in ("429", "403", "parse_failures")
    )
    transient_failures = sum(
        int(stage.get(key, 0) or 0) for key in ("5xx", "timeouts", "connection_errors")
    )
    if hard_failures or success_rate < 0.995:
        return "UNSTABLE"
    if transient_failures / requests > 0.005:
        return "DEGRADED"
    return "STABLE"


def _sample_delta(
    before: Mapping[str, Any], after: Mapping[str, Any], key: str, count: int
) -> list[float]:
    before_values = before.get(key)
    after_values = after.get(key)
    if not isinstance(before_values, list) or not isinstance(after_values, list):
        return []
    if len(after_values) >= len(before_values) and after_values[: len(before_values)] == before_values:
        values = after_values[len(before_values) :]
    else:
        values = after_values[-max(0, count) :] if count else []
    result: list[float] = []
    for value in values:
        try:
            result.append(float(value))
        except (TypeError, ValueError):
            continue
    return result


class FotMobMaxThroughputProbe(FotMobPerformanceProbe):
    """Finite V0.5.6.1 probe using real daily IDs and real detail requests."""

    def __init__(self, pipeline: Any, *, logger: logging.Logger | None = None) -> None:
        super().__init__(pipeline, logger=logger)
        self.logger = logger or logging.getLogger("tipico.fotmob.max_throughput")

    def _run_stage(self, **kwargs: Any) -> dict[str, Any]:
        before = self._snapshot(self.client, include_samples=True)
        resources_before = _process_resource_snapshot()
        stage = super()._run_stage(**kwargs)
        after = self._snapshot(self.client, include_samples=True)
        resources_after = _process_resource_snapshot()

        requests = max(0, int(stage.get("requests", 0) or 0))
        start_times = _sample_delta(
            before,
            after,
            "request_start_times",
            _counter_delta(after, before, "request_start_count") or requests,
        )
        if len(start_times) >= 2:
            start_span = max(0.0, start_times[-1] - start_times[0])
            request_start_rps = (len(start_times) - 1) / start_span if start_span else 0.0
        else:
            start_span = 0.0
            request_start_rps = 0.0
        slot_times = _sample_delta(
            before,
            after,
            "rate_slot_times",
            _counter_delta(after, before, "rate_slot_count") or requests,
        )
        if len(slot_times) >= 2:
            slot_span = max(0.0, slot_times[-1] - slot_times[0])
            rate_slot_rps = (len(slot_times) - 1) / slot_span if slot_span else 0.0
        else:
            slot_span = 0.0
            rate_slot_rps = 0.0
        rate_control = after.get("rate_control")
        controller_rps = (
            float(rate_control.get("current_rps"))
            if isinstance(rate_control, Mapping)
            and rate_control.get("current_rps") is not None
            else float(kwargs.get("rps", 0.0) or 0.0)
        )
        detail_times = _sample_delta(
            before,
            after,
            "detail_call_times_ms",
            _counter_delta(after, before, "detail_call_count") or requests,
        )
        parse_times = _sample_delta(
            before,
            after,
            "parse_times_ms",
            _counter_delta(after, before, "parse_call_count") or requests,
        )
        detail_median = statistics.median(detail_times) if detail_times else None
        detail_p95 = _p95(detail_times)
        parse_median = statistics.median(parse_times) if parse_times else None
        parse_p95 = _p95(parse_times)
        elapsed = max(0.001, float(stage.get("elapsed_seconds", 0.001) or 0.001))
        rate_wait_ms = _numeric_delta(after, before, "rate_wait_ms_total")
        rate_wait_count = _counter_delta(after, before, "rate_wait_count")
        cpu_before = resources_before.get("cpu_time_seconds")
        cpu_after = resources_after.get("cpu_time_seconds")
        cpu_time_delta = (
            max(0.0, float(cpu_after) - float(cpu_before))
            if cpu_before is not None and cpu_after is not None
            else None
        )
        rss_before = resources_before.get("rss_bytes")
        rss_after = resources_after.get("rss_bytes")
        rss_peak = (
            max(int(rss_before), int(rss_after))
            if rss_before is not None and rss_after is not None
            else (int(rss_after) if rss_after is not None else None)
        )
        rss_delta = (
            int(rss_after) - int(rss_before)
            if rss_before is not None and rss_after is not None
            else None
        )
        stage.update(
            {
                "connection_pool_size": after.get("connection_pool_size")
                or getattr(self.client, "connection_pool_size", None),
                "request_start_count": len(start_times)
                or _counter_delta(after, before, "request_start_count")
                or requests,
                "request_start_rps": request_start_rps,
                "request_start_span_seconds": start_span,
                "request_start_interval_median_ms": _median_interval_ms(start_times),
                "controller_rps": controller_rps,
                "rate_slot_rps": rate_slot_rps,
                "rate_slot_span_seconds": slot_span,
                "rate_slot_interval_median_ms": _median_interval_ms(slot_times),
                "rate_wait_ms": rate_wait_ms,
                "rate_wait_count": rate_wait_count,
                "rate_wait_ratio": rate_wait_ms / (elapsed * 1000.0),
                "detail_call_median_ms": detail_median,
                "detail_call_p95_ms": detail_p95,
                "parse_median_ms": parse_median,
                "parse_p95_ms": parse_p95,
                "cpu_time_seconds": cpu_time_delta,
                "cpu_utilization_percent": (
                    cpu_time_delta / elapsed * 100.0 if cpu_time_delta is not None else None
                ),
                "rss_peak_bytes": rss_peak,
                "rss_delta_bytes": rss_delta,
                "status": _strict_stage_status(stage),
            }
        )
        self.store.save_performance_profile(stage)
        return stage

    @staticmethod
    def _rps_levels(max_target_rps: float) -> list[float]:
        maximum = max(30.0, float(max_target_rps))
        levels = [float(value) for value in range(30, min(60, int(maximum)) + 1, 5)]
        if maximum >= 70:
            levels.extend(
                float(value)
                for value in range(70, int(maximum) + 1, 10)
                if value <= maximum
            )
        return levels or [30.0]

    def _set_benchmark_ceiling(self, maximum: float) -> None:
        setter = getattr(self.client, "set_benchmark_max_rps", None)
        if callable(setter):
            setter(maximum, reason="v0561_max_throughput_probe_start")

    def _clear_benchmark_ceiling(self) -> None:
        setter = getattr(self.client, "set_benchmark_max_rps", None)
        if callable(setter):
            setter(None, reason="v0561_max_throughput_probe_complete")

    @staticmethod
    def _clean_workers(
        configured: tuple[int, ...] | list[int] | None,
        *,
        default: tuple[int, ...],
        maximum: int,
        include_worker_50: bool,
    ) -> list[int]:
        values = configured or default
        cleaned = {max(1, min(maximum, int(value))) for value in values if int(value) > 0}
        if include_worker_50:
            cleaned.add(min(maximum, 50))
        return sorted(cleaned) or [max(1, min(maximum, 10))]

    @staticmethod
    def _choose_workers(worker_stages: list[Mapping[str, Any]], fallback: int) -> int:
        stable = [stage for stage in worker_stages if stage.get("status") == "STABLE"]
        if not stable:
            return fallback
        maximum = max(float(stage.get("matches_per_minute", 0.0) or 0.0) for stage in stable)
        threshold = maximum * 0.95
        eligible = [
            stage
            for stage in stable
            if float(stage.get("matches_per_minute", 0.0) or 0.0) >= threshold
        ]
        return min(
            (int(stage.get("workers", fallback) or fallback) for stage in eligible),
            default=fallback,
        )

    @staticmethod
    def _best_effective(stages: list[Mapping[str, Any]]) -> float:
        return max(
            (
                float(stage.get("effective_rps", 0.0) or 0.0)
                for stage in stages
                if stage.get("status") == "STABLE"
            ),
            default=0.0,
        )

    @staticmethod
    def _classify_bottleneck(
        stages: list[Mapping[str, Any]],
        *,
        pool_size: int,
    ) -> tuple[str, str]:
        all_stages = [stage for stage in stages if stage.get("phase") != "SUMMARY"]
        if any(int(stage.get("429", 0) or 0) or int(stage.get("403", 0) or 0) for stage in all_stages):
            return "PROVIDER", "429/403 responses were observed during the finite probe."
        if any(int(stage.get("parse_failures", 0) or 0) for stage in all_stages):
            return "PARSER", "At least one structurally invalid detail payload was observed."
        if any(
            int(stage.get("timeouts", 0) or 0)
            or int(stage.get("connection_errors", 0) or 0)
            for stage in all_stages
        ):
            return "NETWORK", "Transport failures appeared at the tested throughput."

        cpu_values = [
            float(stage.get("cpu_utilization_percent"))
            for stage in all_stages
            if stage.get("cpu_utilization_percent") is not None
        ]
        if cpu_values and max(cpu_values) >= 85.0:
            return "CPU", "Process CPU utilization reached at least 85% in a measured stage."

        for stage in all_stages:
            target = float(stage.get("rps", 0.0) or 0.0)
            start_rps = float(stage.get("request_start_rps", 0.0) or 0.0)
            effective = float(stage.get("effective_rps", 0.0) or 0.0)
            if target <= 0:
                continue
            slot_rps = float(stage.get("rate_slot_rps", 0.0) or 0.0)
            if start_rps and start_rps < target * 0.90:
                parse_median = float(stage.get("parse_median_ms", 0.0) or 0.0)
                parse_p95 = float(stage.get("parse_p95_ms", 0.0) or 0.0)
                if parse_median >= 50.0 or parse_p95 >= 100.0:
                    return "PARSER", "Rate slots were available, but measured detail parsing consumed the worker time."
                if slot_rps >= target * 0.90:
                    return "REQUEST_SCHEDULING", "Rate slots reached target, but workers did not hand requests to HTTP at that rate."
                controller_rps = float(stage.get("controller_rps", 0.0) or 0.0)
                if controller_rps >= target * 0.95:
                    return "REQUEST_SCHEDULING", "The fixed controller target was active, but OS/worker scheduling delivered quantized rate slots below target."
                return "RATE_CONTROLLER", "The controller's active rate itself stayed below the configured target."
            if start_rps >= target * 0.90 and effective < target * 0.90:
                if int(stage.get("workers", 0) or 0) >= pool_size and pool_size > 0:
                    # There is no pool wait evidence unless the benchmark had
                    # to run more workers than available connections.  A
                    # worker-at-pool-size stage alone is not enough to claim a
                    # pool bottleneck.
                    pass
                return "NETWORK", "Requests were scheduled near target, but completed detail throughput lagged behind it."

        worker_stages = [stage for stage in all_stages if stage.get("phase") == "WORKER"]
        if len(worker_stages) >= 2:
            rates = [float(stage.get("matches_per_minute", 0.0) or 0.0) for stage in worker_stages]
            if rates and max(rates) > 0 and max(rates) - min(rates) <= max(rates) * 0.05:
                return "NETWORK", "Worker scaling was flat while request health remained stable."
        return "UNKNOWN", "No single measured component crossed the configured bottleneck thresholds."

    @staticmethod
    def _runtime_estimates(effective_rps: float) -> dict[str, str]:
        counts = (1000, 3000, 10000, 25000, 50000)
        if effective_rps <= 0:
            return {str(count): "n/a" for count in counts}
        result: dict[str, str] = {}
        for count in counts:
            seconds = count / effective_rps
            if seconds < 60:
                label = f"{seconds:.1f} s"
            elif seconds < 3600:
                label = f"{seconds / 60:.1f} min"
            else:
                label = f"{seconds / 3600:.2f} h"
            result[str(count)] = label
        return result

    def run(
        self,
        start_date: date | str,
        end_date: date | str,
        *,
        requests_per_level: int = 100,
        critical_requests: int = 250,
        max_target_rps: float = 100.0,
        worker_levels: tuple[int, ...] | list[int] | None = None,
        include_worker_50: bool = False,
        execution_mode: str = "manual",
    ) -> dict[str, Any]:
        start, end = self._validate_range(start_date, end_date)
        run_id = f"v0561-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
        maximum = max(30.0, float(max_target_rps))
        request_count = max(1, int(requests_per_level))
        critical_count = max(request_count, int(critical_requests))
        configured_max_workers = max(
            1, int(getattr(self.settings, "fotmob_max_workers", 40))
        )
        worker_maximum = max(configured_max_workers, 50 if include_worker_50 else 1)
        default_workers = tuple(
            int(value)
            for value in getattr(self.settings, "fotmob_performance_worker_levels", (10, 20, 30, 40))
        )
        workers = self._clean_workers(
            worker_levels,
            default=default_workers,
            maximum=worker_maximum,
            include_worker_50=include_worker_50,
        )
        configuration = {
            "rate_mode": str(getattr(self.settings, "fotmob_rate_mode", "ADAPTIVE")).upper(),
            "configured_max_rps": float(getattr(self.settings, "fotmob_max_rps", 30.0)),
            "temporary_max_rps": maximum,
            "initial_workers": int(getattr(self.settings, "fotmob_initial_workers", 10)),
            "max_workers": worker_maximum,
            "connection_pool_size": int(getattr(self.settings, "fotmob_connection_pool_size", 40)),
            "requests_per_level": request_count,
            "critical_requests": critical_count,
            "worker_levels": workers,
            "rps_levels_initial": self._rps_levels(maximum),
            "execution_mode": execution_mode,
        }
        if execution_mode.casefold() != "manual":
            return {
                "status": "BLOCKED_BY_POLICY",
                "run_id": run_id,
                "from_date": start.isoformat(),
                "to_date": end.isoformat(),
                "configuration": configuration,
                "stages": [],
                "error": "Der V0.5.6.1-Max-Throughput-Test muss bewusst im Modus manual gestartet werden.",
            }

        index_result = self.pipeline.load_date_range(
            start,
            end,
            fetch_details=False,
            workers=configuration["initial_workers"],
            execution_mode=execution_mode,
        )
        if str(index_result.get("status")) in {"BLOCKED_BY_POLICY", "ERROR"}:
            return {
                "status": "BLOCKED_BY_POLICY"
                if index_result.get("status") == "BLOCKED_BY_POLICY"
                else "FAIL",
                "run_id": run_id,
                "from_date": start.isoformat(),
                "to_date": end.isoformat(),
                "configuration": configuration,
                "index": index_result,
                "stages": [],
                "error": index_result.get("error") or "FotMob-Index konnte nicht geladen werden.",
            }
        rows = self.store.daily_index(
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            limit=100000,
            order_by="kickoff_at_utc",
            ascending=True,
        )
        all_ids = self._sample_ids(rows, critical_count)
        if not all_ids:
            return {
                "status": "FAIL",
                "run_id": run_id,
                "from_date": start.isoformat(),
                "to_date": end.isoformat(),
                "configuration": configuration,
                "index": index_result,
                "stages": [],
                "error": "Die drei Tage enthalten keine FotMob-Match-IDs für Detailtests.",
            }
        primary_ids = all_ids[:request_count]
        critical_ids = all_ids[:critical_count]
        stages: list[dict[str, Any]] = []
        last_stable_target: float | None = None
        unstable_target: float | None = None
        rps_levels = self._rps_levels(maximum)

        self._set_benchmark_ceiling(maximum)
        try:
            initial_workers = max(1, min(worker_maximum, configuration["initial_workers"]))
            for target in rps_levels:
                stage = self._run_stage(
                    run_id=run_id,
                    phase="RPS",
                    rps=target,
                    workers=initial_workers,
                    match_ids=primary_ids,
                    start_date=start,
                    end_date=end,
                )
                stages.append(stage)
                if stage.get("status") == "STABLE":
                    last_stable_target = target
                    continue
                unstable_target = target
                fallback = max(
                    float(getattr(self.settings, "fotmob_min_rps", 0.5)),
                    min(
                        last_stable_target or target / 2.0,
                        float(getattr(self.settings, "fotmob_max_rps", target)),
                    ),
                )
                if fallback < target:
                    confirmation = self._run_stage(
                        run_id=run_id,
                        phase="BACKOFF_CONFIRMATION",
                        rps=fallback,
                        workers=initial_workers,
                        match_ids=primary_ids,
                        start_date=start,
                        end_date=end,
                    )
                    stages.append(confirmation)
                    if confirmation.get("status") == "STABLE":
                        last_stable_target = max(last_stable_target or 0.0, fallback)
                self.logger.warning(
                    "FotMob V0.5.6.1 stops after %s at %.2f target RPS and confirms the last safe rate.",
                    stage.get("status"),
                    target,
                )
                break

            stable_targets = [
                float(stage["rps"])
                for stage in stages
                if stage.get("phase") == "RPS" and stage.get("status") == "STABLE"
            ]
            max_stable_target = max(stable_targets, default=last_stable_target or 0.0)
            critical_stage: dict[str, Any] | None = None
            if max_stable_target > 0:
                critical_stage = self._run_stage(
                    run_id=run_id,
                    phase="CRITICAL_STABLE",
                    rps=max_stable_target,
                    workers=initial_workers,
                    match_ids=critical_ids,
                    start_date=start,
                    end_date=end,
                )
                stages.append(critical_stage)

            worker_stages: list[dict[str, Any]] = []
            if max_stable_target > 0:
                for worker_count in workers:
                    worker_stage = self._run_stage(
                        run_id=run_id,
                        phase="WORKER",
                        rps=max_stable_target,
                        workers=worker_count,
                        match_ids=primary_ids,
                        start_date=start,
                        end_date=end,
                    )
                    worker_stages.append(worker_stage)
                    stages.append(worker_stage)
                    if worker_stage.get("status") != "STABLE":
                        break

            recommended_workers = self._choose_workers(worker_stages, initial_workers)
            stable_for_effective = [
                stage
                for stage in stages
                if stage.get("status") == "STABLE"
                and stage.get("phase") in {"RPS", "CRITICAL_STABLE", "WORKER"}
            ]
            max_effective_rps = max(
                (float(stage.get("effective_rps", 0.0) or 0.0) for stage in stable_for_effective),
                default=0.0,
            )
            recommended_worker_stage = next(
                (
                    stage
                    for stage in worker_stages
                    if int(stage.get("workers", 0) or 0) == recommended_workers
                    and stage.get("status") == "STABLE"
                ),
                None,
            )
            recommended_effective_rps = float(
                (recommended_worker_stage or critical_stage or {}).get(
                    "effective_rps", max_effective_rps
                )
                or max_effective_rps
            )
            bottleneck, bottleneck_detail = self._classify_bottleneck(
                stages,
                pool_size=configuration["connection_pool_size"],
            )
            for stage in stages:
                stage["bottleneck"] = bottleneck
                stage["notes"] = bottleneck_detail
                self.store.save_performance_profile(stage)
            pool_benchmark = {
                "status": "SKIPPED",
                "reason": (
                    "Keine Evidenz, dass mehr als 40 simultane Verbindungen technisch benötigt werden; "
                    "ein künstlicher Pool-Vergleich würde die Messung nicht verbessern."
                ),
                "tested_sizes": [],
            }
            status = "FAIL" if not stable_targets else "PASS"
            if critical_stage is not None and critical_stage.get("status") != "STABLE":
                status = "PARTIAL"
            result = {
                "status": status,
                "run_id": run_id,
                "tested_at": _now(),
                "from_date": start.isoformat(),
                "to_date": end.isoformat(),
                "days": 3,
                "index": index_result,
                "detail_ids_available": len({str(row["fotmob_match_id"]) for row in rows}),
                "detail_ids_tested": len(primary_ids),
                "critical_detail_ids_tested": len(critical_ids),
                "configuration": configuration,
                "rps_levels": rps_levels,
                "stages": stages,
                "max_tested_target_rps": max(
                    (float(stage.get("rps", 0.0) or 0.0) for stage in stages if stage.get("phase") == "RPS"),
                    default=0.0,
                ),
                "max_stable_target_rps": max_stable_target,
                "max_effective_rps": max_effective_rps,
                "recommended_rps": max_stable_target,
                "recommended_workers": recommended_workers,
                "recommended_effective_rps": recommended_effective_rps,
                "recommended_connection_pool": configuration["connection_pool_size"],
                "known_stable_max_rps": self.store.known_stable_max_rps(
                    confirmations=int(
                        getattr(self.settings, "fotmob_performance_stable_confirmations", 2)
                    )
                ),
                "bottleneck": bottleneck,
                "bottleneck_detail": bottleneck_detail,
                "pool_benchmark": pool_benchmark,
                "runtime_estimates": self._runtime_estimates(recommended_effective_rps),
                "higher_rps_probe": bool(
                    max_stable_target > float(configuration["configured_max_rps"])
                ),
                "critical_stage_status": critical_stage.get("status") if critical_stage else None,
                "unstable_target_rps": unstable_target,
            }
            return result
        finally:
            self._clear_benchmark_ceiling()
            restore_rate = min(
                float(getattr(self.settings, "fotmob_max_rps", 30.0)),
                last_stable_target or float(getattr(self.settings, "fotmob_initial_rps", 5.0)),
            )
            self._restore_rate_mode(restore_rate)


def _fmt(value: Any, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def render_max_throughput_report(result: Mapping[str, Any]) -> str:
    stages = list(result.get("stages") or [])
    config = dict(result.get("configuration") or {})
    highest_stage = max(
        (stage for stage in stages if stage.get("phase") == "RPS"),
        key=lambda stage: float(stage.get("rps", 0.0) or 0.0),
        default={},
    )
    lines = [
        "# V0.5.6.1 FotMob Max-Throughput & Bottleneck Report",
        "",
        f"- Status: **{result.get('status', 'FAIL')}**",
        f"- Run: `{result.get('run_id', 'n/a')}`",
        f"- Exact completed days: `{result.get('from_date', 'n/a')} .. {result.get('to_date', 'n/a')}`",
        f"- Detail IDs available/tested: `{result.get('detail_ids_available', 'n/a')}` / `{result.get('detail_ids_tested', 'n/a')}`",
        f"- Critical highest-stable sample: `{result.get('critical_detail_ids_tested', 'n/a')}` detail requests",
        f"- Promotion candidate after validation: `FOTMOB_MAX_RPS={result.get('max_stable_target_rps', 'n/a')}` (now used as the standard ceiling)",
        "",
        "## Probe configuration",
        "",
        "| Setting | Value |",
        "|---|---:|",
    ]
    for key in (
        "configured_max_rps",
        "temporary_max_rps",
        "initial_workers",
        "max_workers",
        "connection_pool_size",
        "requests_per_level",
        "critical_requests",
        "worker_levels",
    ):
        lines.append(f"| `{key}` | `{config.get(key, 'n/a')}` |")
    lines.extend(
        [
            "",
            "## Required stage table",
            "",
            "`Requests` are actual HTTP attempts; retries remain visible in their own column. STABLE requires at least 99.5% success, zero 429/403/parse failures, and very low transient failure rates.",
            "",
            "| Phase | Target RPS | Effective RPS | Workers | Requests | Success % | 429 | 403 | 5xx | Timeouts | Retries | Median ms | P95 ms | Matches/min | Status |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for stage in stages:
        lines.append(
            "| {phase} | {target} | {effective} | {workers} | {requests} | {success} | {r429} | {r403} | {r5xx} | {timeouts} | {retries} | {median} | {p95} | {matches} | {status} |".format(
                phase=stage.get("phase", "n/a"),
                target=_fmt(stage.get("rps")),
                effective=_fmt(stage.get("effective_rps")),
                workers=stage.get("workers", "n/a"),
                requests=stage.get("requests", "n/a"),
                success=f"{float(stage.get('success_rate', 0.0) or 0.0):.2%}",
                r429=stage.get("429", 0),
                r403=stage.get("403", 0),
                r5xx=stage.get("5xx", 0),
                timeouts=stage.get("timeouts", 0),
                retries=stage.get("retries", 0),
                median=_fmt(stage.get("median_latency_ms"), 0),
                p95=_fmt(stage.get("p95_latency_ms"), 0),
                matches=_fmt(stage.get("matches_per_minute")),
                status=stage.get("status", "n/a"),
            )
        )
    lines.extend(
        [
            "",
            "## Decision summary",
            "",
            f"MAX_TESTED_TARGET_RPS = {result.get('max_tested_target_rps', 'n/a')}",
            f"MAX_STABLE_TARGET_RPS = {result.get('max_stable_target_rps', 'n/a')}",
            f"MAX_EFFECTIVE_RPS = {result.get('max_effective_rps', 'n/a')}",
            f"RECOMMENDED_RPS = {result.get('recommended_rps', 'n/a')}",
            f"RECOMMENDED_WORKERS = {result.get('recommended_workers', 'n/a')}",
            f"RECOMMENDED_CONNECTION_POOL = {result.get('recommended_connection_pool', 'n/a')}",
            f"BOTTLENECK = {result.get('bottleneck', 'UNKNOWN')}",
            "",
            "## Internal path profile",
            "",
            "The detail probe bypasses SQLite/Parquet writes by design, so those two components are not declared bottlenecks from this run. The request path is: global rate-controller reservation -> ThreadPoolExecutor worker -> shared requests.Session/HTTPAdapter -> provider response -> parser.",
            "",
            f"- Classification: `{result.get('bottleneck', 'UNKNOWN')}`",
            f"- Evidence: {result.get('bottleneck_detail', 'n/a')}",
            f"- Highest RPS stage: target `{_fmt(highest_stage.get('rps'))}`, controller `{_fmt(highest_stage.get('controller_rps'))}`, reserved slots `{_fmt(highest_stage.get('rate_slot_rps'))}/s`, HTTP starts `{_fmt(highest_stage.get('request_start_rps'))}/s`.",
            f"- Highest RPS timings: HTTP median/P95 `{_fmt(highest_stage.get('median_latency_ms'), 0)}/{_fmt(highest_stage.get('p95_latency_ms'), 0)} ms`, detail-call median/P95 `{_fmt(highest_stage.get('detail_call_median_ms'), 0)}/{_fmt(highest_stage.get('detail_call_p95_ms'), 0)} ms`, parser median/P95 `{_fmt(highest_stage.get('parse_median_ms'), 0)}/{_fmt(highest_stage.get('parse_p95_ms'), 0)} ms`.",
            f"- Connection pool decision: {dict(result.get('pool_benchmark') or {}).get('status', 'n/a')} — {dict(result.get('pool_benchmark') or {}).get('reason', 'n/a')}",
            "",
            "## Backfill runtime estimates",
            "",
            f"Based on recommended effective throughput `{_fmt(result.get('recommended_effective_rps'))}` detail matches/s:",
            "",
            "| Detail matches | Estimated runtime |",
            "|---:|---:|",
        ]
    )
    for count, runtime in dict(result.get("runtime_estimates") or {}).items():
        lines.append(f"| {count} | {runtime} |")
    if result.get("error"):
        lines.extend(["", f"> {result['error']}"])
    lines.append("")
    return "\n".join(lines)


def write_max_throughput_report(result: Mapping[str, Any], path: Path | str) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_max_throughput_report(result), encoding="utf-8")
    return target


def render_max_status_report(result: Mapping[str, Any]) -> str:
    status = str(result.get("status") or "FAIL").upper()
    analysis = "PASS" if result.get("bottleneck") in BOTTLENECK_CLASSES else "FAIL"
    higher_probe = "PASS" if result.get("higher_rps_probe") else "FAIL"
    ready = "YES" if status == "PASS" and result.get("recommended_effective_rps", 0) else "NO"
    return "\n".join(
        [
            "# V0.5.6.1 Status",
            "",
            f"V0561_STATUS = {status}",
            f"BOTTLENECK_ANALYSIS = {analysis}",
            f"HIGHER_RPS_PROBE = {higher_probe}",
            f"MAX_STABLE_TARGET_RPS = {result.get('max_stable_target_rps', 'n/a')}",
            f"MAX_EFFECTIVE_RPS = {result.get('max_effective_rps', 'n/a')}",
            f"RECOMMENDED_RPS = {result.get('recommended_rps', 'n/a')}",
            f"RECOMMENDED_WORKERS = {result.get('recommended_workers', 'n/a')}",
            f"RECOMMENDED_CONNECTION_POOL = {result.get('recommended_connection_pool', 'n/a')}",
            f"BOTTLENECK = {result.get('bottleneck', 'UNKNOWN')}",
            f"READY_FOR_LARGE_BACKFILL = {ready}",
            "",
            f"Run: `{result.get('run_id', 'n/a')}`",
            f"Days: `{result.get('from_date', 'n/a')} .. {result.get('to_date', 'n/a')}`",
            f"Evidence: `{result.get('bottleneck_detail', 'n/a')}`",
            "",
        ]
    )


def write_max_status_report(result: Mapping[str, Any], path: Path | str) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_max_status_report(result), encoding="utf-8")
    return target
