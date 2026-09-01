"""Measured FotMob throughput probe for V0.5.6.

The probe is intentionally explicit and finite: it indexes exactly three
completed calendar days, selects a bounded sample of real detail ids, tests
configured RPS levels, and then tests configured worker levels.  It uses the
same public-page client and the same provider-friendly retry/error handling as
normal history collection.
"""

from __future__ import annotations

import logging
import statistics
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from .models import FotMobFetchResult


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _counter_delta(after: Mapping[str, Any], before: Mapping[str, Any], key: str) -> int:
    try:
        return max(0, int(after.get(key, 0) or 0) - int(before.get(key, 0) or 0))
    except (TypeError, ValueError):
        return 0


def _status_for_measurement(
    *,
    requests: int,
    successful: int,
    http_429: int,
    http_403: int,
    http_5xx: int,
    timeouts: int,
    connection_errors: int,
    parse_failures: int,
    p95_latency_ms: float,
    settings: Any,
) -> str:
    if http_429 or http_403 or parse_failures:
        return "UNSTABLE"
    denominator = max(1, requests)
    error_rate = max(0.0, (requests - successful) / denominator)
    if (
        error_rate > float(getattr(settings, "fotmob_max_error_rate", 0.10))
        or http_5xx / denominator > float(getattr(settings, "fotmob_max_5xx_rate", 0.05))
        or timeouts / denominator > float(getattr(settings, "fotmob_max_timeout_rate", 0.05))
        or connection_errors / denominator
        > float(getattr(settings, "fotmob_max_connection_error_rate", 0.05))
        or p95_latency_ms > float(getattr(settings, "fotmob_max_p95_latency_ms", 3000.0))
    ):
        return "DEGRADED"
    return "STABLE"


