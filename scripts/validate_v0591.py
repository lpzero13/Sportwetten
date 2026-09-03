"""Run the V0.5.9.1 validation layers and emit machine-readable canary state.

The default invocation is offline and only evaluates the configured runtime
gates.  ``--run-tests`` executes the local unit/integration suite.  The two
network options are explicit because they make real provider requests and can
write the selected runtime database.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import Settings
from fotmob.service import FotMobService
from runtime_status import config_fingerprint, feature_health, feature_runtime_matrix, runtime_identity, runtime_warnings
from storage.database import Database


def _test_command(root: Path) -> tuple[str, int, str]:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
        ],
        cwd=root,
        capture_output=True,
        text=True,
    )
    return (
        "PASS" if result.returncode == 0 else "FAIL",
        result.returncode,
        (result.stdout + result.stderr).strip()[-4000:],
    )


def _local_provider_canary(root: Path) -> dict[str, object]:
    settings = Settings.from_env(root)
    database = Database(settings.database_path)
    try:
        service = FotMobService(settings, database)
        day = datetime.now(ZoneInfo("Europe/Berlin")).date()
        if not service.automated_worker_allowed:
            return {
                "status": "FAIL",
                "date": day.isoformat(),
                "records": 0,
                "error": (
                    "FotMob production gates are not effective; provider canary "
                    "refuses to bypass the configured runtime policy."
                ),
                "metrics": service.metrics(),
            }
        records, error = service.history_pipeline.load_daily_fixture_index(
            day,
            allow_network=service.automated_worker_allowed,
            force=True,
        )
        return {
            "status": (
                "FAIL"
                if error is not None
                else "PASS"
                if records
                else "PENDING"
            ),
            "date": day.isoformat(),
            "records": len(records),
            "error": error,
            "metrics": service.metrics(),
        }
    finally:
        database.close()


def _live_canary(root: Path) -> dict[str, object]:
    """Run the real integrated Tipico/FotMob collector once on this host."""

    collector_script = Path(__file__).resolve().parent / "run_collector.py"
    result = subprocess.run(
        [sys.executable, str(collector_script), "--root", str(root), "--once"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=180,
    )
    payload: dict[str, object] = {}
    try:
        decoded = json.loads(result.stdout)
        if isinstance(decoded, dict):
            payload = decoded
    except json.JSONDecodeError:
        pass
    feed = payload.get("feed") if isinstance(payload.get("feed"), dict) else {}
    event_service = (
        payload.get("event_service")
        if isinstance(payload.get("event_service"), dict)
        else {}
    )
    universe = (
        payload.get("smart_live_universe")
        if isinstance(payload.get("smart_live_universe"), dict)
        else {}
    )
    feed_requests = int(feed.get("requests") or 0)
    feed_errors = int(feed.get("errors") or 0)
    feed_parsing_errors = int(feed.get("parsing_errors") or 0)
    plausibility_errors = int(event_service.get("plausibility_errors") or 0)
    live_event_count = int(universe.get("total_live_events") or 0)
    if result.returncode != 0:
        status = "FAIL"
        reason = "collector process failed"
    elif feed_requests < 1:
        status = "FAIL"
        reason = "no live-feed request was recorded"
    elif feed_errors or feed_parsing_errors or plausibility_errors:
        status = "FAIL"
        reason = "live-feed request was not accepted cleanly"
    elif live_event_count < 1:
        # A clean response without an active football event is a valid
        # observation, but it cannot prove the event-level E2E path yet.
        status = "PENDING"
        reason = "no active Tipico football event was available for this canary"
    else:
        status = "PASS"
        reason = "active Tipico football event observed by integrated collector"
    return {
        "status": status,
        "reason": reason,
        "returncode": result.returncode,
        "feed_requests": feed_requests,
        "feed_errors": feed_errors,
        "feed_parsing_errors": feed_parsing_errors,
        "plausibility_errors": plausibility_errors,
        "live_event_count": live_event_count,
        "collector_status": payload,
        "output_tail": (result.stdout + result.stderr).strip()[-4000:],
    }


def build_report(
    root: Path,
    *,
    run_tests: bool,
    local_provider: bool,
    live_canary: bool,
    ct110_live_canary: bool,
) -> dict[str, object]:
    settings = Settings.from_env(root)
    matrix = feature_runtime_matrix(settings)
    identity = runtime_identity(root)
    report: dict[str, object] = {
        "app_version": identity["app_version"],
        "git_commit": identity["git_commit"],
        "git_branch": identity["git_branch"],
        "working_tree_dirty": identity["working_tree_dirty"],
        "runtime_identity": identity,
        "config_fingerprint": config_fingerprint(settings),
        "feature_runtime_matrix": matrix,
        "feature_health": feature_health(matrix),
        "runtime_warnings": runtime_warnings(matrix),
        "UNIT": "NOT_RUN",
        "INTEGRATION": "NOT_RUN",
        "FULL_TEST_SUITE": "NOT_RUN",
        "LOCAL_PROVIDER": "NOT_RUN",
        "LOCAL_LIVE_E2E": "NOT_RUN",
        "CT110_LIVE_CANARY": "NOT_RUN",
    }
    if run_tests:
        test_status, returncode, output = _test_command(root)
        report["UNIT"] = test_status
        report["INTEGRATION"] = test_status
        report["FULL_TEST_SUITE"] = test_status
        report["test_returncode"] = returncode
        report["test_output_tail"] = output
    if local_provider:
        try:
            provider = _local_provider_canary(root)
        except Exception as exc:  # canary output must remain machine-readable
            provider = {"status": "FAIL", "error": str(exc)}
        report["LOCAL_PROVIDER"] = provider
    if live_canary:
        try:
            live = _live_canary(root)
        except Exception as exc:  # canary output must remain machine-readable
            live = {"status": "FAIL", "error": str(exc)}
        report["LOCAL_LIVE_E2E"] = live
    if ct110_live_canary:
        try:
            live = _live_canary(root)
        except Exception as exc:  # canary output must remain machine-readable
            live = {"status": "FAIL", "error": str(exc)}
        report["CT110_LIVE_CANARY"] = live
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--run-tests", action="store_true", help="Run the local V0.5.9.1 regression layers.")
    parser.add_argument("--local-provider", action="store_true", help="Make one real FotMob daily-index request.")
    parser.add_argument("--live-canary", action="store_true", help="Run the real integrated collector once on this host.")
    parser.add_argument("--ct110-live-canary", action="store_true", help="Run the real collector canary on the current CT110 host.")
    parser.add_argument("--output", type=Path, help="Write the JSON report to this path as well as stdout.")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    report = build_report(
        root,
        run_tests=args.run_tests,
        local_provider=args.local_provider,
        live_canary=args.live_canary,
        ct110_live_canary=args.ct110_live_canary,
    )
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
