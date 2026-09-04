"""Runtime identity and feature-gate reporting for production status output.

The collector has several deliberately conservative gates around optional
FotMob traffic.  This module keeps the gate evaluation in one place so a
configured feature cannot silently appear to be active while its effective
runtime path is blocked.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import time
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any


APP_VERSION = "0.5.9.1"
RESEARCH_VERSION = "0.6.1.1"


_IDENTITY_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_IDENTITY_CACHE_TTL_SECONDS = 30.0


def _json_value(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _git(root_dir: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root_dir), *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    return value or None


def runtime_identity(root_dir: Path) -> dict[str, Any]:
    """Return deploy identity without failing when the source is not a git checkout."""

    root = Path(root_dir).resolve()
    cache_key = str(root)
    cached = _IDENTITY_CACHE.get(cache_key)
    manifest_path = root / "DEPLOYMENT_MANIFEST.json"
    manifest_appeared_since_cache = bool(
        cached is not None
        and not cached[1].get("deployment_manifest_present")
        and manifest_path.exists()
    )
    if (
        cached is not None
        and not manifest_appeared_since_cache
        and time.monotonic() - cached[0] < _IDENTITY_CACHE_TTL_SECONDS
    ):
        return dict(cached[1])
    manifest: dict[str, Any] = {}
    try:
        parsed = json.loads(manifest_path.read_text(encoding="utf-8"))
        if isinstance(parsed, dict):
            manifest = parsed
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        manifest = {}
    inside_work_tree = _git(root, "rev-parse", "--is-inside-work-tree") is not None
    commit = str(
        manifest.get("source_commit")
        or _git(root, "rev-parse", "HEAD")
        or os.getenv("WETTEN_GIT_COMMIT")
        or "unknown"
    )
    branch = str(
        manifest.get("source_branch")
        or _git(root, "branch", "--show-current")
        or os.getenv("WETTEN_GIT_BRANCH")
        or "unknown"
    )
    # Include untracked source files as well: a clean commit with an extra
    # local module is not the same runtime identity as the GitHub checkout.
    porcelain = _git(root, "status", "--porcelain") if inside_work_tree else None
    dirty: bool | str = bool(porcelain) if inside_work_tree else "NOT_APPLICABLE"
    identity = {
        "app_version": os.getenv("WETTEN_APP_VERSION", manifest.get("app_version", APP_VERSION)),
        "research_version": os.getenv("WETTEN_RESEARCH_VERSION", manifest.get("research_version", RESEARCH_VERSION)),
        "git_commit": commit,
        "git_branch": branch,
        "working_tree_dirty": dirty,
        "build_time": os.getenv("WETTEN_BUILD_TIME") or None,
        "deploy_time": os.getenv("WETTEN_DEPLOY_TIME") or manifest.get("deployed_at"),
        "deployed_commit": manifest.get("source_commit"),
        "deployed_branch": manifest.get("source_branch"),
        "deployed_tree_hash": manifest.get("source_tree_hash"),
        "artifact_hash": manifest.get("artifact_hash"),
        "installer_version": manifest.get("installer_version"),
        "deployment_manifest_path": str(manifest_path),
        "deployment_manifest_present": bool(manifest),
        "python_version": manifest.get("python_version") or platform.python_version(),
    }
    _IDENTITY_CACHE[cache_key] = (time.monotonic(), dict(identity))
    return identity


def deployment_manifest_path(root_dir: Path) -> Path:
    """Return the install-time identity manifest path."""

    return Path(root_dir).resolve() / "DEPLOYMENT_MANIFEST.json"


def source_tree_hash(root_dir: Path) -> str:
    """Compute the deterministic source hash used by the deployment manifest."""

    # Keep the implementation in the runtime module as the dashboard also
    # needs to expose the same identity without importing the research factory.
    from research.v0611_runtime import source_tree_hash as _source_tree_hash

    return _source_tree_hash(root_dir)


def load_deployment_manifest(root_dir: Path) -> dict[str, Any] | None:
    path = deployment_manifest_path(root_dir)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
    return dict(value) if isinstance(value, dict) else None


def write_deployment_manifest(
    root_dir: Path,
    *,
    settings: Any | None = None,
    installer_version: str = "v0611",
    research_version: str = RESEARCH_VERSION,
) -> dict[str, Any]:
    from research.v0611_runtime import write_deployment_manifest as _write_manifest

    return _write_manifest(
        root_dir,
        settings=settings,
        installer_version=installer_version,
        research_version=research_version,
    )


def deployment_status(root_dir: Path, *, check_integrity: bool = False) -> dict[str, Any]:
    from research.v0611_runtime import deployment_status as _deployment_status

    return _deployment_status(root_dir, check_integrity=check_integrity)


def config_fingerprint(settings: Any) -> str:
    """Hash non-secret settings that influence feature behavior."""

    values: dict[str, Any] = {}
    if is_dataclass(settings):
        for item in fields(settings):
            name = item.name
            lowered = name.casefold()
            if name == "root_dir" or any(token in lowered for token in ("secret", "token", "password", "api_key")):
                continue
            values[name] = _json_value(getattr(settings, name))
    else:
        values = {
            str(name): _json_value(value)
            for name, value in vars(settings).items()
            if not any(token in str(name).casefold() for token in ("secret", "token", "password", "api_key"))
        }
    # Derived archive paths are operationally relevant but are not dataclass
    # fields, so include them when the settings object exposes them.
    for name in ("archive_path", "fotmob_archive_path", "database_path"):
        try:
            values[name] = _json_value(getattr(settings, name))
        except (AttributeError, TypeError):
            pass
    encoded = json.dumps(values, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _fotmob_worker_gate(settings: Any) -> tuple[bool, str | None, str]:
    if not bool(getattr(settings, "fotmob_enabled", False)):
        return False, "FOTMOB_ENABLED", "FOTMOB_ENABLED=false"
    if not bool(getattr(settings, "fotmob_history_enabled", False)):
        return False, "FOTMOB_HISTORY_ENABLED", "FOTMOB_HISTORY_ENABLED=false"
    mode = str(getattr(settings, "fotmob_network_mode", "off")).casefold()
    if mode != "worker":
        return False, "FOTMOB_NETWORK_MODE", f"FOTMOB_NETWORK_MODE={mode or 'off'} (worker erforderlich)"
    decision = str(getattr(settings, "fotmob_provider_decision", "")).upper()
    if decision != "PRODUCTION_READY":
        return False, "FOTMOB_PROVIDER_DECISION", f"FOTMOB_PROVIDER_DECISION={decision or 'unset'}"
    usage = str(getattr(settings, "fotmob_automated_usage", "")).upper()
    if usage != "ACCEPTABLE_FOR_PROJECT":
        return False, "FOTMOB_AUTOMATED_USAGE", f"FOTMOB_AUTOMATED_USAGE={usage or 'unset'}"
    return True, None, "alle Produktions-Gates erfüllt"


def _fotmob_manual_gate(settings: Any) -> tuple[bool, str | None, str]:
    if not bool(getattr(settings, "fotmob_enabled", False)):
        return False, "FOTMOB_ENABLED", "FOTMOB_ENABLED=false"
    decision = str(getattr(settings, "fotmob_provider_decision", "")).upper()
    if decision == "NOT_SUITABLE":
        return False, "FOTMOB_PROVIDER_DECISION", "Provider als NOT_SUITABLE markiert"
    usage = str(getattr(settings, "fotmob_automated_usage", "")).upper()
    if usage == "NOT_ACCEPTABLE":
        return False, "FOTMOB_AUTOMATED_USAGE", "Provider-Nutzung als NOT_ACCEPTABLE markiert"
    return True, None, "Einzelspielpfad freigegeben"


def feature_runtime_matrix(
    settings: Any,
    *,
    fotmob_service: Any | None = None,
    database: Any | None = None,
) -> list[dict[str, Any]]:
    """Evaluate every material runtime feature and its blocking gate."""

    worker_allowed, worker_gate, worker_reason = _fotmob_worker_gate(settings)
    manual_allowed, manual_gate, manual_reason = _fotmob_manual_gate(settings)
    if fotmob_service is not None:
        worker_allowed = bool(getattr(fotmob_service, "automated_worker_allowed", worker_allowed))
        manual_allowed = bool(getattr(fotmob_service, "manual_use_allowed", manual_allowed))

    def entry(
        feature: str,
        configured: bool,
        effective: bool,
        gate: str | None,
        reason: str,
    ) -> dict[str, Any]:
        return {
            "feature": feature,
            "configured_enabled": bool(configured),
            "effective_enabled": bool(effective),
            "blocking_gate": gate,
            "reason": reason,
        }

    history_configured = bool(
        getattr(settings, "fotmob_enabled", False)
        and getattr(settings, "fotmob_history_enabled", False)
    )
    ht_configured = bool(
        getattr(settings, "fotmob_enabled", False)
        and getattr(settings, "fotmob_ht_enrichment_enabled", False)
    )
    smart_configured = bool(getattr(settings, "smart_universe_enabled", False))
    return [
        entry("tipico_live", True, True, None, "Tipico-Livefeed ist im integrierten Collector aktiv"),
        entry(
            "tipico_prematch",
            True,
            int(getattr(settings, "collector_prematch_refresh_seconds", 0)) > 0,
            "COLLECTOR_PREMATCH_REFRESH_SECONDS" if int(getattr(settings, "collector_prematch_refresh_seconds", 0)) <= 0 else None,
            "Pre-Match-Polling aktiv" if int(getattr(settings, "collector_prematch_refresh_seconds", 0)) > 0 else "Pre-Match-Polling-Intervall ist 0",
        ),
        entry(
            "tipico_details",
            True,
            int(getattr(settings, "collector_detail_workers", 0)) > 0,
            "COLLECTOR_DETAIL_WORKERS" if int(getattr(settings, "collector_detail_workers", 0)) <= 0 else None,
            "Detail-Worker verfügbar" if int(getattr(settings, "collector_detail_workers", 0)) > 0 else "Keine Detail-Worker konfiguriert",
        ),
        entry(
            "snapshot_collector",
            any(
                bool(getattr(settings, name, False))
                for name in (
                    "snapshot_pre_enabled",
                    "snapshot_ht_enabled",
                    "snapshot_60_enabled",
                    "snapshot_70_enabled",
                    "snapshot_80_enabled",
                    "snapshot_85_enabled",
                    "snapshot_90_enabled",
                    "snapshot_final_enabled",
                )
            ),
            True,
            None,
            "Mindestens ein Snapshot-Slot konfiguriert",
        ),
        entry("fotmob_daily_index", history_configured, history_configured and worker_allowed, worker_gate if history_configured and not worker_allowed else None, worker_reason if history_configured else "FotMob-Historie deaktiviert"),
        entry("fotmob_auto_link", bool(getattr(settings, "fotmob_enabled", False)), bool(getattr(settings, "fotmob_enabled", False)) and worker_allowed, worker_gate if getattr(settings, "fotmob_enabled", False) and not worker_allowed else None, worker_reason if getattr(settings, "fotmob_enabled", False) else "FotMob deaktiviert"),
        entry("fotmob_controlled_discovery", bool(getattr(settings, "fotmob_enabled", False)), bool(getattr(settings, "fotmob_enabled", False)) and worker_allowed, worker_gate if getattr(settings, "fotmob_enabled", False) and not worker_allowed else None, worker_reason if getattr(settings, "fotmob_enabled", False) else "FotMob deaktiviert"),
        entry("fotmob_selected_live", bool(getattr(settings, "fotmob_enabled", False)), bool(getattr(settings, "fotmob_enabled", False)) and manual_allowed, manual_gate if getattr(settings, "fotmob_enabled", False) and not manual_allowed else None, manual_reason if getattr(settings, "fotmob_enabled", False) else "FotMob deaktiviert"),
        entry("fotmob_ht_enrichment", ht_configured, ht_configured and worker_allowed, worker_gate if ht_configured and not worker_allowed else None, worker_reason if ht_configured else "FOTMOB_HT_ENRICHMENT_ENABLED=false oder FotMob deaktiviert"),
        entry("smart_live_universe", smart_configured, smart_configured, "SMART_UNIVERSE_ENABLED" if not smart_configured else None, "Smart-Universe aktiv" if smart_configured else "SMART_UNIVERSE_ENABLED=false"),
        entry("paper_trading", True, True, None, "Paper-Trading läuft ohne externen Schreibzugriff"),
        entry("archive_export", True, True, None, "Collector-Archiv und Outbox sind konfiguriert"),
        entry("market_intelligence_persistence", database is not None, database is not None, "DATABASE" if database is None else None, "V0.3-Persistenzpfad verfügbar" if database is not None else "Nicht in diesem Prozess konfiguriert"),
    ]


def feature_health(matrix: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Convert the matrix to compact OK/BLOCKED/DEGRADED health records."""

    health: dict[str, dict[str, Any]] = {}
    for item in matrix:
        if not item["configured_enabled"]:
            state = "BLOCKED"
        elif item["effective_enabled"]:
            state = "OK"
        else:
            state = "DEGRADED"
        health[str(item["feature"])] = {
            "status": state,
            "reason": item["reason"],
            "blocking_gate": item["blocking_gate"],
        }
    return health


def runtime_warnings(matrix: list[dict[str, Any]]) -> list[str]:
    return [
        f"{item['feature']}: konfiguriert, aber effektiv blockiert/degradiert ({item['reason']})"
        for item in matrix
        if item["configured_enabled"] and not item["effective_enabled"]
    ]


__all__ = [
    "APP_VERSION",
    "RESEARCH_VERSION",
    "config_fingerprint",
    "deployment_manifest_path",
    "deployment_status",
    "feature_health",
    "feature_runtime_matrix",
    "load_deployment_manifest",
    "runtime_identity",
    "runtime_warnings",
    "source_tree_hash",
    "write_deployment_manifest",
]
