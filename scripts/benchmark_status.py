"""Benchmark the collector heartbeat and cached/full status paths."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import Settings
from research.v0611_runtime import source_tree_hash
from services.collector import Collector
from storage.database import Database
from storage.raw_storage import RawStorage


class _NoNetworkClient:
    """The benchmark must never make a provider request."""


def _summary(values: list[float], target_ms: float) -> dict[str, Any]:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(len(ordered) * 0.95) - 1))
    return {
        "iterations": len(values),
        "median_ms": median(values) if values else 0.0,
        "p95_ms": ordered[index] if ordered else 0.0,
        "max_ms": max(values) if values else 0.0,
        "target_p95_ms": float(target_ms),
        "status": "PASS" if ordered and ordered[index] < target_ms else "FAIL",
    }


def benchmark_collector_status(
    collector: Collector,
    *,
    iterations: int = 100,
    heartbeat_target_p95_ms: float = 100.0,
    full_target_p95_ms: float = 500.0,
) -> dict[str, Any]:
    count = max(1, int(iterations))
    # Warm the full cache before measuring the production heartbeat/full
    # cadence.  The explicit uncached sample makes a slow SQL/archive block
    # visible even when the normal status endpoint is cached.
    collector.status(force_refresh=True)
    heartbeat_values: list[float] = []
    full_values: list[float] = []
    uncached_values: list[float] = []
    for _ in range(count):
        started = time.perf_counter()
        collector.heartbeat()
        heartbeat_values.append((time.perf_counter() - started) * 1000.0)
    for _ in range(count):
        started = time.perf_counter()
        collector.status(force_refresh=False)
        full_values.append((time.perf_counter() - started) * 1000.0)
    # A smaller uncached probe is enough to identify a query regression and
    # avoids turning the benchmark itself into a 100x archive walk.
    for _ in range(min(10, count)):
        started = time.perf_counter()
        collector.status(force_refresh=True)
        uncached_values.append((time.perf_counter() - started) * 1000.0)
    heartbeat = _summary(heartbeat_values, heartbeat_target_p95_ms)
    full = _summary(full_values, full_target_p95_ms)
    uncached = _summary(uncached_values, full_target_p95_ms)
    return {
        "status": "PASS" if heartbeat["status"] == "PASS" and full["status"] == "PASS" else "FAIL",
        "heartbeat": heartbeat,
        "full_cached": full,
        "full_uncached_sample": uncached,
        "status_generation_breakdown": collector.status(force_refresh=False).get(
            "status_generation_breakdown", {}
        ),
    }


def write_benchmark_result(root: Path, result: dict[str, Any]) -> Path:
    """Persist the latest explicit benchmark for the release report.

    The file is runtime evidence, not source identity: it lives below
    ``research/runtime`` and is excluded from the deployment tree hash.
    Writing it atomically means a status/report command never reads a partial
    benchmark after a process interruption.
    """

    path = Path(root).resolve() / "research" / "runtime" / "status_benchmark.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(result)
    payload.setdefault("generated_at", datetime.now(timezone.utc).isoformat())
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path)
    parser.add_argument("--iterations", type=int, default=100)
    args = parser.parse_args()
    temporary: tempfile.TemporaryDirectory[str] | None = None
    root = args.root.resolve() if args.root else None
    if root is None:
        temporary = tempfile.TemporaryDirectory(prefix="v0611-status-")
        root = Path(temporary.name)
    settings = Settings(root_dir=root, store_raw_responses=False)
    database = Database(settings.database_path)
    client = _NoNetworkClient()
    collector = Collector(
        client,  # type: ignore[arg-type]
        database,
        RawStorage(settings.raw_storage_path, enabled=False),
        settings,
    )
    try:
        result = benchmark_collector_status(collector, iterations=args.iterations)
        result["root"] = str(root)
        result["source_tree_hash"] = source_tree_hash(root)
        result["benchmark_path"] = str(write_benchmark_result(root, result))
        import json

        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0 if result["status"] == "PASS" else 2
    finally:
        database.close()
        if temporary is not None:
            temporary.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
