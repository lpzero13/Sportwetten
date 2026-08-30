#!/usr/bin/env python3
"""Run a deliberately small FotMob access probe.

The default mode is offline and prints the configured access boundary.  With
``--match-id`` exactly one public match-details request is made when
``FOTMOB_ENABLED=true``.  This is a verification helper, not a bulk crawler.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import Settings
from fotmob.client import FotMobClient
from fotmob.parser import parse_fotmob_payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Moderate FotMob discovery probe")
    parser.add_argument("--match-id", help="one FotMob match ID to probe")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    settings = Settings.from_env(args.root.resolve())
    result: dict[str, object] = {
        "enabled": settings.fotmob_enabled,
        "network_mode": settings.fotmob_network_mode,
        "provider_decision": settings.fotmob_provider_decision,
        "automated_usage": settings.fotmob_automated_usage,
        "base_url": settings.fotmob_base_url,
        "api_base_url": settings.fotmob_api_base_url,
        "match_details_path": settings.fotmob_match_details_path,
        "request_policy": {
            "timeout_seconds": settings.fotmob_timeout_seconds,
            "max_retries": settings.fotmob_max_retries,
            "min_interval_seconds": settings.fotmob_min_request_interval_seconds,
        },
    }
    if not args.match_id:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return
    if not settings.fotmob_enabled:
        result["status"] = "DISABLED"
        result["error"] = "Set FOTMOB_ENABLED=true explicitly for a one-match probe."
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return
    if settings.fotmob_network_mode not in {"manual", "worker"}:
        result["status"] = "BLOCKED_BY_POLICY"
        result["error"] = "Set FOTMOB_NETWORK_MODE=manual for an explicit one-match probe."
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return
    if (
        settings.fotmob_provider_decision == "NOT_SUITABLE"
        or settings.fotmob_automated_usage == "NOT_ACCEPTABLE"
    ):
        result["status"] = "BLOCKED_BY_POLICY"
        result["error"] = "FotMob single-match use is blocked by the provider policy."
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return
    client = FotMobClient(
        base_url=settings.fotmob_base_url,
        api_base_url=settings.fotmob_api_base_url,
        match_details_path=settings.fotmob_match_details_path,
        timeout_seconds=settings.fotmob_timeout_seconds,
        max_retries=settings.fotmob_max_retries,
        min_request_interval_seconds=settings.fotmob_min_request_interval_seconds,
    )
    fetched = client.fetch_match_details(args.match_id)
    result["status"] = "PASS" if fetched.success else "ERROR"
    result["fetch"] = {
        "success": fetched.success,
        "status_code": fetched.status_code,
        "response_time_ms": fetched.response_time_ms,
        "payload_size": fetched.payload_size,
        "endpoint": fetched.endpoint,
        "error": fetched.error,
    }
    if fetched.match is not None:
        result["match"] = fetched.match.to_dict()
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
