"""Run the V0.1 live validation for a real wall-clock duration.

The script deliberately exercises the same REST client, parsers, persistence
and selected-event detail path used by the application. It is a validation
runner, not the V0.2 collector: it follows one selected event at the detail
cadence and records request, error and database metrics for the final report.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import Settings, configure_logging
from services.event_service import EventService
from services.market_service import MarketService
from storage.database import Database
from storage.raw_storage import RawStorage
from tipico.client import RequestMetrics, TipicoClient


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _metrics_summary(metrics: list[RequestMetrics]) -> dict[str, Any]:
    response_times = [float(item.response_time_ms) for item in metrics]
    payload_sizes = [float(item.payload_size) for item in metrics]
    errors = sum(1 for item in metrics if item.status_code is None or item.status_code >= 400)
    return {
        "requests": len(metrics),
        "http_errors": errors,
        "error_rate": errors / len(metrics) if metrics else None,
        "response_time_ms": {
            "median": statistics.median(response_times) if response_times else None,
            "p95": _percentile(response_times, 0.95),
            "max": max(response_times) if response_times else None,
            "average": statistics.mean(response_times) if response_times else None,
        },
        "payload_bytes": {
            "average": statistics.mean(payload_sizes) if payload_sizes else None,
            "max": max(payload_sizes) if payload_sizes else None,
        },
    }


def _health_check(url: str) -> tuple[int | None, str | None]:
    try:
        response = requests.get(url, timeout=5)
        return response.status_code, None
    except requests.RequestException as exc:
        return None, str(exc)


def _write_status(path: Path, status: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(status, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def run(
    root: Path,
    *,
    event_id: str | None,
    duration_minutes: float,
    feed_interval: float = 10.0,
    detail_interval: float = 5.0,
    streamlit_url: str | None = "http://localhost:8505",
) -> dict[str, Any]:
    settings = Settings.from_env(root)
    logger = configure_logging(settings)
    database = Database(settings.database_path)
    raw_storage = RawStorage(settings.raw_storage_path, enabled=True)
    client = TipicoClient(settings, logger=logger)
    event_service = EventService(client, database, raw_storage, settings, logger=logger)
    market_service = MarketService(client, database, raw_storage, settings, logger=logger)

    feed_metrics: list[RequestMetrics] = []
    detail_metrics: list[RequestMetrics] = []
    streamlit_checks = 0
    streamlit_errors: list[str] = []
    selected_event_ids: list[str] = []
    selected_id = str(event_id) if event_id else None
    started_at = _now_iso()
    started = time.monotonic()
    deadline = started + max(1.0, duration_minutes * 60.0)
    next_feed = started
    next_detail = started
    next_health = started
    status_path = root / "data" / "validation_v01_long.json"
    status: dict[str, Any] = {
        "started_at": started_at,
        "requested_event_id": event_id,
        "duration_minutes_requested": duration_minutes,
        "status": "RUNNING",
    }

    def update_status() -> None:
        elapsed = max(0.0, time.monotonic() - started)
        status.update(
            {
                "runtime_minutes": elapsed / 60.0,
                "selected_event_id": selected_id,
                "selected_event_ids": selected_event_ids,
                "feed": _metrics_summary(feed_metrics),
                "detail": _metrics_summary(detail_metrics),
                "event_service_errors": event_service.error_count,
                "event_service_parsing_errors": event_service.parse_error_count,
                "market_service_errors": market_service.error_count,
                "market_service_parsing_errors": market_service.parse_error_count,
                "streamlit_health_checks": streamlit_checks,
                "streamlit_health_errors": streamlit_errors,
                "database_rows": {
                    table: database.count_rows(table)
                    for table in ("events", "event_states", "markets", "outcomes", "odds_history")
                },
                "updated_at": _now_iso(),
            }
        )
        _write_status(status_path, status)

    try:
        while time.monotonic() < deadline:
            now = time.monotonic()
            if now >= next_feed:
                try:
                    result = event_service.refresh()
                    if result.metrics is not None:
                        feed_metrics.append(result.metrics)
                    if result.success:
                        if selected_id is None or (
                            event_id is None and selected_id not in {item.event_id for item in result.events}
                        ):
                            chosen = max(
                                result.events,
                                key=lambda item: (item.bet_markets_count or 0, item.event_id),
                                default=None,
                            )
                            selected_id = chosen.event_id if chosen else None
                        if selected_id and selected_id not in selected_event_ids:
                            selected_event_ids.append(selected_id)
                except Exception as exc:  # pragma: no cover - defensive runtime guard
                    logger.exception("V0.1 validation feed loop failed")
                    status.setdefault("runtime_errors", []).append(
                        {"kind": "feed_loop", "error": str(exc), "at": _now_iso()}
                    )
                next_feed += feed_interval
                while next_feed <= now:
                    next_feed += feed_interval

            if selected_id and now >= next_detail:
                overview_event = next(
                    (item for item in event_service.events if item.event_id == selected_id),
                    None,
                )
                try:
                    result = market_service.load_event_details(
                        selected_id,
                        overview_event=overview_event,
                    )
                    if result.metrics is not None:
                        detail_metrics.append(result.metrics)
                except Exception as exc:  # pragma: no cover - defensive runtime guard
                    logger.exception("V0.1 validation detail loop failed")
                    status.setdefault("runtime_errors", []).append(
                        {"kind": "detail_loop", "error": str(exc), "at": _now_iso()}
                    )
                next_detail += detail_interval
                while next_detail <= now:
                    next_detail += detail_interval

            if streamlit_url and now >= next_health:
                streamlit_checks += 1
                status_code, error = _health_check(streamlit_url)
                if error or status_code != 200:
                    streamlit_errors.append(
                        f"{_now_iso()}: status={status_code!r} error={error or 'HTTP error'}"
                    )
                next_health += 30.0
                while next_health <= now:
                    next_health += 30.0

            if int(max(0.0, now - started)) % 30 == 0:
                update_status()
            sleep_for = min(
                max(0.2, next_feed - time.monotonic()),
                max(0.2, next_detail - time.monotonic()) if selected_id else 1.0,
                max(0.2, next_health - time.monotonic()) if streamlit_url else 1.0,
                max(0.2, deadline - time.monotonic()),
            )
            time.sleep(sleep_for)
    except KeyboardInterrupt:
        status["interrupted"] = True
    finally:
        status["finished_at"] = _now_iso()
        status["status"] = "COMPLETED" if not status.get("interrupted") else "INTERRUPTED"
        status["runtime_minutes"] = max(0.0, (time.monotonic() - started) / 60.0)
        update_status()
        client.close()
        database.close()

    return status


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Runtime root for database, raw payloads, logs and status JSON",
    )
    parser.add_argument("--event-id", help="Keep polling this live event for detail validation")
    parser.add_argument(
        "--duration-minutes",
        type=float,
        default=60.0,
        help="Real wall-clock runtime; defaults to the required 60 minutes",
    )
    parser.add_argument("--streamlit-url", default="http://localhost:8505")
    parser.add_argument("--no-streamlit-health", action="store_true")
    args = parser.parse_args()
    result = run(
        args.root.resolve(),
        event_id=args.event_id,
        duration_minutes=args.duration_minutes,
        streamlit_url=None if args.no_streamlit_health else args.streamlit_url,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