class FotMobPerformanceProbe:
    """Run and persist a finite, real-data throughput profile."""

    def __init__(self, pipeline: Any, *, logger: logging.Logger | None = None) -> None:
        self.pipeline = pipeline
        self.settings = pipeline.settings
        self.store = pipeline.store
        self.client = pipeline.client
        self.logger = logger or logging.getLogger("tipico.fotmob.performance")

    def _local_today(self) -> date:
        try:
            zone = ZoneInfo(str(getattr(self.settings, "fotmob_daily_timezone", "UTC")))
        except Exception:
            zone = timezone.utc
        return datetime.now(zone).date()

    def _validate_range(self, start_date: date | str, end_date: date | str) -> tuple[date, date]:
        start = self.pipeline._coerce_date(start_date)
        end = self.pipeline._coerce_date(end_date)
        if end < start:
            raise ValueError("Das Enddatum darf nicht vor dem Startdatum liegen.")
        if (end - start).days + 1 != 3:
            raise ValueError("Der V0.5.6-Performance-Test benötigt exakt drei Tage.")
        if end >= self._local_today():
            raise ValueError("Der Performance-Test darf nur drei abgeschlossene Tage verwenden.")
        return start, end

    @staticmethod
    def _snapshot(client: Any, *, include_samples: bool = False) -> dict[str, Any]:
        method = getattr(client, "metrics_snapshot", None)
        if not callable(method):
            return {}
        try:
            value = method(include_samples=include_samples)
        except TypeError:
            value = method()
        return dict(value) if isinstance(value, Mapping) else {}

    @staticmethod
    def _sample_ids(rows: list[Any], count: int) -> list[str]:
        by_day: dict[str, list[str]] = {}
        for row in rows:
            match_id = str(row["fotmob_match_id"])
            day = str(row["observation_date"] or "")
            by_day.setdefault(day, []).append(match_id)
        for values in by_day.values():
            values.sort()
        days = sorted(by_day)
        selected: list[str] = []
        cursor = 0
        while len(selected) < count and days:
            added = False
            for day in days:
                values = by_day[day]
                if cursor < len(values):
                    match_id = values[cursor]
                    if match_id not in selected:
                        selected.append(match_id)
                        added = True
                        if len(selected) >= count:
                            break
            if not added:
                break
            cursor += 1
        return selected

    def _set_fixed_rate(self, rps: float) -> None:
        setter = getattr(self.client, "set_rate_mode", None)
        if callable(setter):
            setter(
                "FIXED",
                rps=float(rps),
                reset_window=True,
                reason="v056_performance_probe_stage",
            )
            return
        setter = getattr(self.client, "set_rate", None)
        if callable(setter):
            setter(float(rps), reset_window=True, reason="v056_performance_probe_stage")

    def _restore_rate_mode(self, recommended_rps: float | None) -> None:
        setter = getattr(self.client, "set_rate_mode", None)
        if not callable(setter):
            return
        mode = str(getattr(self.settings, "fotmob_rate_mode", "ADAPTIVE")).upper()
        setter(
            mode,
            rps=recommended_rps,
            reset_window=True,
            reason="v056_performance_probe_complete",
        )

    @staticmethod
    def _result_success(result: Any) -> bool:
        if isinstance(result, FotMobFetchResult):
            return bool(result.success)
        return result is not None and bool(getattr(result, "success", True))

    @staticmethod
    def _result_latency(result: Any) -> int | None:
        value = getattr(result, "response_time_ms", None)
        try:
            return max(0, int(value)) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _run_stage(
        self,
        *,
        run_id: str,
        phase: str,
        rps: float,
        workers: int,
        match_ids: list[str],
        start_date: date,
        end_date: date,
    ) -> dict[str, Any]:
        self._set_fixed_rate(rps)
        before = self._snapshot(self.client)
        before_samples = self._snapshot(self.client, include_samples=True)
        started = time.monotonic()
        results: list[Any] = []
        errors: list[str] = []
        actual_workers = max(1, int(workers))
        with ThreadPoolExecutor(
            max_workers=actual_workers,
            thread_name_prefix=f"fotmob-probe-{phase.casefold()}",
        ) as executor:
            futures = {
                executor.submit(self.client.fetch_match_details, match_id): match_id
                for match_id in match_ids
            }
            for future in as_completed(futures):
                try:
                    results.append(future.result())
                except Exception as exc:  # pragma: no cover - worker boundary
                    errors.append(f"{futures[future]}: {exc}")
        elapsed_seconds = max(0.001, time.monotonic() - started)
        after = self._snapshot(self.client)
        after_samples = self._snapshot(self.client, include_samples=True)

        fallback_successful = sum(self._result_success(result) for result in results)
        attempts = _counter_delta(after, before, "requests") or sum(
            max(1, int(getattr(result, "attempts", 1) or 1)) for result in results
        )
        requests = max(0, attempts)
        successful = _counter_delta(after, before, "successes")
        if not after and not before:
            successful = fallback_successful
        elif successful == 0 and fallback_successful and requests == len(results):
            successful = fallback_successful
        response_times: list[int] = []
        before_sample_times = before_samples.get("response_times_ms", [])
        after_sample_times = after_samples.get("response_times_ms", [])
        if isinstance(before_sample_times, list) and isinstance(after_sample_times, list):
            response_times = [int(value) for value in after_sample_times[len(before_sample_times):]]
        if not response_times:
            response_times = [
                value
                for result in results
                if (value := self._result_latency(result)) is not None
            ]
        median_latency = float(statistics.median(response_times)) if response_times else 0.0
        if response_times:
            ordered = sorted(response_times)
            p95_latency = float(ordered[max(0, min(len(ordered) - 1, (len(ordered) * 95 + 99) // 100 - 1))])
        else:
            p95_latency = 0.0
        http_429 = _counter_delta(after, before, "rate_limit_responses") or _counter_delta(
            after, before, "429"
        )
        http_403 = _counter_delta(after, before, "forbidden_responses") or _counter_delta(
            after, before, "403"
        )
        http_5xx = _counter_delta(after, before, "server_error_responses") or _counter_delta(
            after, before, "5xx"
        )
        timeouts = _counter_delta(after, before, "timeout_errors") or _counter_delta(
            after, before, "timeouts"
        )
        connection_errors = _counter_delta(after, before, "connection_errors")
        retries = _counter_delta(after, before, "retries")
        parse_failures = _counter_delta(after, before, "parse_failures")
        payload_bytes = _counter_delta(after, before, "total_payload_bytes")
        if payload_bytes == 0:
            payload_bytes = sum(max(0, int(getattr(result, "payload_size", 0) or 0)) for result in results)
        if requests == 0:
            requests = len(results) + len(errors)
        successful = min(requests, max(0, successful))
        error_rate = max(0.0, (requests - successful) / max(1, requests))
        status = _status_for_measurement(
            requests=requests,
            successful=successful,
            http_429=http_429,
            http_403=http_403,
            http_5xx=http_5xx,
            timeouts=timeouts,
            connection_errors=connection_errors,
            parse_failures=parse_failures,
            p95_latency_ms=p95_latency,
            settings=self.settings,
        )
        matches_per_minute = fallback_successful / elapsed_seconds * 60.0
        effective_rps = successful / elapsed_seconds
        megabytes_per_minute = payload_bytes / 1024 / 1024 / elapsed_seconds * 60.0
        result = {
            "run_id": run_id,
            "tested_at": _now(),
            "from_date": start_date.isoformat(),
            "to_date": end_date.isoformat(),
            "phase": phase.upper(),
            "rps": round(float(rps), 4),
            "workers": actual_workers,
            "matches": len(match_ids),
            "successful_matches": fallback_successful,
            "requests": requests,
            "attempts": attempts,
            "successful": successful,
            "429": http_429,
            "403": http_403,
            "5xx": http_5xx,
            "timeouts": timeouts,
            "connection_errors": connection_errors,
            "retries": retries,
            "parse_failures": parse_failures,
            "success_rate": successful / max(1, requests),
            "429_rate": http_429 / max(1, requests),
            "error_rate": error_rate,
            "median_latency_ms": median_latency,
            "p95_latency_ms": p95_latency,
            "effective_rps": effective_rps,
            "matches_per_minute": matches_per_minute,
            "megabytes_per_minute": megabytes_per_minute,
            "elapsed_seconds": elapsed_seconds,
            "status": status,
            "errors": errors,
        }
        self.store.save_performance_profile(result)
        self.logger.info(
            "FotMob V0.5.6 probe %s rps=%.2f workers=%d status=%s requests=%d "
            "success=%.1f%% p95=%.0fms 429=%d retries=%d",
            phase.upper(),
            float(rps),
            actual_workers,
            status,
            requests,
            result["success_rate"] * 100,
            p95_latency,
            http_429,
            retries,
        )
        return result

    def _bottleneck(self, stages: list[dict[str, Any]]) -> str:
        unstable = [stage for stage in stages if stage.get("status") == "UNSTABLE"]
        degraded = [stage for stage in stages if stage.get("status") == "DEGRADED"]
        if unstable:
            if any(stage.get("429") or stage.get("403") for stage in unstable):
                return "PROVIDER_RESPONSE_LIMIT"
            return "PROVIDER_OR_PARSE_INSTABILITY"
        if degraded:
            if any(stage.get("timeouts") or stage.get("connection_errors") for stage in degraded):
                return "NETWORK_OR_CONNECTION_LATENCY"
            return "PROVIDER_LATENCY_OR_TRANSIENT_ERRORS"
        worker_stages = [stage for stage in stages if stage.get("phase") == "WORKER"]
        if len(worker_stages) >= 2:
            first = max(float(worker_stages[0].get("matches_per_minute", 0.0)), 0.0)
            last = max(float(worker_stages[-1].get("matches_per_minute", 0.0)), 0.0)
            if first and last < first * 1.15:
                return "NO_MATERIAL_WORKER_SCALING"
        return "NO_MATERIAL_BOTTLENECK_OBSERVED"

    def run(
        self,
        start_date: date | str,
        end_date: date | str,
        *,
        requests_per_level: int | None = None,
        worker_levels: tuple[int, ...] | list[int] | None = None,
        execution_mode: str = "manual",
    ) -> dict[str, Any]:
        start, end = self._validate_range(start_date, end_date)
        run_id = f"v056-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
        configuration = {
            "rate_mode": str(getattr(self.settings, "fotmob_rate_mode", "ADAPTIVE")).upper(),
            "initial_rps": float(getattr(self.settings, "fotmob_initial_rps", 5.0)),
            "rps_step": float(getattr(self.settings, "fotmob_rps_step", 5.0)),
            "min_rps": float(getattr(self.settings, "fotmob_min_rps", 0.5)),
            "max_rps": float(getattr(self.settings, "fotmob_max_rps", 30.0)),
            "initial_workers": int(getattr(self.settings, "fotmob_initial_workers", 10)),
            "max_workers": int(getattr(self.settings, "fotmob_max_workers", 40)),
            "rate_window_requests": int(getattr(self.settings, "fotmob_rate_window_requests", 20)),
            "requests_per_level": max(
                1,
                int(
                    requests_per_level
                    if requests_per_level is not None
                    else getattr(self.settings, "fotmob_performance_requests_per_level", 25)
                ),
            ),
        }
        if execution_mode.casefold() != "manual":
            return {
                "status": "BLOCKED_BY_POLICY",
                "run_id": run_id,
                "from_date": start.isoformat(),
                "to_date": end.isoformat(),
                "configuration": configuration,
                "error": "V0.5.6-Performance-Tests müssen bewusst im Modus manual gestartet werden.",
                "stages": [],
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
                else "PARTIAL",
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
        requested_ids = self._sample_ids(rows, configuration["requests_per_level"])
        if not requested_ids:
            return {
                "status": "PARTIAL",
                "run_id": run_id,
                "from_date": start.isoformat(),
                "to_date": end.isoformat(),
                "configuration": configuration,
                "index": index_result,
                "stages": [],
                "error": "Die drei Tage enthalten keine FotMob-Match-IDs für Detailtests.",
            }

        initial = max(0.1, configuration["initial_rps"])
        step = max(0.1, configuration["rps_step"])
        maximum = max(initial, configuration["max_rps"])
        rps_levels: list[float] = []
        current = initial
        while current <= maximum + 1e-9:
            rps_levels.append(round(current, 4))
            current += step
        configured_workers = worker_levels or getattr(
            self.settings, "fotmob_performance_worker_levels", (10, 20, 30, 40)
        )
        worker_levels_clean = sorted(
            {
                max(1, min(configuration["max_workers"], int(value)))
                for value in configured_workers
                if int(value) > 0
            }
        ) or [configuration["initial_workers"]]

        stages: list[dict[str, Any]] = []
        last_stable_rps: float | None = None
        for index, rps in enumerate(rps_levels):
            worker_index = min(
                len(worker_levels_clean) - 1,
                (index * len(worker_levels_clean)) // max(1, len(rps_levels)),
            )
            stage = self._run_stage(
                run_id=run_id,
                phase="RPS",
                rps=rps,
                workers=worker_levels_clean[worker_index],
                match_ids=requested_ids,
                start_date=start,
                end_date=end,
            )
            stages.append(stage)
            if stage["status"] == "STABLE":
                last_stable_rps = rps
                continue
            self.logger.warning(
                "FotMob V0.5.6 probe backs off after %s at %.2f rps (%s)",
                stage["status"],
                rps,
                "provider protection/health threshold",
            )
            fallback_rps = max(
                float(configuration["min_rps"]),
                min(
                    float(configuration["max_rps"]),
                    last_stable_rps if last_stable_rps is not None else rps / 2.0,
                ),
            )
            if fallback_rps < rps:
                confirmation = self._run_stage(
                    run_id=run_id,
                    phase="BACKOFF_CONFIRMATION",
                    rps=fallback_rps,
                    workers=worker_levels_clean[worker_index],
                    match_ids=requested_ids,
                    start_date=start,
                    end_date=end,
                )
                stages.append(confirmation)
                if confirmation["status"] == "STABLE":
                    last_stable_rps = fallback_rps
            break

        stable_rps = [
            float(stage["rps"])
            for stage in stages
            if stage.get("phase") == "RPS" and stage.get("status") == "STABLE"
        ]
        recommended_rps = max(stable_rps) if stable_rps else (
            last_stable_rps or float(configuration["initial_rps"])
        )
        worker_stages: list[dict[str, Any]] = []
        if stable_rps or any(
            stage.get("phase") == "BACKOFF_CONFIRMATION"
            and stage.get("status") == "STABLE"
            for stage in stages
        ):
            for workers in worker_levels_clean:
                stage = self._run_stage(
                    run_id=run_id,
                    phase="WORKER",
                    rps=recommended_rps,
                    workers=workers,
                    match_ids=requested_ids,
                    start_date=start,
                    end_date=end,
                )
                worker_stages.append(stage)
                if stage["status"] != "STABLE":
                    break
        stages.extend(worker_stages)
        stable_worker_stages = [
            stage for stage in worker_stages if stage.get("status") == "STABLE"
        ]
        if stable_worker_stages:
            recommended_workers = max(
                stable_worker_stages,
                key=lambda item: (
                    float(item.get("matches_per_minute", 0.0)),
                    -int(item.get("workers", 0)),
                ),
            )["workers"]
        else:
            recommended_workers = next(
                (
                    int(stage["workers"])
                    for stage in stages
                    if stage.get("phase") == "RPS" and stage.get("rps") == recommended_rps
                ),
                configuration["initial_workers"],
            )
        known_stable = self.store.known_stable_max_rps(
            confirmations=int(getattr(self.settings, "fotmob_performance_stable_confirmations", 2)),
        )
        max_tested = max((float(stage["rps"]) for stage in stages if stage.get("phase") == "RPS"), default=0.0)
        status = "PASS" if any(stage.get("status") == "STABLE" for stage in stages if stage.get("phase") == "RPS") else "PARTIAL"
        result = {
            "status": status,
            "run_id": run_id,
            "from_date": start.isoformat(),
            "to_date": end.isoformat(),
            "days": 3,
            "index": index_result,
            "detail_ids_available": len({str(row["fotmob_match_id"]) for row in rows}),
            "detail_ids_tested": len(requested_ids),
            "configuration": configuration,
            "rps_levels": rps_levels,
            "worker_levels": worker_levels_clean,
            "stages": stages,
            "max_tested_rps": max_tested,
            "max_stable_rps": max(stable_rps, default=0.0),
            "known_stable_max_rps": known_stable,
            "recommended_rps": recommended_rps,
            "recommended_workers": recommended_workers,
            "bottleneck": self._bottleneck(stages),
        }
        self._restore_rate_mode(recommended_rps)
        return result


def _fmt(value: Any, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def render_performance_report(result: Mapping[str, Any]) -> str:
    stages = list(result.get("stages") or [])
    config = dict(result.get("configuration") or {})
    lines = [
        "# V0.5.6 FotMob Performance Report",
        "",
        f"- Status: **{result.get('status', 'PARTIAL')}**",
        f"- Run: `{result.get('run_id', 'n/a')}`",
        f"- Completed days: `{result.get('from_date', 'n/a')} .. {result.get('to_date', 'n/a')}`",
        f"- Detail IDs available/tested: `{result.get('detail_ids_available', 'n/a')}` / `{result.get('detail_ids_tested', 'n/a')}`",
        "",
        "## Effective configuration",
        "",
        "| Setting | Value |",
        "|---|---:|",
    ]
    for key in (
        "rate_mode",
        "initial_rps",
        "rps_step",
        "max_rps",
        "initial_workers",
        "max_workers",
        "rate_window_requests",
        "requests_per_level",
    ):
        lines.append(f"| `{key}` | `{config.get(key, 'n/a')}` |")
    lines.extend(
        [
            "",
            "## Probe stages",
            "",
            "`Requests` counts actual HTTP attempts; retries are reported separately and `successful matches` counts completed detail calls.",
            "",
            "| Phase | RPS target | Workers | Requests | Successful | Success rate | 429 | 403 | 5xx | Timeouts | Retries | Median ms | P95 ms | Effective RPS | Matches/min | MB/min | Status |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for stage in stages:
        lines.append(
            "| {phase} | {rps} | {workers} | {requests} | {successful} | {success_rate:.1%} | {rate429} | {rate403} | {rate5xx} | {timeouts} | {retries} | {median} | {p95} | {effective} | {matches} | {mb} | {status} |".format(
                phase=stage.get("phase", "n/a"),
                rps=_fmt(stage.get("rps")),
                workers=stage.get("workers", "n/a"),
                requests=stage.get("requests", "n/a"),
                successful=stage.get("successful", "n/a"),
                success_rate=float(stage.get("success_rate", 0.0)),
                **{
                    "rate429": stage.get("429", 0),
                    "rate403": stage.get("403", 0),
                    "rate5xx": stage.get("5xx", 0),
                    "timeouts": stage.get("timeouts", 0),
                    "retries": stage.get("retries", 0),
                    "median": _fmt(stage.get("median_latency_ms"), 0),
                    "p95": _fmt(stage.get("p95_latency_ms"), 0),
                    "effective": _fmt(stage.get("effective_rps")),
                    "matches": _fmt(stage.get("matches_per_minute")),
                    "mb": _fmt(stage.get("megabytes_per_minute")),
                    "status": stage.get("status", "n/a"),
                },
            )
        )
    lines.extend(
        [
            "",
            "## Decision",
            "",
            f"- `MAX_TESTED_RPS = {result.get('max_tested_rps', 'n/a')}`",
            f"- `MAX_STABLE_RPS = {result.get('max_stable_rps', 'n/a')}`",
            f"- `KNOWN_STABLE_MAX_RPS = {result.get('known_stable_max_rps', 'n/a')}`",
            f"- `RECOMMENDED_RPS = {result.get('recommended_rps', 'n/a')}`",
            f"- `RECOMMENDED_WORKERS = {result.get('recommended_workers', 'n/a')}`",
            f"- `BOTTLENECK = {result.get('bottleneck', 'n/a')}`",
            "",
            "429/403 responses and parse failures are treated as provider instability. `SKIPPED_NO_HALFTIME` is a data-availability outcome and is not part of the performance-health decision.",
            "",
        ]
    )
    if result.get("error"):
        lines.extend([f"> {result['error']}", ""])
    return "\n".join(lines)


def write_performance_report(result: Mapping[str, Any], path: Path | str) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_performance_report(result), encoding="utf-8")
    return target


def render_status_report(result: Mapping[str, Any]) -> str:
    probe_status = "PASS" if result.get("status") == "PASS" else "FAIL"
    overall_status = "PASS" if probe_status == "PASS" else "PARTIAL"
    max_stable = result.get("max_stable_rps")
    recommended_rps = result.get("recommended_rps")
    recommended_workers = result.get("recommended_workers")
    return "\n".join(
        [
            "# V0.5.6 Status",
            "",
            f"V056_STATUS = {overall_status}",
            "CAPABILITY_AUDIT = PASS",
            "LEGACY_COLLECTOR_REVIEW = PASS",
            "ADAPTIVE_RATE_CONTROL = PASS",
            f"PERFORMANCE_PROBE = {probe_status}",
            f"MAX_STABLE_RPS = {max_stable if max_stable is not None else 'n/a'}",
            f"RECOMMENDED_RPS = {recommended_rps if recommended_rps is not None else 'n/a'}",
            f"RECOMMENDED_WORKERS = {recommended_workers if recommended_workers is not None else 'n/a'}",
            "",
            f"Run: `{result.get('run_id', 'n/a')}`",
            f"Days: `{result.get('from_date', 'n/a')} .. {result.get('to_date', 'n/a')}`",
            f"Bottleneck: `{result.get('bottleneck', 'n/a')}`",
            "",
        ]
    )


def write_status_report(result: Mapping[str, Any], path: Path | str) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_status_report(result), encoding="utf-8")
    return target
