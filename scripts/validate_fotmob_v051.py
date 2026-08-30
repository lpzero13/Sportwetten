#!/usr/bin/env python3
"""Run the offline part of the FotMob V0.5.1 validation.

This command never contacts FotMob.  It checks the recorded provider policy,
the seven-slot storage contract and, when explicitly given a local JSON
fixture, the parser's nullable HT/statistics semantics.  Real browser
observations and the provider decision are documented in ``outputs/``; this
helper deliberately does not turn a local fixture into a real coverage claim.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import FOTMOB_AUTOMATED_USAGE_VALUES, FOTMOB_PROVIDER_DECISION_VALUES, Settings
from fotmob.models import FOTMOB_SNAPSHOT_TYPES
from fotmob.parser import parse_fotmob_payload


def _fixture_summary(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, dict) and isinstance(payload.get("payload"), dict):
        payload = payload["payload"]
    if not isinstance(payload, dict):
        raise ValueError("fixture must contain a JSON object")
    match = parse_fotmob_payload(payload)
    return {
        "provider_match_id": match.provider_match_id,
        "competition": match.competition_name,
        "country": match.competition_country,
        "status": match.status,
        "score": [match.score_home, match.score_away],
        "half_time_score": [match.ht_score_home, match.ht_score_away],
        "ht_stats_available": match.ht_stats_available,
        "full_time_stats_available": match.stats.has_any_value(),
    }


def validate(settings: Settings, fixture: Path | None = None) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    policy_valid = (
        settings.fotmob_provider_decision in FOTMOB_PROVIDER_DECISION_VALUES
        and settings.fotmob_automated_usage in FOTMOB_AUTOMATED_USAGE_VALUES
    )
    checks.append({"name": "provider_policy_is_explicit", "passed": policy_valid})

    slots_valid = len(FOTMOB_SNAPSHOT_TYPES) == 7 and len(set(FOTMOB_SNAPSHOT_TYPES)) == 7
    checks.append({"name": "exactly_seven_snapshot_slots", "passed": slots_valid})

    fixture_result: dict[str, Any] | None = None
    if fixture is not None:
        fixture_result = _fixture_summary(fixture)
        checks.append(
            {
                "name": "fixture_parser_and_nullable_ht_fields",
                "passed": bool(
                    fixture_result["provider_match_id"]
                    and fixture_result["ht_stats_available"] is True
                ),
            }
        )

    return {
        "status": "PASS" if all(item["passed"] for item in checks) else "FAIL",
        "network_requests": 0,
        "provider_decision": settings.fotmob_provider_decision,
        "automated_usage": settings.fotmob_automated_usage,
        "automated_worker_allowed": (
            settings.fotmob_enabled
            and settings.fotmob_provider_decision == "PRODUCTION_READY"
            and settings.fotmob_automated_usage == "ACCEPTABLE_FOR_PROJECT"
        ),
        "checks": checks,
        "fixture": fixture_result,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline FotMob V0.5.1 validation")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--fixture",
        type=Path,
        help="optional local JSON fixture; no network request is made",
    )
    args = parser.parse_args()
    settings = Settings.from_env(args.root.resolve())
    try:
        report = validate(settings, args.fixture.resolve() if args.fixture else None)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        report = {"status": "FAIL", "network_requests": 0, "error": str(exc)}
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    raise SystemExit(0 if report["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
