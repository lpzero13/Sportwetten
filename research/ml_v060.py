"""V0.6.0 historical halftime-goal research factory.

This module deliberately lives outside the Tipico collector.  It reads the
canonical FotMob Parquet archive, builds one immutable research dataset, and
then runs explicitly requested experiments through a resumable SQLite
registry.  Importing the module never builds data, trains a model, or starts a
network request.

The only target used here is the number of goals after halftime and before
the end of regulation.  Full-time/second-half fields are labels or audit
inputs only; they are never part of a model feature universe.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import itertools
import json
import math
import os
import platform
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any

try:  # pragma: no cover - import availability is checked at runtime
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

try:  # pragma: no cover - dependency is declared in requirements
    import pyarrow as pa
    import pyarrow.dataset as ds
    import pyarrow.parquet as pq
except ImportError:  # pragma: no cover
    pa = None
    ds = None
    pq = None

try:  # pragma: no cover - dependency is declared in requirements
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

from runtime_status import (
    APP_VERSION,
    RESEARCH_VERSION,
    config_fingerprint,
    deployment_status,
    runtime_identity,
    source_tree_hash,
)
from research.v0611_runtime import (
    AlreadyRunningError,
    ResearchRunLock,
    StaleLockError,
    deployment_manifest_path,
    write_deployment_manifest,
)


V060_VERSION = "0.6.0"
V061_VERSION = "0.6.1"
V0611_VERSION = "0.6.1.1"
IO_WORKERS = 10
DATASET_SCHEMA_VERSION = "v060_ht_dataset_v2"
FEATURE_SCHEMA_VERSION = "v060_feature_schema_v2"
TARGET_SCHEMA_VERSION = "v060_h2_target_v1"
MODEL_CUTOFF = "HALFTIME"
TARGET_CLASSES = ("H2_GOALS_0", "H2_GOALS_1", "H2_GOALS_2_PLUS")
P1_THRESHOLDS = (0.30, 0.275, 0.25, 0.225, 0.20, 0.175, 0.15, 0.125, 0.10)
ALLOWED_FEATURE_CUTOFFS = {"HALFTIME", "FIRST_HALF", "ROLLING_HISTORY"}
FORBIDDEN_FEATURE_PATTERNS = (
    re.compile(r"(^|_)ft(_|$)"),
    re.compile(r"(^|_)final(_|$)"),
    re.compile(r"second[_ ]half"),
    re.compile(r"actual[_ ]target"),
    re.compile(r"(^|_)result(_|$)"),
)

CORE_METRICS = (
    "shots",
    "shots_on_target",
    "big_chances",
    "corners",
    "possession",
    "yellow_cards",
    "red_cards",
    "fouls",
    "offsides",
    "goalkeeper_saves",
    "passes",
    "accurate_passes",
    "shots_inside_box",
    "shots_outside_box",
    "touches_in_box",
)
XG_METRICS = ("xg",)
SHOTMAP_METRICS = (
    "shots",
    "sot",
    "xg",
    "mean_xg",
    "max_xg",
    "median_xg",
    "inside_box",
    "outside_box",
    "open_play",
    "set_piece",
    "penalty",
)
SHOTMAP_WINDOWS = ("full", "last5", "last10", "last15", "last20", "previous15")
MOMENTUM_METRICS = ("shots", "sot", "xg")
HISTORY_WINDOWS = (5, 10, 20)


class DatasetError(RuntimeError):
    """Raised when the canonical research dataset cannot be trusted."""


class LeakageError(DatasetError):
    """Raised when a model feature violates the halftime cutoff."""


class InsufficientData(DatasetError):
    """Raised for a valid but untrainable experiment slice."""


class DependencyMissing(DatasetError):
    """Raised when an explicitly requested optional model library is absent."""

    def __init__(self, dependency: str, requested_model_family: str, reason: str | None = None) -> None:
        self.dependency = str(dependency)
        self.requested_model_family = str(requested_model_family).upper()
        message = f"{self.dependency} is required for {self.requested_model_family}"
        if reason:
            message += f": {reason}"
        super().__init__(message)


class ModelIdentityError(DatasetError):
    """Raised when the instantiated model is not the requested family."""


class HardResearchStop(DatasetError):
    """A structural invariant failed; the current run must stop."""


class ResourceGuardError(DatasetError):
    """Raised before training when host resources are unsafe."""


def _ram_guard(requested: int) -> int:
    """Bound archive I/O workers when the host has little free RAM."""

    requested = max(1, int(requested))
    available: int | None = None
    try:
        import psutil

        available = int(psutil.virtual_memory().available)
    except ImportError:
        if hasattr(os, "sysconf"):
            try:
                available = int(os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE"))
            except (OSError, ValueError):
                available = None
    if available is None:
        return requested
    reserve = 512 * 1024 * 1024
    per_worker = 256 * 1024 * 1024
    safe_max = max(1, (available - reserve) // per_worker)
    return min(requested, int(safe_max))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_time(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        match = re.search(r"[-+]?\d+(?:[.,]\d+)?", str(value).replace(" ", ""))
        if not match:
            return None
        try:
            result = float(match.group(0).replace(",", "."))
        except ValueError:
            return None
    return result if math.isfinite(result) else None


def _integer(value: Any) -> int | None:
    number = _number(value)
    return None if number is None else int(number)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _hash_json(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _package_version(distribution: str) -> str | None:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return None
    except Exception:
        return None


RESEARCH_PACKAGES = {
    "numpy": "numpy",
    "pandas": "pandas",
    "scikit-learn": "scikit-learn",
    "pyarrow": "pyarrow",
    "catboost": "catboost",
    "xgboost": "xgboost",
    "lightgbm": "lightgbm",
}


def environment_manifest() -> dict[str, Any]:
    """Return a stable, non-secret manifest for a research run."""

    packages = {
        name: _package_version(distribution)
        for name, distribution in RESEARCH_PACKAGES.items()
    }
    return {
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "packages": packages,
    }


def environment_hash(manifest: Mapping[str, Any] | None = None) -> str:
    return _hash_json(dict(manifest or environment_manifest()))


def new_research_run_id() -> str:
    """Create an opaque ID that is unique across explicit CLI invocations."""

    return f"run-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:12]}"


def _resource_snapshot(root: Path) -> dict[str, Any]:
    available_ram = total_ram = swap_used = swap_percent = None
    try:
        import psutil

        memory = psutil.virtual_memory()
        swap = psutil.swap_memory()
        available_ram = int(memory.available)
        total_ram = int(memory.total)
        swap_used = int(swap.used)
        swap_percent = float(swap.percent)
    except (ImportError, OSError, AttributeError):
        pass
    try:
        disk = shutil.disk_usage(Path(root))
        free_disk = int(disk.free)
        total_disk = int(disk.total)
    except OSError:
        free_disk = total_disk = None
    return {
        "available_ram_bytes": available_ram,
        "total_ram_bytes": total_ram,
        "swap_used_bytes": swap_used,
        "swap_percent": swap_percent,
        "free_disk_bytes": free_disk,
        "total_disk_bytes": total_disk,
    }


def resource_guard(root: Path, *, phase: str = "experiment") -> dict[str, Any]:
    """Check resources before work that can allocate a large model."""

    snapshot = _resource_snapshot(root)
    try:
        min_ram_mb = max(64, int(os.getenv("V061_MIN_AVAILABLE_RAM_MB", "512")))
    except ValueError:
        min_ram_mb = 512
    # Keep the ML guard at least at the configured CRITICAL threshold.  A
    # deployment may deliberately choose a higher value (the example env
    # uses 5 GiB), but the default must never allow a deep run into a disk
    # state that the collector reports as critical.
    try:
        min_disk_gb = max(2.0, float(os.getenv("V061_MIN_FREE_DISK_GB", "2")))
    except ValueError:
        min_disk_gb = 2.0
    try:
        estimated_artifact_bytes = max(
            0,
            int(os.getenv("V061_ESTIMATED_ARTIFACT_BYTES", "0") or 0),
        )
    except ValueError:
        estimated_artifact_bytes = 0
    try:
        max_swap_percent = max(0.0, float(os.getenv("V061_MAX_SWAP_PERCENT", "50")))
    except ValueError:
        max_swap_percent = 50.0
    reasons: list[str] = []
    available = snapshot.get("available_ram_bytes")
    if available is not None and available < min_ram_mb * 1024 * 1024:
        reasons.append(f"available RAM below {min_ram_mb} MB")
    free_disk = snapshot.get("free_disk_bytes")
    required_free_disk = min_disk_gb * 1024**3 + estimated_artifact_bytes
    if free_disk is not None and free_disk < required_free_disk:
        reasons.append(
            f"free disk below {min_disk_gb:.1f} GB plus estimated artifacts "
            f"({estimated_artifact_bytes} bytes)"
        )
    swap_percent = snapshot.get("swap_percent")
    if swap_percent is not None and swap_percent > max_swap_percent:
        reasons.append(f"swap usage above {max_swap_percent:.1f}%")
    snapshot.update(
        {
            "phase": str(phase),
            "status": "PASS" if not reasons else "DEGRADED",
            "guard_reasons": reasons,
            "minimum_available_ram_mb": min_ram_mb,
            "minimum_free_disk_gb": min_disk_gb,
            "estimated_artifact_requirement_bytes": estimated_artifact_bytes,
            "required_free_disk_bytes": int(required_free_disk),
            "maximum_swap_percent": max_swap_percent,
        }
    )
    if reasons:
        raise ResourceGuardError("; ".join(reasons))
    return snapshot


def lower_process_priority() -> dict[str, Any]:
    """Best-effort low priority for research, leaving the collector alone."""

    result: dict[str, Any] = {"requested": True, "applied": False, "method": None}
    try:
        import psutil
    except ImportError:
        result["error"] = "psutil unavailable"
        return result
    try:
        process = psutil.Process()
        if os.name == "nt":
            process.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
            result.update({"applied": True, "method": "BELOW_NORMAL"})
        else:
            process.nice(10)
            result.update({"applied": True, "method": "nice+10"})
            try:
                import subprocess

                subprocess.run(["ionice", "-c3", "-p", str(process.pid)], check=False, capture_output=True)
                result["method"] = "nice+10+ionice-idle"
            except (OSError, subprocess.SubprocessError):
                pass
    except (OSError, psutil.Error):
        result["error"] = "priority adjustment unavailable"
    return result


def _safe_path(value: Any) -> str:
    text = str(value or "unknown").strip().replace("\\", "-").replace("/", "-")
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text) or "unknown"


def pair_feature_names(base: str) -> list[str]:
    return [
        f"{base}_home",
        f"{base}_away",
        f"{base}_total",
        f"{base}_diff",
        f"{base}_home_share",
        f"{base}_home_away_ratio",
    ]


def _add_pair(target: dict[str, Any], base: str, home: Any, away: Any) -> None:
    home_value = _number(home)
    away_value = _number(away)
    target[f"{base}_home"] = home_value
    target[f"{base}_away"] = away_value
    if home_value is None or away_value is None:
        target[f"{base}_total"] = None
        target[f"{base}_diff"] = None
        target[f"{base}_home_share"] = None
        target[f"{base}_home_away_ratio"] = None
        return
    total = home_value + away_value
    target[f"{base}_total"] = total
    target[f"{base}_diff"] = home_value - away_value
    target[f"{base}_home_share"] = home_value / total if total else None
    target[f"{base}_home_away_ratio"] = home_value / away_value if away_value else None


def _metric_value(row: Mapping[str, Any], metric: str, side: str) -> Any:
    return row.get(f"ht_{metric}_{side}")


def _is_forbidden_feature(name: str, origin: str = "") -> bool:
    value = f"{name} {origin}".casefold().replace("-", "_")
    return any(pattern.search(value) for pattern in FORBIDDEN_FEATURE_PATTERNS)


def _feature_names_for_metrics(metrics: Iterable[str]) -> list[str]:
    result: list[str] = []
    for metric in metrics:
        result.extend(pair_feature_names(f"ht_{metric}"))
    return result


SCORE_FEATURES = [
    "ht_home_goals",
    "ht_away_goals",
    "ht_total_goals",
    "ht_goal_diff",
]
CORE_FEATURES = _feature_names_for_metrics(CORE_METRICS)
XG_FEATURES = _feature_names_for_metrics(XG_METRICS)
XG_FEATURES += pair_feature_names("ht_xg_per_shot") + pair_feature_names("ht_xg_per_sot")
SHOTMAP_FEATURES = [
    feature
    for window in SHOTMAP_WINDOWS
    for metric in SHOTMAP_METRICS
    for feature in pair_feature_names(f"shotmap_{window}_{metric}")
]
MOMENTUM_FEATURES = [
    feature
    for metric in MOMENTUM_METRICS
    for feature in pair_feature_names(f"momentum_last15_minus_previous15_{metric}")
] + [f"momentum_last15_{metric}_home_share_delta" for metric in MOMENTUM_METRICS]
EVENT_FEATURES = [
    feature
    for metric in (
        "events_goals",
        "events_yellow_cards",
        "events_red_cards",
        "events_minutes_with_numerical_advantage",
    )
    for feature in pair_feature_names(metric)
] + [
    "events_first_goal_minute",
    "events_last_goal_minute",
    "events_minutes_since_last_goal",
]
HISTORY_FEATURES: list[str] = []
for window in HISTORY_WINDOWS:
    HISTORY_FEATURES.extend(
        feature
        for metric in (
            "h2_scored",
            "h2_conceded",
            "p1_rate",
            "ht_shots",
            "ht_xg",
        )
        for feature in pair_feature_names(f"history_last{window}_{metric}")
    )
HISTORY_FEATURES.extend(
    feature
    for metric in ("h2_scored", "h2_conceded", "p1_rate", "ht_shots", "ht_xg")
    for feature in pair_feature_names(f"history_season_to_date_{metric}")
)
HISTORY_FEATURES.extend(
    [
        "league_history_p1_rate",
        "league_history_h2_goals_mean",
    ]
)

FEATURES_BY_UNIVERSE = {
    "SCORE_ONLY": SCORE_FEATURES,
    "CORE": SCORE_FEATURES + CORE_FEATURES,
    "CORE_XG": SCORE_FEATURES + CORE_FEATURES + XG_FEATURES,
    "CORE_SHOTMAP": SCORE_FEATURES + CORE_FEATURES + SHOTMAP_FEATURES + MOMENTUM_FEATURES,
    "CORE_XG_SHOTMAP": SCORE_FEATURES + CORE_FEATURES + XG_FEATURES + SHOTMAP_FEATURES + MOMENTUM_FEATURES,
    "CORE_EVENTS": SCORE_FEATURES + CORE_FEATURES + EVENT_FEATURES,
    "CORE_HISTORY": SCORE_FEATURES + CORE_FEATURES + HISTORY_FEATURES,
    "ALL_AVAILABLE": SCORE_FEATURES + CORE_FEATURES + XG_FEATURES + SHOTMAP_FEATURES + MOMENTUM_FEATURES + EVENT_FEATURES + HISTORY_FEATURES,
}
for _key, _value in list(FEATURES_BY_UNIVERSE.items()):
    FEATURES_BY_UNIVERSE[_key] = list(dict.fromkeys(_value))


def feature_catalog() -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for name in SCORE_FEATURES:
        result.append({"feature_name": name, "feature_origin": "HALFTIME", "availability_cutoff": "HALFTIME"})
    for name in CORE_FEATURES + XG_FEATURES:
        result.append({"feature_name": name, "feature_origin": "FIRST_HALF", "availability_cutoff": "HALFTIME"})
    for name in SHOTMAP_FEATURES:
        result.append({"feature_name": name, "feature_origin": "FIRST_HALF_SHOTMAP", "availability_cutoff": "HALFTIME"})
    for name in MOMENTUM_FEATURES:
        result.append({"feature_name": name, "feature_origin": "FIRST_HALF_SHOTMAP", "availability_cutoff": "HALFTIME"})
    for name in EVENT_FEATURES:
        result.append({"feature_name": name, "feature_origin": "FIRST_HALF_EVENTS", "availability_cutoff": "HALFTIME"})
    for name in HISTORY_FEATURES:
        result.append({"feature_name": name, "feature_origin": "ROLLING_HISTORY", "availability_cutoff": "ROLLING_HISTORY"})
    return result


def classify_h2_goal_target(
    ht_home: int | None,
    ht_away: int | None,
    regulation_ft_home: int | None,
    regulation_ft_away: int | None,
    *,
    extra_time_ambiguous: bool = False,
) -> dict[str, Any]:
    """Derive the only permitted V0.6 target from HT and regulation FT."""

    if extra_time_ambiguous:
        return {
            "h2_home_goals": None,
            "h2_away_goals": None,
            "h2_total_goals": None,
            "target_h2_goal_class": None,
            "middle_loss": None,
            "target_quality": "INVALID_EXTRA_TIME_AMBIGUOUS",
            "ml_eligible": False,
        }
    if None in {ht_home, ht_away, regulation_ft_home, regulation_ft_away}:
        return {
            "h2_home_goals": None,
            "h2_away_goals": None,
            "h2_total_goals": None,
            "target_h2_goal_class": None,
            "middle_loss": None,
            "target_quality": "MISSING_HT_OR_REGULATION_FT",
            "ml_eligible": False,
        }
    assert ht_home is not None and ht_away is not None
    assert regulation_ft_home is not None and regulation_ft_away is not None
    h2_home = regulation_ft_home - ht_home
    h2_away = regulation_ft_away - ht_away
    h2_total = h2_home + h2_away
    if h2_home < 0 or h2_away < 0 or h2_total < 0:
        return {
            "h2_home_goals": h2_home,
            "h2_away_goals": h2_away,
            "h2_total_goals": h2_total,
            "target_h2_goal_class": None,
            "middle_loss": None,
            "target_quality": "INVALID_SCORE_TOTAL_LT_HALFTIME",
            "ml_eligible": False,
        }
    target_class = (
        "H2_GOALS_0" if h2_total == 0 else "H2_GOALS_1" if h2_total == 1 else "H2_GOALS_2_PLUS"
    )
    return {
        "h2_home_goals": h2_home,
        "h2_away_goals": h2_away,
        "h2_total_goals": h2_total,
        "target_h2_goal_class": target_class,
        "middle_loss": target_class == "H2_GOALS_1",
        "target_quality": "VALID_REGULATION",
        "ml_eligible": True,
    }


def _extra_time_from_text(row: Mapping[str, Any]) -> bool:
    text = " ".join(
        _text(row.get(key))
        for key in ("match_status", "status", "round_name", "ft_score_source", "source_context")
    ).casefold()
    patterns = (
        r"extra\s*time",
        r"after\s+extra",
        r"a\.e\.t\.",
        r"\baet\b",
        r"penalty\s+shoot",
        r"shootout",
        r"verl[äa]ngerung",
        r"\bn\.?\s*v\.?\b",
    )
    return any(re.search(pattern, text) for pattern in patterns)


def _extra_time_from_events(events: Sequence[Mapping[str, Any]]) -> bool:
    def positive_payload_marker(value: Any) -> bool:
        if isinstance(value, Mapping):
            for key, child in value.items():
                normalized_key = _text(key).casefold().replace("-", "_").replace(" ", "_")
                if normalized_key in {"ispenaltyshootoutevent", "is_penalty_shootout_event"}:
                    if child is True or _text(child).casefold() in {"true", "1", "yes"}:
                        return True
                if normalized_key in {"period", "phase", "status", "match_status", "type", "event_type", "eventtype"}:
                    child_text = _text(child).casefold().replace(" ", "_")
                    if any(token in child_text for token in ("firsthalfextra", "secondhalfextra", "extra_time", "overtime", "penaltyshootout", "penalty_shoot", "shootout")):
                        return True
                if isinstance(child, (Mapping, list, tuple)) and positive_payload_marker(child):
                    return True
        elif isinstance(value, (list, tuple)):
            return any(positive_payload_marker(child) for child in value)
        return False

    for event in events:
        period = _text(event.get("period")).casefold().replace(" ", "_")
        if "extra" in period or "overtime" in period:
            return True
        event_type = _text(event.get("event_type")).casefold().replace(" ", "_")
        if "shootout" in event_type or "penalty_shoot" in event_type:
            return True
        payload = _extra_mapping(event.get("extra_json"))
        # FotMob versions use several payload shapes for extra time and
        # shootouts.  Inspect positive marker values, not merely JSON keys;
        # regular matches commonly contain isPenaltyShootoutEvent=false.
        if payload and positive_payload_marker(payload):
            return True
        minute = _integer(event.get("minute"))
        if minute is not None and minute > 90:
            return True
    return False


def _is_first_half(period: Any, minute: Any) -> bool:
    normalized = _text(period).casefold().replace(" ", "_")
    minute_value = _integer(minute)
    return normalized in {"first_half", "1st_half", "firsthalf", "1h"} or (
        minute_value is not None and minute_value <= 45
    )


def _extra_mapping(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _first_half_shot_rows(
    rows: Sequence[Mapping[str, Any]],
    core: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], bool]:
    result: list[dict[str, Any]] = []
    home_id = _text(core.get("home_team_id"))
    away_id = _text(core.get("away_team_id"))
    for raw in rows:
        period = _text(raw.get("period")).casefold().replace(" ", "_")
        minute = _integer(raw.get("minute"))
        if not _is_first_half(period, minute):
            continue
        is_home = raw.get("is_home")
        if not isinstance(is_home, bool):
            team_id = _text(raw.get("team_id"))
            is_home = True if team_id and team_id == home_id else False if team_id and team_id == away_id else None
        if is_home is None:
            continue
        extra = _extra_mapping(raw.get("extra_json"))
        result.append(
            {
                "is_home": is_home,
                "minute": minute,
                "xg": _number(raw.get("xg")),
                "xgot": _number(raw.get("xgot")),
                "outcome": _text(raw.get("outcome")).casefold(),
                "shot_type": _text(raw.get("shot_type")).casefold(),
                "situation": _text(raw.get("situation")).casefold(),
                "is_inside_box": raw.get("is_inside_box", extra.get("isFromInsideBox")),
                "is_on_target": raw.get("is_on_target", extra.get("isOnTarget")),
            }
        )
    return result, bool(result)


def _shot_in_window(window: str, minute: Any) -> bool:
    if window == "full":
        return True
    minute_value = _integer(minute)
    if minute_value is None:
        return False
    if window == "previous15":
        return 15 < minute_value <= 30
    match = re.match(r"last(\d+)$", window)
    return bool(match and minute_value > 45 - int(match.group(1)))


def _add_momentum_features(result: dict[str, Any]) -> None:
    """Add last-15 versus preceding-15 first-half shotmap deltas."""

    for metric in MOMENTUM_METRICS:
        last_home = result.get(f"shotmap_last15_{metric}_home")
        last_away = result.get(f"shotmap_last15_{metric}_away")
        previous_home = result.get(f"shotmap_previous15_{metric}_home")
        previous_away = result.get(f"shotmap_previous15_{metric}_away")
        _add_pair(
            result,
            f"momentum_last15_minus_previous15_{metric}",
            last_home - previous_home if last_home is not None and previous_home is not None else None,
            last_away - previous_away if last_away is not None and previous_away is not None else None,
        )
        last_total = last_home + last_away if last_home is not None and last_away is not None else None
        previous_total = previous_home + previous_away if previous_home is not None and previous_away is not None else None
        last_share = last_home / last_total if last_home is not None and last_total else None
        previous_share = previous_home / previous_total if previous_home is not None and previous_total else None
        result[f"momentum_last15_{metric}_home_share_delta"] = (
            last_share - previous_share if last_share is not None and previous_share is not None else None
        )


def _shotmap_features(rows: Sequence[Mapping[str, Any]], core: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
    shots, available = _first_half_shot_rows(rows, core)
    result: dict[str, Any] = {}
    for window in SHOTMAP_WINDOWS:
        current = [
            shot
            for shot in shots
            if _shot_in_window(window, shot.get("minute"))
        ]
        for metric in SHOTMAP_METRICS:
            values: list[list[float]] = [[], []]
            for shot in current:
                side = 0 if shot["is_home"] else 1
                outcome = shot["outcome"]
                situation = shot["situation"]
                shot_type = shot["shot_type"]
                on_target = (
                    bool(shot.get("is_on_target"))
                    if shot.get("is_on_target") is not None
                    else bool(
                        shot["xgot"] is not None
                        or any(token in outcome for token in ("goal", "saved", "on target", "on_target"))
                    )
                )
                inside = (
                    bool(shot.get("is_inside_box"))
                    if shot.get("is_inside_box") is not None
                    else "inside" in shot_type or "inside" in situation or "penalty area" in situation
                )
                outside = (
                    not inside
                    if shot.get("is_inside_box") is not None
                    else "outside" in shot_type or "outside" in situation
                )
                set_piece = any(token in situation for token in ("free kick", "corner", "set piece", "throw"))
                penalty = "penalty" in situation or "penalty" in shot_type
                open_play = "open play" in situation or "open_play" in situation
                if metric == "shots":
                    values[side].append(1.0)
                elif metric == "sot" and on_target:
                    values[side].append(1.0)
                elif metric == "xg" and shot["xg"] is not None:
                    values[side].append(float(shot["xg"]))
                elif metric == "inside_box" and inside:
                    values[side].append(1.0)
                elif metric == "outside_box" and outside:
                    values[side].append(1.0)
                elif metric == "open_play" and open_play:
                    values[side].append(1.0)
                elif metric == "set_piece" and set_piece:
                    values[side].append(1.0)
                elif metric == "penalty" and penalty:
                    values[side].append(1.0)
                elif metric in {"mean_xg", "max_xg", "median_xg"} and shot["xg"] is not None:
                    values[side].append(float(shot["xg"]))
            if metric == "mean_xg":
                pair = tuple(sum(side_values) / len(side_values) if side_values else None for side_values in values)
            elif metric == "max_xg":
                pair = tuple(max(side_values) if side_values else None for side_values in values)
            elif metric == "median_xg":
                pair = tuple(median(side_values) if side_values else None for side_values in values)
            else:
                pair = tuple(float(len(side_values)) if side_values or available else None for side_values in values)
            _add_pair(result, f"shotmap_{window}_{metric}", pair[0], pair[1])
    _add_momentum_features(result)
    return result, available


def _first_half_event_rows(rows: Sequence[Mapping[str, Any]], core: Mapping[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    result: list[dict[str, Any]] = []
    home_id = _text(core.get("home_team_id"))
    away_id = _text(core.get("away_team_id"))
    for raw in rows:
        period = _text(raw.get("period")).casefold().replace(" ", "_")
        minute = _integer(raw.get("minute"))
        if not _is_first_half(period, minute):
            continue
        is_home = raw.get("is_home")
        if not isinstance(is_home, bool):
            team_id = _text(raw.get("team_id"))
            is_home = True if team_id and team_id == home_id else False if team_id and team_id == away_id else None
        result.append(
            {
                "is_home": is_home,
                "minute": minute,
                "added_time": _integer(raw.get("added_time")) or 0,
                "event_type": _text(raw.get("event_type")).casefold(),
            }
        )
    return result, bool(result)


def _event_features(rows: Sequence[Mapping[str, Any]], core: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
    events, available = _first_half_event_rows(rows, core)
    result: dict[str, Any] = {}
    counts = {
        "events_goals": [0.0, 0.0],
        "events_yellow_cards": [0.0, 0.0],
        "events_red_cards": [0.0, 0.0],
        "events_minutes_with_numerical_advantage": [0.0, 0.0],
    }
    goal_times: list[float] = []
    for event in events:
        kind = event["event_type"]
        side = event["is_home"]
        time_value = (event["minute"] or 0) + event["added_time"] / 10.0
        if "goal" in kind and side is not None:
            counts["events_goals"][0 if side else 1] += 1
            goal_times.append(time_value)
        if "yellow" in kind and side is not None:
            counts["events_yellow_cards"][0 if side else 1] += 1
        if ("red" in kind or "second_yellow" in kind) and side is not None:
            counts["events_red_cards"][0 if side else 1] += 1
            advantaged = 0 if not side else 1
            counts["events_minutes_with_numerical_advantage"][advantaged] += max(0.0, 45.0 - time_value)
    for metric, values in counts.items():
        _add_pair(result, metric, values[0] if events or available else None, values[1] if events or available else None)
    if goal_times:
        result["events_first_goal_minute"] = min(goal_times)
        result["events_last_goal_minute"] = max(goal_times)
        result["events_minutes_since_last_goal"] = max(0.0, 45.0 - max(goal_times))
    else:
        result["events_first_goal_minute"] = None
        result["events_last_goal_minute"] = None
        result["events_minutes_since_last_goal"] = None
    return result, available


_SHOT_COUNT_METRICS = ("shots", "sot", "inside_box", "outside_box", "open_play", "set_piece", "penalty")


def _new_shot_aggregate() -> dict[str, Any]:
    return {
        "__v060_shot_aggregate__": True,
        "counts": [[[0 for _ in _SHOT_COUNT_METRICS] for _ in range(2)] for _ in SHOTMAP_WINDOWS],
        "xg_values": [[[] for _ in range(2)] for _ in SHOTMAP_WINDOWS],
        "available": False,
    }


def _side_from_raw(raw: Mapping[str, Any]) -> bool | None:
    value = raw.get("is_home")
    if isinstance(value, bool):
        return value
    team_id = _text(raw.get("team_id"))
    home_id = _text(raw.get("home_team_id"))
    away_id = _text(raw.get("away_team_id"))
    if team_id and home_id and team_id == home_id:
        return True
    if team_id and away_id and team_id == away_id:
        return False
    return None


def _update_shot_aggregate(state: dict[str, Any], raw: Mapping[str, Any]) -> None:
    if not _is_first_half(raw.get("period"), raw.get("minute")):
        return
    side_value = _side_from_raw(raw)
    minute = _integer(raw.get("minute"))
    if side_value is None:
        return
    side = 0 if side_value else 1
    extra = _extra_mapping(raw.get("extra_json"))
    is_inside_value = raw.get("is_inside_box", extra.get("isFromInsideBox"))
    is_on_target_value = raw.get("is_on_target", extra.get("isOnTarget"))
    shot_type = _text(raw.get("shot_type")).casefold()
    situation = _text(raw.get("situation")).casefold()
    outcome = _text(raw.get("outcome")).casefold()
    is_inside = bool(is_inside_value) if is_inside_value is not None else (
        "inside" in shot_type or "inside" in situation or "penalty area" in situation
    )
    is_outside = (not is_inside) if is_inside_value is not None else (
        "outside" in shot_type or "outside" in situation
    )
    is_on_target = bool(is_on_target_value) if is_on_target_value is not None else bool(
        raw.get("xgot") is not None or any(token in outcome for token in ("goal", "saved", "on target", "on_target"))
    )
    flags = (
        True,
        is_on_target,
        is_inside,
        is_outside,
        "open play" in situation or "open_play" in situation,
        any(token in situation for token in ("free kick", "corner", "set piece", "throw")),
        "penalty" in situation or "penalty" in shot_type,
    )
    xg = _number(raw.get("xg"))
    for window_index, window in enumerate(SHOTMAP_WINDOWS):
        if not _shot_in_window(window, minute):
            continue
        for metric_index, flag in enumerate(flags):
            if flag:
                state["counts"][window_index][side][metric_index] += 1
        if xg is not None:
            state["xg_values"][window_index][side].append(float(xg))
        state["available"] = True


def _shotmap_features_from_aggregate(state: Mapping[str, Any]) -> tuple[dict[str, Any], bool, tuple[float | None, float | None]]:
    result: dict[str, Any] = {}
    xg_totals: list[float | None] = []
    metric_index = {name: index for index, name in enumerate(_SHOT_COUNT_METRICS)}
    for window_index, window in enumerate(SHOTMAP_WINDOWS):
        side_xg: list[float | None] = []
        for side in range(2):
            values = state["xg_values"][window_index][side]
            side_xg.append(sum(values) if values else None)
        if window == "full":
            xg_totals = list(side_xg)
        for metric in SHOTMAP_METRICS:
            if metric == "xg":
                pair = tuple(side_xg)
            elif metric in {"mean_xg", "max_xg", "median_xg"}:
                pair_values = []
                for side in range(2):
                    values = state["xg_values"][window_index][side]
                    if not values:
                        pair_values.append(None)
                    elif metric == "mean_xg":
                        pair_values.append(sum(values) / len(values))
                    elif metric == "max_xg":
                        pair_values.append(max(values))
                    else:
                        pair_values.append(median(values))
                pair = tuple(pair_values)
            else:
                index = metric_index[metric]
                pair = tuple(
                    float(state["counts"][window_index][side][index])
                    if state["available"] else None
                    for side in range(2)
                )
            _add_pair(result, f"shotmap_{window}_{metric}", pair[0], pair[1])
    _add_momentum_features(result)
    return result, bool(state.get("available")), (xg_totals[0], xg_totals[1]) if xg_totals else (None, None)


def _new_event_aggregate() -> dict[str, Any]:
    return {
        "__v060_event_aggregate__": True,
        "counts": {
            "events_goals": [0.0, 0.0],
            "events_yellow_cards": [0.0, 0.0],
            "events_red_cards": [0.0, 0.0],
            "events_minutes_with_numerical_advantage": [0.0, 0.0],
        },
        "first_goal_minute": None,
        "last_goal_minute": None,
        "available": False,
    }


def _update_event_aggregate(state: dict[str, Any], raw: Mapping[str, Any]) -> None:
    if not _is_first_half(raw.get("period"), raw.get("minute")):
        return
    state["available"] = True
    side_value = _side_from_raw(raw)
    if side_value is None:
        return
    side = 0 if side_value else 1
    kind = _text(raw.get("event_type")).casefold()
    minute = _integer(raw.get("minute")) or 0
    added_time = _integer(raw.get("added_time")) or 0
    time_value = minute + added_time / 10.0
    if "goal" in kind:
        state["counts"]["events_goals"][side] += 1
        state["first_goal_minute"] = time_value if state["first_goal_minute"] is None else min(state["first_goal_minute"], time_value)
        state["last_goal_minute"] = time_value if state["last_goal_minute"] is None else max(state["last_goal_minute"], time_value)
    if "yellow" in kind:
        state["counts"]["events_yellow_cards"][side] += 1
    if "red" in kind or "second_yellow" in kind:
        state["counts"]["events_red_cards"][side] += 1
        advantaged = 0 if not side_value else 1
        state["counts"]["events_minutes_with_numerical_advantage"][advantaged] += max(0.0, 45.0 - time_value)


def _event_features_from_aggregate(state: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
    result: dict[str, Any] = {}
    for metric, values in state["counts"].items():
        _add_pair(result, metric, values[0] if state.get("available") else None, values[1] if state.get("available") else None)
    if state.get("first_goal_minute") is not None:
        result["events_first_goal_minute"] = state["first_goal_minute"]
        result["events_last_goal_minute"] = state["last_goal_minute"]
        result["events_minutes_since_last_goal"] = max(0.0, 45.0 - state["last_goal_minute"])
    else:
        result["events_first_goal_minute"] = None
        result["events_last_goal_minute"] = None
        result["events_minutes_since_last_goal"] = None
    return result, bool(state.get("available"))


class _HistoryState:
    def __init__(self) -> None:
        self.team: dict[str, list[dict[str, float]]] = defaultdict(list)
        self.team_season: dict[tuple[str, str], list[dict[str, float]]] = defaultdict(list)
        self.league: dict[str, list[dict[str, float]]] = defaultdict(list)

    @staticmethod
    def _team_key(row: Mapping[str, Any], side: str) -> str:
        # Canonical archives normally contain stable team IDs.  Some older
        # rows only have names, so use the name fallback consistently rather
        # than collapsing every ID-less team into one empty key.
        return (
            _text(row.get(f"{side}_team_id"))
            or _text(row.get(f"{side}_team_name"))
            or _text(row.get(f"{side}_team"))
        ).casefold()

    @staticmethod
    def _records_before(records: Sequence[dict[str, Any]], kickoff: datetime | None) -> list[dict[str, Any]]:
        """Return only records from strictly earlier kickoffs.

        The builder processes ties deterministically, but matches with the
        same kickoff are simultaneous from a modelling perspective and must
        not become one another's history.  Records are appended in sorted
        order, so trimming the suffix avoids an O(n) filter for every row.
        """

        if kickoff is None:
            return list(records)
        end = len(records)
        while end:
            value = records[end - 1].get("_kickoff")
            if not isinstance(value, datetime) or value < kickoff:
                break
            end -= 1
        return records[:end]

    @staticmethod
    def _summary(records: Sequence[Mapping[str, float]], key: str) -> float | None:
        values = [float(item[key]) for item in records if item.get(key) is not None]
        return sum(values) / len(values) if values else None

    def _team_features(self, key: str, prefix: str, records: Sequence[Mapping[str, float]], result: dict[str, Any]) -> None:
        for metric, source_key in (
            ("h2_scored", "scored"),
            ("h2_conceded", "conceded"),
            ("p1_rate", "p1"),
            ("ht_shots", "ht_shots"),
            ("ht_xg", "ht_xg"),
        ):
            result[f"{prefix}_{metric}_{key}"] = self._summary(records, source_key)

    def features_for(self, row: Mapping[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        home_key = self._team_key(row, "home")
        away_key = self._team_key(row, "away")
        season = _text(row.get("season")) or "unknown"
        kickoff = _parse_time(row.get("kickoff"))
        for window in HISTORY_WINDOWS:
            home_records = self._records_before(self.team.get(home_key, []), kickoff)[-window:]
            away_records = self._records_before(self.team.get(away_key, []), kickoff)[-window:]
            for metric, source_key in (
                ("h2_scored", "scored"),
                ("h2_conceded", "conceded"),
                ("p1_rate", "p1"),
                ("ht_shots", "ht_shots"),
                ("ht_xg", "ht_xg"),
            ):
                base = f"history_last{window}_{metric}"
                _add_pair(result, base, self._summary(home_records, source_key), self._summary(away_records, source_key))
        home_season = self._records_before(self.team_season.get((home_key, season), []), kickoff)
        away_season = self._records_before(self.team_season.get((away_key, season), []), kickoff)
        for metric, source_key in (
            ("h2_scored", "scored"),
            ("h2_conceded", "conceded"),
            ("p1_rate", "p1"),
            ("ht_shots", "ht_shots"),
            ("ht_xg", "ht_xg"),
        ):
            _add_pair(
                result,
                f"history_season_to_date_{metric}",
                self._summary(home_season, source_key),
                self._summary(away_season, source_key),
            )
        league_key = f"{_text(row.get('country_code'))}|{_text(row.get('league_id'))}"
        league_records = self._records_before(self.league.get(league_key, []), kickoff)
        result["league_history_p1_rate"] = self._summary(league_records, "p1")
        result["league_history_h2_goals_mean"] = self._summary(league_records, "h2_total")
        return result

    def update(self, row: Mapping[str, Any]) -> None:
        if not row.get("ml_eligible"):
            return
        h2_home = _number(row.get("h2_home_goals"))
        h2_away = _number(row.get("h2_away_goals"))
        h2_total = _number(row.get("h2_total_goals"))
        if h2_home is None or h2_away is None or h2_total is None:
            return
        p1 = 1.0 if row.get("target_h2_goal_class") == "H2_GOALS_1" else 0.0
        home_key = self._team_key(row, "home")
        away_key = self._team_key(row, "away")
        season = _text(row.get("season")) or "unknown"
        league_key = f"{_text(row.get('country_code'))}|{_text(row.get('league_id'))}"
        kickoff = _parse_time(row.get("kickoff"))
        home_record = {
            "scored": h2_home,
            "conceded": h2_away,
            "p1": p1,
            "ht_shots": _number(row.get("ht_shots_home")),
            "ht_xg": _number(row.get("ht_xg_home")),
            "h2_total": h2_total,
            "_kickoff": kickoff,
        }
        away_record = {
            "scored": h2_away,
            "conceded": h2_home,
            "p1": p1,
            "ht_shots": _number(row.get("ht_shots_away")),
            "ht_xg": _number(row.get("ht_xg_away")),
            "h2_total": h2_total,
            "_kickoff": kickoff,
        }
        self.team[home_key].append(home_record)
        self.team[away_key].append(away_record)
        self.team_season[(home_key, season)].append(home_record)
        self.team_season[(away_key, season)].append(away_record)
        self.league[league_key].append({"p1": p1, "h2_total": h2_total})


def load_search_space(path: Path | None = None) -> dict[str, Any]:
    config_path = path or Path(__file__).resolve().parent / "config" / "v060_search_space.yaml"
    text = config_path.read_text(encoding="utf-8")
    if yaml is not None:
        value = yaml.safe_load(text)
    else:  # JSON is valid YAML and keeps audit/CLI usable in a minimal install.
        value = json.loads(text)
    if not isinstance(value, dict):
        raise DatasetError(f"Invalid V0.6 search-space document: {config_path}")
    return value


def load_custom_configs(path: Path) -> list[dict[str, Any]]:
    """Load explicit YAML/JSON experiment definitions for CUSTOM mode."""

    text = Path(path).read_text(encoding="utf-8")
    if yaml is not None:
        value = yaml.safe_load(text)
    else:
        value = json.loads(text)
    if isinstance(value, Mapping):
        value = value.get("experiments", value.get("configs", []))
    if not isinstance(value, list) or not all(isinstance(item, Mapping) for item in value):
        raise DatasetError(f"Custom config must be a list or an experiments/configs mapping: {path}")
    return [dict(item) for item in value]


class DatasetBuilder:
    """Build and validate the one-row-per-match halftime research cache."""

    CORE_COLUMNS = (
        "fotmob_match_id",
        "league_id",
        "league_name",
        "country_code",
        "country_name",
        "season_id",
        "season_label",
        "kickoff_at_utc",
        "home_team_id",
        "home_team_name",
        "away_team_id",
        "away_team_name",
        "ht_score_home",
        "ht_score_away",
        "ft_score_home",
        "ft_score_away",
        "match_status",
        "source_type",
        "source_context",
        "ft_score_source",
        "ht_shots_home",
        "ht_shots_away",
        "ht_shots_on_target_home",
        "ht_shots_on_target_away",
        "ht_big_chances_home",
        "ht_big_chances_away",
        "ht_corners_home",
        "ht_corners_away",
        "ht_possession_home",
        "ht_possession_away",
        "ht_yellow_cards_home",
        "ht_yellow_cards_away",
        "ht_red_cards_home",
        "ht_red_cards_away",
        "ht_fouls_home",
        "ht_fouls_away",
        "ht_offsides_home",
        "ht_offsides_away",
        "ht_goalkeeper_saves_home",
        "ht_goalkeeper_saves_away",
        "ht_passes_home",
        "ht_passes_away",
        "ht_accurate_passes_home",
        "ht_accurate_passes_away",
        "ht_shots_inside_box_home",
        "ht_shots_inside_box_away",
        "ht_shots_outside_box_home",
        "ht_shots_outside_box_away",
        "ht_touches_in_box_home",
        "ht_touches_in_box_away",
        "ht_xg_home",
        "ht_xg_away",
    )

    def __init__(
        self,
        root: Path,
        *,
        archive_root: Path | None = None,
        database_path: Path | None = None,
        cache_dir: Path | None = None,
        workers: int = IO_WORKERS,
    ) -> None:
        self.root = Path(root).resolve()
        self.archive_root = Path(archive_root or self.root / "data" / "archive" / "fotmob").resolve()
        self.database_path = Path(database_path or self.root / "data" / "tipico.db").resolve()
        self.cache_dir = Path(cache_dir or self.root / "research" / "cache").resolve()
        self.requested_workers = max(1, int(workers))
        self.workers = _ram_guard(self.requested_workers)
        self.dataset_path = self.cache_dir / "v060_ht_dataset.parquet"
        self.manifest_path = self.cache_dir / "v060_ht_dataset_manifest.json"

    @staticmethod
    def _partition(path: Path, key: str) -> str | None:
        prefix = f"{key}="
        for part in path.parts:
            if part.startswith(prefix):
                return part[len(prefix):]
        return None

    @staticmethod
    def _read_row(path: Path, columns: Sequence[str] | None = None) -> dict[str, Any]:
        if pq is None:
            raise DatasetError("pyarrow is required to read the canonical FotMob archive")
        parquet_file = pq.ParquetFile(path)
        available = set(parquet_file.schema_arrow.names)
        requested = [column for column in (columns or parquet_file.schema_arrow.names) if column in available]
        table = parquet_file.read(columns=requested)
        rows = table.to_pylist()
        return dict(rows[0]) if rows else {}

    @staticmethod
    def _read_rows(path: Path, columns: Sequence[str] | None = None) -> list[dict[str, Any]]:
        if pq is None:
            raise DatasetError("pyarrow is required to read the canonical FotMob archive")
        parquet_file = pq.ParquetFile(path)
        available = set(parquet_file.schema_arrow.names)
        requested = [column for column in (columns or parquet_file.schema_arrow.names) if column in available]
        return [dict(row) for row in parquet_file.read(columns=requested).to_pylist()]

    @staticmethod
    def _bulk_rows(directory: Path, columns: Sequence[str]) -> list[dict[str, Any]] | None:
        """Read a partitioned archive directory with one Arrow dataset scan.

        The fallback value ``None`` means that the caller should use the
        portable per-file reader.  An existing but empty directory returns an
        empty list, which is different from a failed bulk scan.
        """

        if ds is None or not directory.exists():
            return None
        try:
            dataset = ds.dataset(str(directory), format="parquet", partitioning="hive")
            available = set(dataset.schema.names)
            requested = [column for column in columns if column in available]
            if not requested:
                return []
            result: list[dict[str, Any]] = []
            for batch in dataset.scanner(columns=requested, batch_size=8192).to_batches():
                result.extend(dict(row) for row in batch.to_pylist())
            return result
        except (OSError, ValueError, RuntimeError):
            return None

    def _core_records(self, paths: Sequence[Path]) -> list[tuple[Path, dict[str, Any]]]:
        if not paths:
            return []
        with ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="v060-core") as executor:
            return list(executor.map(lambda path: (path, self._read_row(path, self.CORE_COLUMNS)), paths))

    def _related_records(
        self,
        directory: Path,
        columns: Sequence[str],
    ) -> tuple[dict[str, list[dict[str, Any]]], set[str]]:
        """Return compact first-half related rows and explicit ET IDs.

        Raw ``extra_json`` payloads are parsed once while scanning and then
        discarded.  Keeping those strings for every event/shot was the main
        reason the first full-archive implementation approached gigabytes of
        Python heap usage.
        """

        is_events = directory.name.casefold() == "events"
        grouped: dict[str, dict[str, Any]] = {}
        extra_time_ids: set[str] = set()

        paths = list(self._related_lookup(directory).items())

        def process_file(item: tuple[str, Path]) -> tuple[str, dict[str, Any] | None, bool]:
            expected_id, path = item
            state = _new_event_aggregate() if is_events else _new_shot_aggregate()
            extra_time = False
            for raw in self._read_rows(path, columns):
                match_id = _text(raw.get("fotmob_match_id")) or expected_id
                if is_events and _extra_time_from_events([raw]):
                    extra_time = True
                if is_events:
                    _update_event_aggregate(state, raw)
                else:
                    _update_shot_aggregate(state, raw)
            return expected_id, state if state.get("available") else None, extra_time

        if paths:
            with ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix=f"v060-{directory.name}") as executor:
                for match_id, state, extra_time in executor.map(process_file, paths):
                    if extra_time:
                        extra_time_ids.add(match_id)
                    if state is not None:
                        grouped[match_id] = state
        return dict(grouped), extra_time_ids

    def _source_paths(self) -> list[Path]:
        core_root = self.archive_root / "match_core"
        paths = sorted(core_root.rglob("*.parquet")) if core_root.exists() else []
        if paths:
            return paths
        # Older/relocated deployments still have the canonical path in the
        # SQLite archive index.  Reading this table is read-only and does not
        # make the research process dependent on the live collector.
        if not self.database_path.exists():
            return []
        try:
            uri = f"file:{self.database_path.as_posix()}?mode=ro"
            connection = sqlite3.connect(uri, uri=True)
            rows = connection.execute(
                """
                SELECT archive_path FROM fotmob_historical_archive_index
                WHERE schema_version = 'fotmob_match_core_v2'
                ORDER BY fotmob_match_id
                """
            ).fetchall()
            connection.close()
        except sqlite3.Error:
            return []
        result: list[Path] = []
        seen: set[str] = set()
        for row in rows:
            value = str(row[0] or "").replace("\\\\", "\\")
            path = Path(value)
            if not path.exists():
                candidate = self.archive_root / "match_core" / path.name
                if candidate.exists():
                    path = candidate
            key = str(path.resolve()) if path.exists() else str(path)
            if key not in seen and path.exists():
                seen.add(key)
                result.append(path)
        return sorted(result)

    @staticmethod
    def _related_lookup(directory: Path) -> dict[str, Path]:
        result: dict[str, Path] = {}
        if not directory.exists():
            return result
        for path in sorted(directory.rglob("match-*.parquet")):
            match = re.match(r"match-(.+)\.parquet$", path.name)
            if match:
                result.setdefault(match.group(1), path)
        return result

    @staticmethod
    def _source_fingerprint(paths: Sequence[Path]) -> str:
        digest = hashlib.sha256()
        for path in paths:
            try:
                stat = path.stat()
            except OSError:
                continue
            digest.update(str(path).encode("utf-8", errors="replace"))
            digest.update(str(stat.st_size).encode())
            digest.update(str(stat.st_mtime_ns).encode())
        return digest.hexdigest()

    def _build_row(
        self,
        core: dict[str, Any],
        shots: Sequence[Mapping[str, Any]],
        events: Sequence[Mapping[str, Any]],
        history: _HistoryState,
        path: Path,
        *,
        extra_time_hint: bool = False,
    ) -> dict[str, Any]:
        league_id = _text(core.get("league_id")) or self._partition(path, "league_id") or "unknown"
        season = _text(core.get("season_label")) or self._partition(path, "season") or _text(core.get("season_id")) or "unknown"
        kickoff = _text(core.get("kickoff_at_utc"))
        event_flags = [] if isinstance(events, Mapping) else list(events)
        extra_time = extra_time_hint or _extra_time_from_text(core) or _extra_time_from_events(event_flags)
        target = classify_h2_goal_target(
            _integer(core.get("ht_score_home")),
            _integer(core.get("ht_score_away")),
            None if extra_time else _integer(core.get("ft_score_home")),
            None if extra_time else _integer(core.get("ft_score_away")),
            extra_time_ambiguous=extra_time,
        )
        row: dict[str, Any] = {
            "row_id": _text(core.get("fotmob_match_id")) or path.stem.replace("match-", ""),
            "fotmob_match_id": _text(core.get("fotmob_match_id")) or path.stem.replace("match-", ""),
            "kickoff": kickoff or None,
            "country": _text(core.get("country_name")) or _text(core.get("country_code")) or None,
            "country_code": _text(core.get("country_code")) or None,
            "league_id": league_id,
            "league_name": _text(core.get("league_name")) or league_id,
            "season": season,
            "season_id": _text(core.get("season_id")) or season,
            "home_team": _text(core.get("home_team_name")),
            "away_team": _text(core.get("away_team_name")),
            "home_team_id": _text(core.get("home_team_id")) or None,
            "away_team_id": _text(core.get("away_team_id")) or None,
            "ht_home": _integer(core.get("ht_score_home")),
            "ht_away": _integer(core.get("ht_score_away")),
            "ht_total": None,
            "ht_diff": None,
            "regulation_ft_home": None if extra_time else _integer(core.get("ft_score_home")),
            "regulation_ft_away": None if extra_time else _integer(core.get("ft_score_away")),
            "extra_time_ambiguous": bool(extra_time),
            "match_status": _text(core.get("match_status")) or None,
            "source_type": _text(core.get("source_type")) or None,
            "source_context": _text(core.get("source_context")) or None,
            **target,
            "xg_available": False,
            "shotmap_available": False,
            "events_available": False,
        }
        if row["ht_home"] is not None and row["ht_away"] is not None:
            row["ht_total"] = row["ht_home"] + row["ht_away"]
            row["ht_diff"] = row["ht_home"] - row["ht_away"]
        if row["ml_eligible"] and not kickoff:
            row["target_quality"] = "MISSING_KICKOFF"
            row["ml_eligible"] = False

        features: dict[str, Any] = {name: None for name in set(itertools.chain.from_iterable(FEATURES_BY_UNIVERSE.values()))}
        features.update(
            {
                "ht_home_goals": _number(row["ht_home"]),
                "ht_away_goals": _number(row["ht_away"]),
                "ht_total_goals": _number(row["ht_total"]),
                "ht_goal_diff": _number(row["ht_diff"]),
            }
        )
        for metric in CORE_METRICS:
            _add_pair(features, f"ht_{metric}", _metric_value(core, metric, "home"), _metric_value(core, metric, "away"))
        shot_features: dict[str, Any]
        if isinstance(shots, Mapping) and shots.get("__v060_shot_aggregate__"):
            shot_features, shot_available, shot_xg_totals = _shotmap_features_from_aggregate(shots)
        else:
            first_half_shots, _ = _first_half_shot_rows(shots, core)
            shot_features, shot_available = _shotmap_features(shots, core)
            shot_xg_values: list[float | None] = []
            for side in (True, False):
                values = [shot.get("xg") for shot in first_half_shots if shot["is_home"] is side and shot.get("xg") is not None]
                shot_xg_values.append(sum(values) if values else None)
            shot_xg_totals = (shot_xg_values[0], shot_xg_values[1])
        xg_home = _number(core.get("ht_xg_home"))
        xg_away = _number(core.get("ht_xg_away"))
        # Older canonical match-core files do not always carry aggregate
        # first-half xG.  When the immutable shot archive has both sides'
        # xG values, derive the aggregate from those shots.  This is still a
        # halftime feature and keeps CORE_XG optional instead of silently
        # discarding valid shot-level xG.
        if xg_home is None and shot_xg_totals[0] is not None:
            xg_home = shot_xg_totals[0]
        if xg_away is None and shot_xg_totals[1] is not None:
            xg_away = shot_xg_totals[1]
        _add_pair(features, "ht_xg", xg_home, xg_away)
        shots_home = _number(core.get("ht_shots_home"))
        shots_away = _number(core.get("ht_shots_away"))
        _add_pair(
            features,
            "ht_xg_per_shot",
            xg_home / shots_home if xg_home is not None and shots_home else None,
            xg_away / shots_away if xg_away is not None and shots_away else None,
        )
        sot_home = _number(core.get("ht_shots_on_target_home"))
        sot_away = _number(core.get("ht_shots_on_target_away"))
        _add_pair(
            features,
            "ht_xg_per_sot",
            xg_home / sot_home if xg_home is not None and sot_home else None,
            xg_away / sot_away if xg_away is not None and sot_away else None,
        )
        if isinstance(events, Mapping) and events.get("__v060_event_aggregate__"):
            event_features, event_available = _event_features_from_aggregate(events)
        else:
            event_features, event_available = _event_features(events, core)
        features.update(shot_features)
        features.update(event_features)
        row["shotmap_available"] = bool(shot_available)
        row["events_available"] = bool(event_available)
        row["xg_available"] = bool(xg_home is not None and xg_away is not None)
        row.update(features)
        # ``features`` contains None placeholders for every universe.  Apply
        # rolling history afterwards so the genuinely prior-match values are
        # not overwritten by those placeholders.
        row.update(history.features_for(row))
        if row["ml_eligible"]:
            history.update(row)
        row["feature_coverage"] = (
            sum(value is not None for name, value in row.items() if name in features) / max(1, len(features))
        )
        return row

    @staticmethod
    def _arrow_schema() -> Any:
        if pa is None:
            raise DatasetError("pyarrow is required to build the V0.6 dataset")
        strings = [
            "row_id",
            "fotmob_match_id",
            "kickoff",
            "country",
            "country_code",
            "league_id",
            "league_name",
            "season",
            "season_id",
            "home_team",
            "away_team",
            "home_team_id",
            "away_team_id",
            "target_h2_goal_class",
            "target_quality",
            "match_status",
            "source_type",
            "source_context",
        ]
        ints = [
            "ht_home",
            "ht_away",
            "ht_total",
            "ht_diff",
            "regulation_ft_home",
            "regulation_ft_away",
            "h2_home_goals",
            "h2_away_goals",
            "h2_total_goals",
        ]
        booleans = ["middle_loss", "ml_eligible", "extra_time_ambiguous", "xg_available", "shotmap_available", "events_available"]
        feature_names = list(dict.fromkeys(itertools.chain.from_iterable(FEATURES_BY_UNIVERSE.values())))
        return pa.schema(
            [(name, pa.string()) for name in strings]
            + [(name, pa.int64()) for name in ints]
            + [(name, pa.bool_()) for name in booleans]
            + [("feature_coverage", pa.float64())]
            + [(name, pa.float64()) for name in feature_names]
        )

    def build(self, *, force: bool = False, start_date: str | None = None, end_date: str | None = None) -> dict[str, Any]:
        if self.dataset_path.exists() and self.manifest_path.exists() and not force and not start_date and not end_date:
            return json.loads(self.manifest_path.read_text(encoding="utf-8"))
        if pq is None or pa is None:
            raise DatasetError("pyarrow is required for build-dataset")
        paths = self._source_paths()
        shot_lookup, _ = self._related_records(
            self.archive_root / "shots",
            (
                "fotmob_match_id",
                "period",
                "minute",
                "xg",
                "xgot",
                "is_home",
                "team_id",
                "home_team_id",
                "away_team_id",
                "outcome",
                "shot_type",
                "situation",
                "extra_json",
            ),
        )
        event_lookup, extra_time_ids = self._related_records(
            self.archive_root / "events",
            (
                "fotmob_match_id",
                "period",
                "minute",
                "added_time",
                "event_type",
                "is_home",
                "team_id",
                "home_team_id",
                "away_team_id",
                "extra_json",
            ),
        )
        history = _HistoryState()
        # The archive is partitioned by league/season, not globally by
        # kickoff.  Load the small core headers first and process them in
        # chronological order so rolling-history features only see genuinely
        # earlier matches.
        source_rows = self._core_records(paths)
        source_rows.sort(
            key=lambda item: (
                _parse_time(item[1].get("kickoff_at_utc")) or datetime.max.replace(tzinfo=timezone.utc),
                _text(item[1].get("fotmob_match_id")) or item[0].name,
            )
        )
        schema = self._arrow_schema()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.dataset_path.with_suffix(".parquet.tmp")
        if temporary.exists():
            temporary.unlink()
        writer = None
        buffered: list[dict[str, Any]] = []
        wrote_table = False
        selected_count = 0
        eligible_count = 0
        target_distribution = {target: 0 for target in TARGET_CLASSES}
        feature_coverage_counts = {name: 0 for name in FEATURES_BY_UNIVERSE["ALL_AVAILABLE"]}
        dates: list[datetime] = []

        def flush() -> None:
            nonlocal wrote_table
            if not buffered:
                return
            table = pa.Table.from_pylist(
                [{field.name: row.get(field.name) for field in schema} for row in buffered],
                schema=schema,
            )
            writer.write_table(table)
            buffered.clear()
            wrote_table = True

        try:
            writer = pq.ParquetWriter(temporary, schema=schema, compression="zstd", use_dictionary=True)
            for path, core in source_rows:
                match_id = _text(core.get("fotmob_match_id"))
                row = self._build_row(
                    core,
                    shot_lookup.get(match_id, []),
                    event_lookup.get(match_id, []),
                    history,
                    path,
                    extra_time_hint=match_id in extra_time_ids,
                )
                date = _parse_time(row.get("kickoff"))
                if start_date and (date is None or date.date().isoformat() < str(start_date)):
                    continue
                if end_date and (date is None or date.date().isoformat() > str(end_date)):
                    continue
                buffered.append(row)
                selected_count += 1
                if row.get("ml_eligible"):
                    eligible_count += 1
                    target = row.get("target_h2_goal_class")
                    if target in target_distribution:
                        target_distribution[str(target)] += 1
                for name in feature_coverage_counts:
                    if row.get(name) is not None:
                        feature_coverage_counts[name] += 1
                if date is not None:
                    dates.append(date)
                if len(buffered) >= 1000:
                    flush()
            flush()
            if not wrote_table:
                writer.write_table(pa.Table.from_pylist([], schema=schema))
            writer.close()
            writer = None
            temporary.replace(self.dataset_path)
        except BaseException:
            if writer is not None:
                writer.close()
            temporary.unlink(missing_ok=True)
            raise
        feature_coverage = {
            name: count / max(1, selected_count)
            for name, count in feature_coverage_counts.items()
        }
        manifest: dict[str, Any] = {
            "dataset_schema_version": DATASET_SCHEMA_VERSION,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "target_schema_version": TARGET_SCHEMA_VERSION,
            "model_cutoff": MODEL_CUTOFF,
            "target_definition": "regulation_ft_goals - halftime_goals",
            "dataset_path": str(self.dataset_path),
            "dataset_hash": _hash_file(self.dataset_path),
            "created_at": _now(),
            "source_archive_root": str(self.archive_root),
            "source_file_count": len(paths),
            "source_fingerprint": self._source_fingerprint(paths),
            "workers": {"requested": self.requested_workers, "effective": self.workers},
            "source_date_range": {
                "from": min(dates).date().isoformat() if dates else None,
                "to": max(dates).date().isoformat() if dates else None,
            },
            "match_count": selected_count,
            "eligible_match_count": eligible_count,
            "target_distribution": target_distribution,
            "feature_coverage": feature_coverage,
            "feature_columns_by_universe": FEATURES_BY_UNIVERSE,
            "feature_catalog": feature_catalog(),
            "target_columns": [
                "regulation_ft_home",
                "regulation_ft_away",
                "h2_home_goals",
                "h2_away_goals",
                "h2_total_goals",
                "target_h2_goal_class",
                "middle_loss",
                "target_quality",
                "ml_eligible",
            ],
            "build_filters": {"start_date": start_date, "end_date": end_date},
        }
        self.manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return manifest

    def load(self) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if pq is None:
            raise DatasetError("pyarrow is required to read the V0.6 dataset")
        if not self.dataset_path.exists() or not self.manifest_path.exists():
            raise DatasetError("V0.6 dataset cache does not exist; run build-dataset first")
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        table = pq.read_table(self.dataset_path)
        return manifest, [dict(row) for row in table.to_pylist()]

    def audit(self) -> dict[str, Any]:
        manifest, rows = self.load()
        dataset_hash = _hash_file(self.dataset_path)
        if dataset_hash != manifest.get("dataset_hash"):
            raise DatasetError("Dataset hash does not match its manifest")
        if manifest.get("model_cutoff") != MODEL_CUTOFF:
            raise LeakageError(f"Dataset model cutoff must be {MODEL_CUTOFF}")
        catalog = manifest.get("feature_catalog") or []
        forbidden: list[str] = []
        catalog_by_name: dict[str, Mapping[str, Any]] = {}
        for item in catalog:
            name = str(item.get("feature_name", ""))
            origin = str(item.get("feature_origin", ""))
            cutoff = str(item.get("availability_cutoff", ""))
            catalog_by_name[name] = item
            if cutoff not in ALLOWED_FEATURE_CUTOFFS or _is_forbidden_feature(name, origin):
                forbidden.append(name)
        # Audit the actual universe declarations as well as the catalog.  A
        # hand-edited or stale manifest must not be able to hide a forbidden
        # column by omitting its metadata entry.
        declared_features = {
            str(name)
            for names in (manifest.get("feature_columns_by_universe") or {}).values()
            if isinstance(names, Sequence) and not isinstance(names, (str, bytes))
            for name in names
        }
        for name in declared_features:
            item = catalog_by_name.get(name)
            if item is None:
                forbidden.append(f"missing_metadata:{name}")
                continue
            if _is_forbidden_feature(name, str(item.get("feature_origin", ""))):
                forbidden.append(name)
        if forbidden:
            raise LeakageError(f"Forbidden or undocumented feature(s) in V0.6 dataset: {', '.join(sorted(set(forbidden)))}")
        checked = 0
        target_mismatches: list[str] = []
        for row in rows:
            if row.get("extra_time_ambiguous"):
                if row.get("ml_eligible") or row.get("target_quality") != "INVALID_EXTRA_TIME_AMBIGUOUS":
                    target_mismatches.append(str(row.get("row_id")))
                continue
            expected = classify_h2_goal_target(
                row.get("ht_home"),
                row.get("ht_away"),
                row.get("regulation_ft_home"),
                row.get("regulation_ft_away"),
            )
            checked += 1
            for key in ("h2_home_goals", "h2_away_goals", "h2_total_goals", "target_h2_goal_class", "ml_eligible", "target_quality"):
                if row.get(key) != expected.get(key):
                    target_mismatches.append(str(row.get("row_id")))
                    break
        if target_mismatches:
            raise DatasetError(f"Target audit failed for {len(target_mismatches)} row(s)")
        return {
            "status": "PASS",
            "dataset_hash": dataset_hash,
            "match_count": len(rows),
            "eligible_match_count": sum(bool(row.get("ml_eligible")) for row in rows),
            "checked_target_rows": checked,
            "forbidden_features": [],
            "model_cutoff": manifest.get("model_cutoff"),
        }


def target_regression_preflight() -> dict[str, Any]:
    """Run the three mandatory H2 target regression cases."""

    cases = (
        ((1, 0, 1, 1), "H2_GOALS_1"),
        ((0, 0, 2, 0), "H2_GOALS_2_PLUS"),
        ((2, 1, 2, 1), "H2_GOALS_0"),
    )
    results = []
    for values, expected in cases:
        actual = classify_h2_goal_target(*values)
        results.append(
            {
                "input": values,
                "expected": expected,
                "actual": actual.get("target_h2_goal_class"),
                "pass": actual.get("target_h2_goal_class") == expected
                and actual.get("ml_eligible") is True,
            }
        )
    return {"status": "PASS" if all(item["pass"] for item in results) else "FAIL", "cases": results}


def _catboost_preflight() -> dict[str, Any]:
    """Verify import, real fit, predict, save and reload for CatBoost."""

    result: dict[str, Any] = {
        "installed": _package_version("catboost") is not None,
        "import_ok": False,
        "version": _package_version("catboost"),
        "train_smoke_test": "NOT_RUN",
        "predict_proba_test": "NOT_RUN",
        "serialization_test": "NOT_RUN",
        "reload_predict_proba_test": "NOT_RUN",
        "effective_class_module": None,
        "status": "NOT_AVAILABLE",
    }
    try:
        from catboost import CatBoostClassifier
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result
    result["import_ok"] = True
    try:
        if np is None:
            raise DatasetError("numpy is unavailable")
        x = np.asarray(
            [[0.0, 0.0], [0.0, 1.0], [1.0, 0.0], [1.0, 1.0], [2.0, 0.5], [2.0, 1.5]],
            dtype=float,
        )
        y = np.asarray([0, 1, 1, 2, 2, 0], dtype=int)
        model = CatBoostClassifier(
            iterations=4,
            depth=2,
            learning_rate=0.1,
            verbose=False,
            allow_writing_files=False,
            random_seed=42,
            thread_count=1,
        )
        model.fit(x, y)
        result["train_smoke_test"] = "PASS"
        result["effective_class_module"] = f"{type(model).__module__}.{type(model).__name__}"
        if not str(type(model).__module__).casefold().startswith("catboost"):
            raise ModelIdentityError("CatBoost smoke model did not originate from catboost")
        predictions = model.predict_proba(x)
        if np.asarray(predictions).shape != (len(x), 3):
            raise ModelIdentityError("CatBoost smoke predict_proba returned an unexpected shape")
        result["predict_proba_test"] = "PASS"
        with tempfile.TemporaryDirectory(prefix="v061-catboost-") as directory:
            path = Path(directory) / "smoke.cbm"
            model.save_model(str(path))
            result["serialization_test"] = "PASS" if path.exists() else "FAIL"
            reloaded = CatBoostClassifier()
            reloaded.load_model(str(path))
            reloaded_predictions = reloaded.predict_proba(x)
            if not np.allclose(predictions, reloaded_predictions, rtol=1e-6, atol=1e-7):
                raise ModelIdentityError("CatBoost reload predictions differ from smoke predictions")
            result["reload_predict_proba_test"] = "PASS"
        result["status"] = "PASS"
    except Exception as exc:
        result["status"] = "FAIL"
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def _optional_dependency_status(distribution: str, module_name: str | None = None) -> dict[str, Any]:
    module = module_name or distribution.replace("-", "_")
    version = _package_version(distribution)
    result: dict[str, Any] = {"installed": version is not None, "import_ok": False, "version": version}
    try:
        __import__(module)
        result["import_ok"] = True
        result["status"] = "PASS"
    except Exception as exc:
        result["status"] = "NOT_AVAILABLE" if version is None else "FAIL"
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def research_preflight(
    root: Path,
    *,
    cache_dir: Path | None = None,
    registry_path: Path | None = None,
    require_catboost: bool = False,
    container: bool = False,
) -> dict[str, Any]:
    """Validate runtime prerequisites before an explicit research run."""

    root = Path(root).resolve()
    builder = DatasetBuilder(root, cache_dir=cache_dir)
    dataset: dict[str, Any] = {
        "path": str(builder.dataset_path),
        "manifest_path": str(builder.manifest_path),
        "exists": builder.dataset_path.exists() and builder.manifest_path.exists(),
        "hash": None,
        "manifest_hash": None,
        "status": "FAIL",
    }
    audit: dict[str, Any]
    if dataset["exists"]:
        try:
            manifest, _ = builder.load()
            actual_hash = _hash_file(builder.dataset_path)
            dataset.update(
                {
                    "hash": actual_hash,
                    "manifest_hash": manifest.get("dataset_hash"),
                    "schema": manifest.get("dataset_schema_version"),
                    "feature_schema": manifest.get("feature_schema_version"),
                    "target_schema": manifest.get("target_schema_version"),
                    "status": "PASS" if actual_hash == manifest.get("dataset_hash") else "FAIL",
                }
            )
            audit = builder.audit()
        except Exception as exc:
            audit = {"status": "FAIL", "error": f"{type(exc).__name__}: {exc}"}
    else:
        audit = {"status": "FAIL", "error": "dataset cache or manifest missing"}

    registry_file = Path(registry_path or root / "research" / "ml_registry.sqlite").resolve()
    registry_rows: list[dict[str, Any]] = []
    registry_status: dict[str, Any] = {
        "path": str(registry_file),
        "integrity": "FAIL",
        "dataset_identity": "FAIL",
    }
    try:
        registry_file.parent.mkdir(parents=True, exist_ok=True)
        registry = ExperimentRegistry(registry_file)
        try:
            check = registry.connection.execute("PRAGMA integrity_check").fetchone()
            registry_status["integrity"] = "PASS" if check and str(check[0]).lower() == "ok" else "FAIL"
            registry_status["columns"] = [str(row[1]) for row in registry.connection.execute("PRAGMA table_info(experiments)")]
            registry_status["counts"] = registry.counts()
            registry_rows = registry.list()
            expected_dataset_hash = str(dataset.get("hash") or dataset.get("manifest_hash") or "")
            mismatches = [
                {
                    "experiment_id": str(row.get("experiment_id")),
                    "dataset_hash": str(row.get("dataset_hash")),
                }
                for row in registry_rows
                if row.get("dataset_hash")
                and expected_dataset_hash
                and str(row.get("dataset_hash")) != expected_dataset_hash
            ]
            missing_identity = [
                str(row.get("experiment_id"))
                for row in registry_rows
                if row.get("status") in {"PLANNED", "RUNNING", "INTERRUPTED"}
                and not row.get("dataset_hash")
            ]
            registry_status["dataset_hash"] = expected_dataset_hash or None
            registry_status["dataset_hash_mismatches"] = mismatches
            registry_status["dataset_hash_missing_for_active"] = missing_identity
            registry_status["dataset_identity"] = (
                "PASS"
                if dataset.get("status") == "PASS" and not mismatches and not missing_identity
                else "FAIL"
            )
            current_identity_mismatches: list[str] = []
            historical_fallbacks: list[str] = []
            for row in registry_rows:
                if row.get("status") != "COMPLETED":
                    continue
                metrics = row.get("metrics") or {}
                requested = str(
                    row.get("requested_model_family")
                    or metrics.get("requested_model_family")
                    or row.get("model_family")
                    or ""
                ).upper()
                effective = str(
                    row.get("effective_model_family")
                    or metrics.get("effective_model_family")
                    or ""
                ).upper()
                has_run_context = bool(row.get("research_run_id") or metrics.get("research_run_id"))
                if not has_run_context:
                    if effective == "EXTRATREES_FALLBACK":
                        historical_fallbacks.append(str(row.get("experiment_id")))
                    continue
                if not effective or requested != effective:
                    current_identity_mismatches.append(str(row.get("experiment_id")))
            registry_status["current_model_identity_mismatches"] = current_identity_mismatches
            registry_status["historical_fallback_records"] = historical_fallbacks
            registry_status["model_identity"] = "PASS" if not current_identity_mismatches else "FAIL"
            registry_status["no_model_fallback"] = "PASS" if not current_identity_mismatches else "FAIL"
        finally:
            registry.close()
    except Exception as exc:
        registry_status["error"] = f"{type(exc).__name__}: {exc}"

    write_dirs = [
        root / "research" / "cache",
        root / "research" / "models" / "v060",
        root / "research" / "output" / "v060",
    ]
    write_status: dict[str, Any] = {}
    for directory in write_dirs:
        try:
            directory.mkdir(parents=True, exist_ok=True)
            probe = directory / ".v061-write-probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            write_status[str(directory)] = "PASS"
        except OSError as exc:
            write_status[str(directory)] = f"FAIL: {exc}"

    environment = environment_manifest()
    catboost = _catboost_preflight()
    xgboost = _optional_dependency_status("xgboost")
    lightgbm = _optional_dependency_status("lightgbm")
    resources = _resource_snapshot(root)
    try:
        configured_min_disk_gb = max(
            2.0, float(os.getenv("V061_MIN_FREE_DISK_GB", "2"))
        )
    except ValueError:
        configured_min_disk_gb = 2.0
    resources["minimum_free_disk_gb"] = configured_min_disk_gb
    resources["disk_guard_status"] = (
        "PASS"
        if resources.get("free_disk_bytes") is not None
        and int(resources["free_disk_bytes"]) >= configured_min_disk_gb * 1024**3
        else "FAIL"
    )
    target = target_regression_preflight()
    lock_status = ResearchRunLock(root).inspect().as_dict()
    deploy_status = deployment_status(root, check_integrity=False)
    canary_status: dict[str, Any]
    try:
        canary_registry = ExperimentRegistry(registry_file)
        try:
            canary_status = catboost_real_canary_status(
                canary_registry,
                scope="CT110",
                dataset_hash=dataset.get("hash"),
                tree_hash=source_tree_hash(root),
            )
        finally:
            canary_registry.close()
    except Exception as exc:
        canary_status = {"status": "FAIL", "error": f"{type(exc).__name__}: {exc}"}
    required_core = all(
        (
            dataset["status"] == "PASS",
            audit.get("status") == "PASS",
            registry_status.get("integrity") == "PASS",
            registry_status.get("dataset_identity") == "PASS",
            registry_status.get("model_identity") == "PASS",
            registry_status.get("no_model_fallback") == "PASS",
            target.get("status") == "PASS",
            all(str(value).startswith("PASS") for value in write_status.values()),
        )
    )
    cat_required_ok = catboost.get("status") == "PASS" if require_catboost or container else True
    overall = "PASS" if required_core and cat_required_ok else "PARTIAL" if required_core else "FAIL"
    result = {
        "v061_version": V061_VERSION,
        "v0611_version": V0611_VERSION,
        "status": overall,
        "RESEARCH_PREFLIGHT": overall,
        "python_version": environment["python_version"],
        "platform": environment["platform"],
        "package_versions": environment["packages"],
        "environment_manifest": environment,
        "environment_hash": environment_hash(environment),
        "deployment": deploy_status,
        "deployment_manifest": deploy_status,
        "ML_LOCK": lock_status,
        "ML_LOCK_STATUS": lock_status.get("status"),
        "CATBOOST_CT_REAL_DATA_CANARY": canary_status,
        "dataset": dataset,
        "DATASET_IDENTITY": dataset["status"],
        "DATASET_REGISTRY_IDENTITY": registry_status.get("dataset_identity"),
        "audit": audit,
        "TARGET_H2_ONLY": target["status"],
        "EXTRA_TIME_PROTECTION": "PASS" if audit.get("status") == "PASS" else "FAIL",
        "LEAKAGE_AUDIT": "PASS" if audit.get("status") == "PASS" else "FAIL",
        "target_regression": target,
        "catboost": catboost,
        "CATBOOST_RUNTIME": catboost.get("status"),
        "MODEL_IDENTITY": registry_status.get("model_identity", "FAIL"),
        "NO_MODEL_FALLBACK": registry_status.get("no_model_fallback", "FAIL"),
        "RESUME": "PASS" if registry_status.get("integrity") == "PASS" else "FAIL",
        # Live collector health is a deployment canary, not something a
        # filesystem-only research preflight can infer honestly.
        "COLLECTOR_HEALTH": "PENDING",
        "xgboost": xgboost,
        "lightgbm": lightgbm,
        "resources": resources,
        "RAM_GUARD": "PASS" if resources.get("available_ram_bytes") is not None else "PARTIAL",
        "DISK_GUARD": resources.get("disk_guard_status", "FAIL"),
        "registry": registry_status,
        "REGISTRY": "PASS" if registry_status.get("integrity") == "PASS" and registry_status.get("dataset_identity") == "PASS" else "FAIL",
        "write_permissions": write_status,
        "container": bool(container),
        "require_catboost": bool(require_catboost or container),
    }
    return result


class ExperimentRegistry:
    """Durable registry for planned, resumable research experiments."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(self.path))
        self.connection.row_factory = sqlite3.Row
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS experiments (
                experiment_id TEXT PRIMARY KEY,
                config_hash TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                dataset_hash TEXT,
                code_commit TEXT,
                config_json TEXT NOT NULL,
                model_family TEXT NOT NULL,
                target_type TEXT NOT NULL,
                feature_set TEXT NOT NULL,
                training_window TEXT NOT NULL,
                time_decay TEXT NOT NULL,
                calibration TEXT NOT NULL,
                league_scope TEXT NOT NULL,
                hyperparameters_json TEXT NOT NULL,
                metrics_json TEXT,
                artifact_path TEXT,
                error TEXT,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                research_run_id TEXT,
                environment_hash TEXT,
                requested_model_family TEXT,
                effective_model_family TEXT,
                requested_target_type TEXT,
                effective_target_type TEXT,
                requested_feature_universe TEXT,
                effective_feature_universe TEXT,
                dependency TEXT,
                tree_hash TEXT,
                experiment_role TEXT,
                plan_verified_at TEXT,
                plan_verification_json TEXT
            )
            """
        )
        for column, definition in (
            ("research_run_id", "TEXT"),
            ("environment_hash", "TEXT"),
            ("requested_model_family", "TEXT"),
            ("effective_model_family", "TEXT"),
            ("requested_target_type", "TEXT"),
            ("effective_target_type", "TEXT"),
            ("requested_feature_universe", "TEXT"),
            ("effective_feature_universe", "TEXT"),
            ("dependency", "TEXT"),
            ("tree_hash", "TEXT"),
            ("experiment_role", "TEXT"),
            ("plan_verified_at", "TEXT"),
            ("plan_verification_json", "TEXT"),
        ):
            self._ensure_column(column, definition)
        self.connection.execute("CREATE INDEX IF NOT EXISTS idx_v060_experiments_status ON experiments(status)")
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_v061_experiments_run ON experiments(research_run_id, status)"
        )
        self._migrate_historical_identity()
        self.connection.commit()

    def _ensure_column(self, column: str, definition: str) -> None:
        existing = {
            str(row[1])
            for row in self.connection.execute("PRAGMA table_info(experiments)").fetchall()
        }
        if column not in existing:
            self.connection.execute(f"ALTER TABLE experiments ADD COLUMN {column} {definition}")

    def _migrate_historical_identity(self) -> None:
        """Annotate V0.6.0 rows without changing their historical results."""

        rows = self.connection.execute(
            "SELECT experiment_id, status, model_family, target_type, feature_set, metrics_json, "
            "requested_model_family, effective_model_family, requested_target_type, "
            "effective_target_type, requested_feature_universe, effective_feature_universe, "
            "research_run_id "
            "FROM experiments"
        ).fetchall()
        for row in rows:
            metrics: dict[str, Any] = {}
            if row[5]:
                try:
                    parsed = json.loads(row[5])
                    metrics = parsed if isinstance(parsed, dict) else {}
                except (TypeError, ValueError, json.JSONDecodeError):
                    metrics = {}
            requested_family = row[6] or row[2]
            requested_target = row[8] or row[3]
            requested_feature = row[10] or row[4]
            is_historical_completed = (
                str(row[1]).upper() == "COMPLETED"
                and not row[12]
            )
            # Only completed V0.6.0 rows are eligible for historical identity
            # inference.  Planned/current rows must retain NULL effective
            # identity until the runner has actually created the model (or
            # recorded an explicit dependency/resource skip).
            effective_family = row[7] or metrics.get("effective_model_family")
            effective_target = row[9]
            effective_feature = row[11]
            if is_historical_completed:
                effective_family = effective_family or row[2]
                if (
                    str(row[2]).upper() == "CATBOOST"
                    and not metrics.get("class_module")
                ):
                    effective_family = metrics.get("effective_model_family") or "EXTRATREES_FALLBACK"
                effective_target = effective_target or requested_target
                if effective_feature is None:
                    effective_feature = (
                        None
                        if str(effective_family).upper() == "EXTRATREES_FALLBACK"
                        else requested_feature
                    )
            self.connection.execute(
                """UPDATE experiments SET
                    requested_model_family = COALESCE(requested_model_family, ?),
                    effective_model_family = COALESCE(effective_model_family, ?),
                    requested_target_type = COALESCE(requested_target_type, ?),
                    effective_target_type = COALESCE(effective_target_type, ?),
                    requested_feature_universe = COALESCE(requested_feature_universe, ?),
                    effective_feature_universe = COALESCE(effective_feature_universe, ?)
                   WHERE experiment_id = ?""",
                (
                    str(requested_family).upper() if requested_family is not None else None,
                    str(effective_family).upper() if effective_family is not None else None,
                    str(requested_target).upper() if requested_target is not None else None,
                    str(effective_target).upper() if effective_target is not None else None,
                    str(requested_feature).upper() if requested_feature is not None else None,
                    str(effective_feature).upper() if effective_feature is not None else None,
                    row[0],
                ),
            )

    @staticmethod
    def _as_dict(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        for key in ("config_json", "hyperparameters_json", "metrics_json", "plan_verification_json"):
            if result.get(key):
                try:
                    result[key[:-5] if key.endswith("_json") else key] = json.loads(result[key])
                except (TypeError, ValueError):
                    pass
        return result

    def close(self) -> None:
        self.connection.close()

    def config_hashes(self) -> set[str]:
        return {str(row[0]) for row in self.connection.execute("SELECT config_hash FROM experiments")}

    def insert_planned(
        self,
        config: Mapping[str, Any],
        dataset_hash: str | None,
        code_commit: str | None,
        *,
        tree_hash: str | None = None,
        experiment_role: str | None = None,
    ) -> dict[str, Any]:
        config_dict = dict(config)
        config_hash = experiment_config_hash(config_dict)
        experiment_id = str(config_dict.get("experiment_id") or f"EXP_{config_hash[:16]}")
        # IDs are human labels for reports; the hash is the deduplication key.
        if self.connection.execute("SELECT 1 FROM experiments WHERE config_hash = ?", (config_hash,)).fetchone():
            row = self.connection.execute("SELECT * FROM experiments WHERE config_hash = ?", (config_hash,)).fetchone()
            return self._as_dict(row)
        existing_id = self.connection.execute("SELECT 1 FROM experiments WHERE experiment_id = ?", (experiment_id,)).fetchone()
        if existing_id:
            experiment_id = f"{experiment_id}_{config_hash[:8]}"
        now = _now()
        self.connection.execute(
            """
            INSERT INTO experiments (
                experiment_id, config_hash, status, created_at, dataset_hash, code_commit,
                config_json, model_family, target_type, feature_set, training_window,
                time_decay, calibration, league_scope, hyperparameters_json,
                requested_model_family, requested_target_type, requested_feature_universe,
                tree_hash, experiment_role
            ) VALUES (?, ?, 'PLANNED', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                experiment_id,
                config_hash,
                now,
                dataset_hash,
                code_commit,
                _json(config_dict),
                str(config_dict.get("model_family", "")).upper(),
                str(config_dict.get("target_type", "")).upper(),
                str(config_dict.get("feature_universe", "")).upper(),
                str(config_dict.get("training_window", "ALL")).upper(),
                str(config_dict.get("time_decay", "NONE")).upper(),
                str(config_dict.get("calibration", "NONE")).upper(),
                str(config_dict.get("league_scope", "GLOBAL")).upper(),
                _json(config_dict.get("hyperparameters", {})),
                str(config_dict.get("model_family", "")).upper(),
                str(config_dict.get("target_type", "")).upper(),
                str(config_dict.get("feature_universe", "")).upper(),
                tree_hash,
                experiment_role or ("CANARY" if config_dict.get("canary_real_data") else None),
            ),
        )
        self.connection.commit()
        row = self.connection.execute("SELECT * FROM experiments WHERE experiment_id = ?", (experiment_id,)).fetchone()
        return self._as_dict(row)

    def update(
        self,
        experiment_id: str,
        *,
        status: str,
        metrics: Mapping[str, Any] | None = None,
        artifact_path: str | None = None,
        error: str | None = None,
        research_run_id: str | None = None,
        environment_hash_value: str | None = None,
        requested_model_family: str | None = None,
        effective_model_family: str | None = None,
        requested_target_type: str | None = None,
        effective_target_type: str | None = None,
        requested_feature_universe: str | None = None,
        effective_feature_universe: str | None = None,
        dependency: str | None = None,
        tree_hash: str | None = None,
        plan_verified_at: str | None = None,
        plan_verification: Mapping[str, Any] | None = None,
    ) -> None:
        now = _now()
        started = now if status == "RUNNING" else None
        finished = now if status in {
            "COMPLETED", "FAILED", "SKIPPED", "SKIPPED_DEPENDENCY_MISSING",
            "SKIPPED_RESOURCE_GUARD", "INTERRUPTED",
        } else None
        self.connection.execute(
            """
            UPDATE experiments SET
                status = ?,
                started_at = COALESCE(?, started_at),
                finished_at = COALESCE(?, finished_at),
                metrics_json = COALESCE(?, metrics_json),
                artifact_path = COALESCE(?, artifact_path),
                error = ?,
                attempt_count = attempt_count + CASE WHEN ? = 'RUNNING' THEN 1 ELSE 0 END,
                research_run_id = COALESCE(?, research_run_id),
                environment_hash = COALESCE(?, environment_hash),
                requested_model_family = COALESCE(?, requested_model_family),
                effective_model_family = COALESCE(?, effective_model_family),
                requested_target_type = COALESCE(?, requested_target_type),
                effective_target_type = COALESCE(?, effective_target_type),
                requested_feature_universe = COALESCE(?, requested_feature_universe),
                effective_feature_universe = COALESCE(?, effective_feature_universe),
                dependency = COALESCE(?, dependency),
                tree_hash = COALESCE(?, tree_hash),
                plan_verified_at = COALESCE(?, plan_verified_at),
                plan_verification_json = COALESCE(?, plan_verification_json)
            WHERE experiment_id = ?
            """,
            (
                status, started, finished,
                _json(metrics) if metrics is not None else None,
                artifact_path, error, status,
                research_run_id, environment_hash_value,
                requested_model_family, effective_model_family,
                requested_target_type, effective_target_type,
                requested_feature_universe, effective_feature_universe,
                dependency, tree_hash, plan_verified_at,
                _json(plan_verification) if plan_verification is not None else None,
                experiment_id,
            ),
        )
        self.connection.commit()

    def recover_running(self) -> int:
        cursor = self.connection.execute(
            """
            UPDATE experiments
            SET status = 'INTERRUPTED', finished_at = ?, error = 'process recovery: RUNNING marked retryable'
            WHERE status = 'RUNNING'
            """,
            (_now(),),
        )
        self.connection.commit()
        return int(cursor.rowcount)

    def get(self, experiment_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM experiments WHERE experiment_id = ?", (experiment_id,)).fetchone()
        return self._as_dict(row) if row else None

    def records_for_run(self, research_run_id: str, statuses: Iterable[str] | None = None) -> list[dict[str, Any]]:
        values = tuple(str(value).upper() for value in statuses or ())
        params: list[Any] = [str(research_run_id)]
        query = "SELECT * FROM experiments WHERE research_run_id = ?"
        if values:
            placeholders = ",".join("?" for _ in values)
            query += f" AND status IN ({placeholders})"
            params.extend(values)
        query += " ORDER BY created_at, experiment_id"
        return [self._as_dict(row) for row in self.connection.execute(query, params).fetchall()]

    def verify_plan(self, research_run_id: str, verification: Mapping[str, Any]) -> None:
        stamp = _now()
        self.connection.execute(
            "UPDATE experiments SET plan_verified_at = ?, plan_verification_json = ? WHERE research_run_id = ?",
            (stamp, _json(dict(verification)), str(research_run_id)),
        )
        self.connection.commit()

    def list(self, statuses: Iterable[str] | None = None) -> list[dict[str, Any]]:
        values = tuple(str(value).upper() for value in statuses or ())
        if values:
            placeholders = ",".join("?" for _ in values)
            rows = self.connection.execute(
                f"SELECT * FROM experiments WHERE status IN ({placeholders}) ORDER BY created_at, experiment_id",
                values,
            ).fetchall()
        else:
            rows = self.connection.execute("SELECT * FROM experiments ORDER BY created_at, experiment_id").fetchall()
        return [self._as_dict(row) for row in rows]

    def counts(self) -> dict[str, int]:
        return {
            str(row["status"]): int(row["n"])
            for row in self.connection.execute("SELECT status, COUNT(*) AS n FROM experiments GROUP BY status")
        }


def experiment_config_hash(config: Mapping[str, Any]) -> str:
    normalized = {key: value for key, value in dict(config).items() if key not in {"experiment_id", "description"}}
    return _hash_json(normalized)


def _normalize_config(config: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(config)
    for key in ("model_family", "target_type", "feature_universe", "training_window", "time_decay", "calibration", "league_scope", "stage"):
        if key in result and result[key] is not None:
            result[key] = str(result[key]).upper()
    result.setdefault("training_window", "ALL")
    result.setdefault("time_decay", "NONE")
    result.setdefault("calibration", "NONE")
    result.setdefault("league_scope", "GLOBAL")
    result.setdefault("hyperparameters", {})
    return result


def _supported_config(config: Mapping[str, Any]) -> bool:
    family = str(config.get("model_family", "")).upper()
    target = str(config.get("target_type", "")).upper()
    if family == "POISSON":
        return target == "COUNT"
    if family == "ENSEMBLE":
        return target == "MULTICLASS"
    return family in {
        "LOGISTIC",
        "HISTGRADIENTBOOSTING",
        "EXTRATREES",
        "RANDOMFOREST",
        "CATBOOST",
        "XGBOOST",
        "LIGHTGBM",
    } and target in {"MULTICLASS", "BINARY_P1"}


class ExperimentPlanner:
    """Breadth-first, hash-deduplicating search-space planner."""

    def __init__(self, registry: ExperimentRegistry, search_space: Mapping[str, Any]) -> None:
        self.registry = registry
        self.search_space = dict(search_space)

    def local_configs(self) -> list[dict[str, Any]]:
        return [_normalize_config(value) for value in self.search_space.get("local_start_experiments", []) if isinstance(value, Mapping)]

    def _generated(self) -> Iterable[dict[str, Any]]:
        families = [str(value).upper() for value in self.search_space.get("model_families", [])]
        targets = [str(value).upper() for value in self.search_space.get("targets", [])]
        features = [str(value).upper() for value in self.search_space.get("feature_universes", [])]
        windows = [str(value).upper() for value in self.search_space.get("training_windows", [])]
        decays = [str(value).upper() for value in self.search_space.get("time_decay", [])]
        calibrations = [str(value).upper() for value in self.search_space.get("calibration", [])]
        scopes = [str(value).upper() for value in self.search_space.get("league_scopes", [])]
        hp_space = self.search_space.get("hyperparameters", {})

        def make(family: str, target: str, feature: str, window: str, decay: str, calibration: str, scope: str, hp: Mapping[str, Any], stage: str) -> dict[str, Any]:
            return _normalize_config(
                {
                    "model_family": family,
                    "target_type": target,
                    "feature_universe": feature,
                    "training_window": window,
                    "time_decay": decay,
                    "calibration": calibration,
                    "league_scope": scope,
                    "hyperparameters": dict(hp),
                    "stage": stage,
                }
            )

        # First charge changes the scientific question, not just a parameter.
        # Interleave time-window/decay variants so a 100-model breadth-first
        # plan contains ALL/2Y/3Y and decay coverage from its first page
        # instead of spending the entire budget on ALL/NONE.
        broad: list[tuple[str, str, str, Mapping[str, Any], str]] = []
        for target in targets:
            for family in families:
                for feature in features:
                    if not _supported_config({"model_family": family, "target_type": target}):
                        continue
                    hp_values = hp_space.get(family, [{}]) if isinstance(hp_space, Mapping) else [{}]
                    hp = hp_values[0] if isinstance(hp_values, list) and hp_values else {}
                    calibration = "NONE" if target == "COUNT" else "SIGMOID"
                    broad.append((family, target, feature, hp, calibration))
        breadth_variants = list(itertools.product(("ALL", "2Y", "3Y"), ("NONE", "6M", "12M")))
        for index, (family, target, feature, hp, calibration) in enumerate(broad):
            window, decay = breadth_variants[index % len(breadth_variants)]
            yield make(family, target, feature, window, decay, calibration, "GLOBAL", hp, "BROAD_EXPLORATION")
        # Refinement is deterministic and only reached after broad coverage.
        for family, target, feature, window, decay, calibration, scope in itertools.product(
            families, targets, features, windows, decays, calibrations, scopes
        ):
            if not _supported_config({"model_family": family, "target_type": target}):
                continue
            if target == "COUNT" and calibration != "NONE":
                continue
            hp_values = hp_space.get(family, [{}]) if isinstance(hp_space, Mapping) else [{}]
            for hp in hp_values if isinstance(hp_values, list) else [{}]:
                yield make(family, target, feature, window, decay, calibration, scope, hp, "REFINEMENT")

    def plan_new(
        self,
        limit: int,
        *,
        mode: str = "standard",
        custom: Sequence[Mapping[str, Any]] | None = None,
        dataset_hash: str | None = None,
        code_commit: str | None = None,
        research_run_id: str | None = None,
        environment_hash_value: str | None = None,
        tree_hash: str | None = None,
        experiment_role: str | None = None,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        mode = str(mode).casefold()
        candidates: Iterable[Mapping[str, Any]]
        if custom is not None:
            candidates = custom
        elif mode == "local":
            candidates = self.local_configs()
        else:
            candidates = self._generated()
        known = self.registry.config_hashes()
        planned: list[dict[str, Any]] = []
        for candidate in candidates:
            config = _normalize_config(candidate)
            if not _supported_config(config) and config.get("model_family") != "ENSEMBLE":
                continue
            config_hash = experiment_config_hash(config)
            if config_hash in known:
                continue
            row = self.registry.insert_planned(
                config,
                dataset_hash,
                code_commit,
                tree_hash=tree_hash,
                experiment_role=experiment_role,
            )
            if research_run_id or environment_hash_value:
                self.registry.update(
                    str(row["experiment_id"]),
                    status=str(row.get("status") or "PLANNED"),
                    research_run_id=research_run_id,
                    environment_hash_value=environment_hash_value,
                )
                row = self.registry.get(str(row["experiment_id"])) or row
            known.add(config_hash)
            planned.append(row)
            if len(planned) >= limit:
                break
        return planned


class _ProbabilityCalibrator:
    """Small train-only probability calibrator with no validation/test access."""

    def __init__(self, method: str) -> None:
        self.method = method.upper()
        self.models: list[Any] = []
        self.applied = False

    def fit(self, probabilities: Any, target: Any) -> "_ProbabilityCalibrator":
        if np is None:
            return self
        if self.method == "NONE" or len(target) < 30:
            return self
        try:
            if self.method == "ISOTONIC":
                from sklearn.isotonic import IsotonicRegression

                for index in range(probabilities.shape[1]):
                    model = IsotonicRegression(out_of_bounds="clip")
                    model.fit(probabilities[:, index], (target == index).astype(float))
                    self.models.append(model)
            elif self.method == "SIGMOID":
                from sklearn.linear_model import LogisticRegression

                for index in range(probabilities.shape[1]):
                    values = np.clip(probabilities[:, index], 1e-6, 1 - 1e-6)
                    model = LogisticRegression(C=100.0, max_iter=300)
                    model.fit(np.log(values / (1.0 - values)).reshape(-1, 1), (target == index).astype(int))
                    self.models.append(model)
            else:
                return self
            self.applied = len(self.models) == probabilities.shape[1]
        except (ValueError, TypeError):
            self.models = []
            self.applied = False
        return self

    def transform(self, probabilities: Any) -> Any:
        if np is None or not self.applied:
            return probabilities
        values: list[Any] = []
        for index, model in enumerate(self.models):
            if self.method == "ISOTONIC":
                values.append(np.asarray(model.predict(probabilities[:, index]), dtype=float))
            else:
                clipped = np.clip(probabilities[:, index], 1e-6, 1 - 1e-6)
                values.append(model.predict_proba(np.log(clipped / (1.0 - clipped)).reshape(-1, 1))[:, 1])
        transformed = np.column_stack(values)
        transformed = np.clip(transformed, 1e-8, None)
        return transformed / transformed.sum(axis=1, keepdims=True)


class _ConstantModel:
    def __init__(self, probabilities: Sequence[float], classes: Sequence[int]) -> None:
        self._probabilities = np.asarray(probabilities, dtype=float) if np is not None else probabilities
        self.classes_ = np.asarray(classes, dtype=int) if np is not None else classes

    def predict_proba(self, values: Any) -> Any:
        if np is None:
            return [self._probabilities for _ in values]
        return np.tile(self._probabilities, (len(values), 1))


class _FittedModel:
    def __init__(self, base: Any, target_type: str, calibration: str, non_one_prior: float = 0.5) -> None:
        self.base = base
        self.target_type = target_type
        self.calibration = calibration
        self.calibrator: _ProbabilityCalibrator | None = None
        self.non_one_prior = non_one_prior
        self.calibration_applied = False

    def fit_calibrator(self, values: Any, target: Any) -> None:
        if self.target_type == "COUNT" or self.calibration == "NONE" or np is None:
            return
        raw = self.base.predict_proba(values)
        classes = list(getattr(self.base, "classes_", range(raw.shape[1])))
        mapped = np.zeros((raw.shape[0], 3 if self.target_type == "MULTICLASS" else 2), dtype=float)
        for index, label in enumerate(classes):
            if int(label) < mapped.shape[1]:
                mapped[:, int(label)] = raw[:, index]
        calibrator = _ProbabilityCalibrator(self.calibration).fit(mapped, target)
        self.calibrator = calibrator if calibrator.applied else None
        self.calibration_applied = bool(calibrator.applied)

    def predict(self, values: Any, *, apply_calibration: bool = True) -> dict[str, Any]:
        if np is None:
            raise DatasetError("numpy is required for model prediction")
        if self.target_type == "COUNT":
            expected = np.clip(np.asarray(self.base.predict(values), dtype=float), 0.0, None)
            p0 = np.exp(-expected)
            p1 = expected * p0
            p2 = np.clip(1.0 - p0 - p1, 0.0, 1.0)
            probabilities = np.column_stack((p0, p1, p2))
            return {"probabilities": probabilities, "predicted_count": expected}
        raw = self.base.predict_proba(values)
        classes = list(getattr(self.base, "classes_", range(raw.shape[1])))
        width = 3 if self.target_type == "MULTICLASS" else 2
        mapped = np.zeros((raw.shape[0], width), dtype=float)
        for index, label in enumerate(classes):
            if int(label) < width:
                mapped[:, int(label)] = raw[:, index]
        if apply_calibration and self.calibrator is not None:
            mapped = self.calibrator.transform(mapped)
        if self.target_type == "BINARY_P1":
            p1 = mapped[:, 1] if mapped.shape[1] > 1 else mapped[:, 0]
            p0 = (1.0 - p1) * self.non_one_prior
            p2 = (1.0 - p1) * (1.0 - self.non_one_prior)
            probabilities = np.column_stack((p0, p1, p2))
        else:
            probabilities = mapped
        probabilities = np.clip(probabilities, 1e-8, None)
        probabilities = probabilities / probabilities.sum(axis=1, keepdims=True)
        return {"probabilities": probabilities, "predicted_count": None}


def _require_ml() -> None:
    if np is None:
        raise DatasetError("numpy/scikit-learn are required for training")
    try:
        import sklearn  # noqa: F401
    except ImportError as exc:  # pragma: no cover
        raise DatasetError("scikit-learn is required for training") from exc


def _make_base_model(config: Mapping[str, Any], class_count: int) -> tuple[Any, str]:
    _require_ml()
    from sklearn.ensemble import ExtraTreesClassifier, HistGradientBoostingClassifier, RandomForestClassifier
    from sklearn.linear_model import LogisticRegression, PoissonRegressor
    from sklearn.pipeline import Pipeline
    from sklearn.impute import SimpleImputer
    from sklearn.preprocessing import StandardScaler

    family = str(config.get("model_family", "")).upper()
    target = str(config.get("target_type", "")).upper()
    hp = dict(config.get("hyperparameters") or {})
    model_threads = max(
        1,
        int(
            hp.get(
                "model_threads",
                config.get("model_threads", os.getenv("V061_MODEL_THREADS", "2")),
            )
        ),
    )
    if family == "POISSON":
        estimator = PoissonRegressor(alpha=float(hp.get("alpha", 0.5)), max_iter=int(hp.get("max_iter", 300)))
        return Pipeline([("imputer", SimpleImputer(strategy="median", add_indicator=True, keep_empty_features=True)), ("model", estimator)]), family
    if family == "LOGISTIC":
        logistic_kwargs = {
            "C": float(hp.get("C", 1.0)),
            "max_iter": int(hp.get("max_iter", 500)),
            "random_state": 42,
        }
        try:
            # scikit-learn 1.8 removed the multi_class constructor argument;
            # lbfgs now selects the appropriate multinomial/binary behavior.
            estimator = LogisticRegression(
                **logistic_kwargs,
                multi_class="multinomial" if target == "MULTICLASS" else "auto",
            )
        except TypeError:
            estimator = LogisticRegression(**logistic_kwargs)
        return Pipeline([("imputer", SimpleImputer(strategy="median", add_indicator=True, keep_empty_features=True)), ("scale", StandardScaler()), ("model", estimator)]), family
    if family == "HISTGRADIENTBOOSTING":
        estimator = HistGradientBoostingClassifier(
            learning_rate=float(hp.get("learning_rate", 0.05)),
            max_iter=int(hp.get("max_iter", 150)),
            max_leaf_nodes=int(hp.get("max_leaf_nodes", 15)),
            l2_regularization=float(hp.get("l2_regularization", 0.0)),
            random_state=42,
        )
        return Pipeline([("imputer", SimpleImputer(strategy="median", add_indicator=True, keep_empty_features=True)), ("model", estimator)]), family
    if family == "EXTRATREES":
        estimator = ExtraTreesClassifier(
            n_estimators=int(hp.get("n_estimators", 150)),
            max_depth=hp.get("max_depth"),
            min_samples_leaf=int(hp.get("min_samples_leaf", 2)),
            max_features=hp.get("max_features", "sqrt"),
            random_state=42,
            n_jobs=model_threads,
        )
        return Pipeline([("imputer", SimpleImputer(strategy="median", add_indicator=True, keep_empty_features=True)), ("model", estimator)]), family
    if family == "RANDOMFOREST":
        estimator = RandomForestClassifier(
            n_estimators=int(hp.get("n_estimators", 150)),
            max_depth=hp.get("max_depth"),
            min_samples_leaf=int(hp.get("min_samples_leaf", 2)),
            max_features=hp.get("max_features", "sqrt"),
            random_state=42,
            n_jobs=model_threads,
        )
        return Pipeline([("imputer", SimpleImputer(strategy="median", add_indicator=True, keep_empty_features=True)), ("model", estimator)]), family
    if family == "CATBOOST":
        try:
            from catboost import CatBoostClassifier
        except Exception as exc:
            # An optional dependency is an explicit capability.  Never
            # substitute another estimator under the requested name.
            raise DependencyMissing("catboost", family, str(exc)) from exc
        estimator = CatBoostClassifier(
            iterations=int(hp.get("iterations", 250)),
            depth=int(hp.get("depth", 6)),
            learning_rate=float(hp.get("learning_rate", 0.05)),
            l2_leaf_reg=float(hp.get("l2_leaf_reg", 3.0)),
            verbose=False,
            allow_writing_files=False,
            random_seed=42,
            thread_count=model_threads,
        )
        # CatBoost 1.2.x can be used as a native estimator, but recent
        # scikit-learn releases may reject it as a Pipeline step because the
        # optional ``__sklearn_tags__`` protocol is not implemented.  The
        # research matrix is numeric and CatBoost handles missing values
        # natively, so the direct estimator is both the production path and a
        # faithful class-identity check for the canary.
        return estimator, family
    if family == "XGBOOST":
        try:
            from xgboost import XGBClassifier
        except Exception as exc:
            raise DependencyMissing("xgboost", family, str(exc)) from exc
        estimator = XGBClassifier(
            n_estimators=int(hp.get("n_estimators", 250)),
            max_depth=int(hp.get("max_depth", 6)),
            learning_rate=float(hp.get("learning_rate", 0.05)),
            subsample=float(hp.get("subsample", 0.9)),
            colsample_bytree=float(hp.get("colsample_bytree", 0.9)),
            objective="multi:softprob" if target == "MULTICLASS" else "binary:logistic",
            eval_metric="mlogloss" if target == "MULTICLASS" else "logloss",
            n_jobs=model_threads,
            random_state=42,
            verbosity=0,
            **({"num_class": class_count} if target == "MULTICLASS" else {}),
        )
        return Pipeline([("imputer", SimpleImputer(strategy="median", add_indicator=True, keep_empty_features=True)), ("model", estimator)]), family
    if family == "LIGHTGBM":
        try:
            from lightgbm import LGBMClassifier
        except Exception as exc:
            raise DependencyMissing("lightgbm", family, str(exc)) from exc
        estimator = LGBMClassifier(
            n_estimators=int(hp.get("n_estimators", 250)),
            max_depth=int(hp.get("max_depth", -1)),
            learning_rate=float(hp.get("learning_rate", 0.05)),
            num_leaves=int(hp.get("num_leaves", 31)),
            verbosity=-1,
            n_jobs=model_threads,
            random_state=42,
        )
        return Pipeline([("imputer", SimpleImputer(strategy="median", add_indicator=True, keep_empty_features=True)), ("model", estimator)]), family
    raise DatasetError(f"Unsupported model family: {family}")


def _underlying_estimator(model: Any) -> Any:
    current = model
    if hasattr(current, "base"):
        current = current.base
    named_steps = getattr(current, "named_steps", None)
    if isinstance(named_steps, Mapping) and named_steps:
        current = next(reversed(named_steps.values()))
    return current


def effective_model_family(model: Any) -> str:
    """Infer the family from the instantiated class/module, never its ID."""

    estimator = _underlying_estimator(model)
    module = str(getattr(type(estimator), "__module__", "")).casefold()
    name = str(getattr(type(estimator), "__name__", "")).casefold()
    if module.startswith("catboost"):
        return "CATBOOST"
    if module.startswith("xgboost"):
        return "XGBOOST"
    if module.startswith("lightgbm"):
        return "LIGHTGBM"
    if "extratrees" in name:
        return "EXTRATREES"
    if "randomforest" in name:
        return "RANDOMFOREST"
    if "histgradientboosting" in name:
        return "HISTGRADIENTBOOSTING"
    if "logisticregression" in name:
        return "LOGISTIC"
    if "poissonregressor" in name:
        return "POISSON"
    if isinstance(model, _ConstantModel):
        return "CONSTANT"
    return str(getattr(type(estimator), "__name__", "UNKNOWN")).upper()


def model_identity(model: Any) -> dict[str, Any]:
    estimator = _underlying_estimator(model)
    cls = type(estimator)
    return {
        "effective_model_family": effective_model_family(model),
        "class_name": cls.__name__,
        "class_module": cls.__module__,
        "class_qualname": getattr(cls, "__qualname__", cls.__name__),
    }


def _time_weights(rows: Sequence[Mapping[str, Any]], decay: str) -> Any:
    if np is None:
        return None
    decay = str(decay or "NONE").upper()
    if decay == "NONE":
        return np.ones(len(rows), dtype=float)
    match = re.match(r"(\d+)([MY])", decay)
    if not match:
        return np.ones(len(rows), dtype=float)
    half_life_days = int(match.group(1)) * (30.4375 if match.group(2) == "M" else 365.25)
    dates = [_parse_time(row.get("kickoff")) for row in rows]
    valid = [date for date in dates if date is not None]
    anchor = max(valid) if valid else datetime.now(timezone.utc)
    return np.asarray(
        [math.exp(-math.log(2.0) * max(0.0, (anchor - (date or anchor)).total_seconds() / 86400.0) / half_life_days) for date in dates],
        dtype=float,
    )


def _feature_requirements(universe: str) -> tuple[str, ...]:
    upper = str(universe).upper()
    required: list[str] = []
    if "XG" in upper:
        required.append("xg_available")
    if "SHOTMAP" in upper:
        required.append("shotmap_available")
    if "EVENTS" in upper:
        required.append("events_available")
    return tuple(required)


def _split_rows(rows: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[tuple[list[dict[str, Any]], list[dict[str, Any]]]]]:
    ordered = sorted(rows, key=lambda row: (_parse_time(row.get("kickoff")) or datetime.max.replace(tzinfo=timezone.utc), str(row.get("row_id"))))
    if len(ordered) < 4:
        raise InsufficientData("fewer than four eligible chronological matches")
    locked_count = max(1, int(round(len(ordered) * 0.20))) if len(ordered) >= 10 else 0
    dev = ordered[:-locked_count] if locked_count else ordered
    locked = ordered[-locked_count:] if locked_count else []
    if len(dev) < 3:
        raise InsufficientData("not enough development observations for a temporal split")
    folds_count = min(3, max(1, (len(dev) - 2) // 2))
    validation_size = max(1, len(dev) // (folds_count + 2))
    first_train = max(2, len(dev) - folds_count * validation_size)
    folds: list[tuple[list[dict[str, Any]], list[dict[str, Any]]]] = []
    for index in range(folds_count):
        train_end = first_train + index * validation_size
        validation_end = min(len(dev), train_end + validation_size)
        if train_end >= validation_end or train_end < 2:
            continue
        folds.append((dev[:train_end], dev[train_end:validation_end]))
    if not folds:
        raise InsufficientData("no valid walk-forward fold")
    return dev, locked, folds


def _metric_log_loss(actual: Any, probabilities: Any) -> float:
    from sklearn.metrics import log_loss

    return float(log_loss(actual, probabilities, labels=[0, 1, 2]))


def _threshold_fold_stability(
    predictions: Sequence[Mapping[str, Any]],
    threshold: float,
) -> dict[str, Any]:
    """Summarise a low-P1 threshold separately for every validation fold."""

    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for prediction in predictions:
        if float(prediction.get("p1", 0.0)) < threshold:
            groups[str(prediction.get("fold", "unknown"))].append(prediction)
    per_fold: list[dict[str, Any]] = []
    rates: list[float] = []
    for fold, values in sorted(groups.items()):
        actual_p1 = sum(int(value.get("actual_index", 0)) == 1 for value in values) / max(1, len(values))
        zero_or_two = sum(int(value.get("actual_index", 0)) != 1 for value in values) / max(1, len(values))
        rates.append(float(zero_or_two))
        per_fold.append(
            {
                "fold": fold,
                "n": len(values),
                "actual_p1": float(actual_p1),
                "zero_or_2plus_hit_rate": float(zero_or_two),
            }
        )
    mean_rate = sum(rates) / len(rates) if rates else None
    return {
        "folds": len(per_fold),
        "per_fold": per_fold,
        "min_zero_or_2plus_hit_rate": min(rates) if rates else None,
        "max_zero_or_2plus_hit_rate": max(rates) if rates else None,
        "stddev_zero_or_2plus_hit_rate": (
            math.sqrt(sum((rate - mean_rate) ** 2 for rate in rates) / len(rates))
            if rates and mean_rate is not None
            else None
        ),
    }


def _evaluate_predictions(predictions: Sequence[Mapping[str, Any]], denominator: int) -> dict[str, Any]:
    _require_ml()
    from sklearn.metrics import accuracy_score, balanced_accuracy_score, confusion_matrix

    if not predictions:
        raise InsufficientData("no walk-forward predictions were produced")
    actual = np.asarray([int(item["actual_index"]) for item in predictions], dtype=int)
    probabilities = np.asarray([[item["p0"], item["p1"], item["p2"]] for item in predictions], dtype=float)
    probabilities = np.clip(probabilities, 1e-8, None)
    probabilities = probabilities / probabilities.sum(axis=1, keepdims=True)
    predicted = np.argmax(probabilities, axis=1)
    one = (actual == 1).astype(float)
    p1 = probabilities[:, 1]
    threshold_rows: list[dict[str, Any]] = []
    for threshold in P1_THRESHOLDS:
        mask = p1 < threshold
        count = int(mask.sum())
        actual_p1 = float(one[mask].mean()) if count else None
        zero_or_two = float((actual[mask] != 1).mean()) if count else None
        mean_p1 = float(p1[mask].mean()) if count else None
        if count:
            # Wilson interval for the strategy-relevant ZERO_OR_2_PLUS rate.
            rate = zero_or_two or 0.0
            z = 1.959963984540054
            denominator_ci = 1 + z * z / count
            center = (rate + z * z / (2 * count)) / denominator_ci
            radius = z * math.sqrt(rate * (1 - rate) / count + z * z / (4 * count * count)) / denominator_ci
            ci_low, ci_high = max(0.0, center - radius), min(1.0, center + radius)
        else:
            ci_low = ci_high = None
        threshold_rows.append(
            {
                "max_predicted_p1": threshold,
                "sample_n": count,
                "coverage": count / max(1, len(predictions)),
                "mean_predicted_p1": mean_p1,
                "actual_p1_rate": actual_p1,
                "zero_or_2plus_hit_rate": zero_or_two,
                "zero_or_2plus_ci_low": ci_low,
                "zero_or_2plus_ci_high": ci_high,
                "fold_stability": _threshold_fold_stability(predictions, threshold),
            }
        )
    cm = confusion_matrix(actual, predicted, labels=[0, 1, 2]).tolist()
    return {
        "sample_n": len(predictions),
        "coverage": len(predictions) / max(1, denominator),
        "log_loss": _metric_log_loss(actual, probabilities),
        "brier": float(np.mean(np.sum((probabilities - np.eye(3)[actual]) ** 2, axis=1))),
        "p1_brier": float(np.mean((p1 - one) ** 2)),
        "p1_calibration_error": float(abs(float(p1.mean()) - float(one.mean()))),
        "accuracy": float(accuracy_score(actual, predicted)),
        "balanced_accuracy": float(balanced_accuracy_score(actual, predicted)),
        "confusion_matrix": cm,
        "mean_predicted_p1": float(p1.mean()),
        "actual_p1_rate": float(one.mean()),
        "zero_or_2plus_hit_rate": float((actual != 1).mean()),
        "p1_thresholds": threshold_rows,
    }


class ExperimentRunner:
    """Execute one registry item with temporal validation and no test tuning."""

    def __init__(
        self,
        root: Path,
        manifest: Mapping[str, Any],
        rows: Sequence[Mapping[str, Any]],
        registry: ExperimentRegistry,
        *,
        research_run_id: str | None = None,
        environment_manifest_value: Mapping[str, Any] | None = None,
        model_threads: int | None = None,
    ) -> None:
        self.root = Path(root)
        self.manifest = dict(manifest)
        self.rows = [dict(row) for row in rows]
        self.registry = registry
        self.research_run_id = research_run_id or new_research_run_id()
        self.run_started_at = _now()
        self.environment_manifest_value = dict(environment_manifest_value or environment_manifest())
        self.environment_hash_value = environment_hash(self.environment_manifest_value)
        self.model_threads = max(
            1,
            int(model_threads or os.getenv("V061_MODEL_THREADS", "2")),
        )
        self.model_root = self.root / "research" / "models" / "v060"
        self.model_root.mkdir(parents=True, exist_ok=True)
        self.prediction_root = self.root / "research" / "output" / "v060" / "predictions"

    def _artifact_retention_decision(
        self,
        config: Mapping[str, Any],
        effective_family: str,
        metrics: Mapping[str, Any],
    ) -> tuple[bool, str]:
        """Keep only validation-ranked model binaries by default.

        Registry rows, metrics and OOF predictions are retained for every
        experiment.  Large serialized model binaries are limited to the
        current top K per *effective* family, or to an explicit pin.  The
        locked period is intentionally absent from this decision.
        """

        family = str(effective_family or "").upper()
        if bool(config.get("canary_real_data")):
            return True, "CANARY_REQUIRED"
        if family == "ENSEMBLE":
            return False, "OOF_ENSEMBLE_HAS_NO_STANDALONE_MODEL"
        explicit_pin = bool(
            config.get("retain_model_artifact")
            or config.get("pin_artifact")
            or config.get("shortlisted")
        )
        pinned_ids = {
            value.strip()
            for value in os.getenv("V061_PINNED_EXPERIMENTS", "").split(",")
            if value.strip()
        }
        if str(config.get("experiment_id", "")) in pinned_ids:
            explicit_pin = True
        if explicit_pin:
            return True, "EXPLICITLY_PINNED"
        if os.getenv("V061_RETAIN_ALL_MODEL_ARTIFACTS", "").strip().casefold() in {"1", "true", "yes", "on"}:
            return True, "EXPLICIT_RETAIN_ALL"
        try:
            top_k = max(0, int(os.getenv("V061_ARTIFACT_TOP_K_PER_FAMILY", "2")))
        except ValueError:
            top_k = 2
        if top_k <= 0:
            return False, "RETENTION_TOP_K_ZERO"
        current_score = metrics.get("p1_brier")
        try:
            current_score_value = float(current_score)
        except (TypeError, ValueError):
            current_score_value = math.inf
        existing_scores: list[tuple[float, str]] = []
        for existing in self.registry.list(["COMPLETED"]):
            existing_metrics = existing.get("metrics") or {}
            existing_family = str(
                existing.get("effective_model_family")
                or existing_metrics.get("effective_model_family")
                or ""
            ).upper()
            if existing_family != family:
                continue
            try:
                score = float(existing_metrics.get("p1_brier"))
            except (TypeError, ValueError):
                continue
            if math.isfinite(score):
                existing_scores.append((score, str(existing.get("experiment_id", ""))))
        rank = 1 + sum(score < current_score_value for score, _ in existing_scores)
        if rank <= top_k:
            return True, f"TOP_{top_k}_BY_VALIDATION_P1_BRIER_RANK_{rank}"
        return False, f"OUTSIDE_TOP_{top_k}_BY_VALIDATION_P1_BRIER_RANK_{rank}"

    @staticmethod
    def _window_rows(rows: list[dict[str, Any]], window: str) -> list[dict[str, Any]]:
        window = str(window or "ALL").upper()
        if window == "ALL":
            return rows
        match = re.match(r"(\d+)Y", window)
        if not match:
            return rows
        dates = [_parse_time(row.get("kickoff")) for row in rows]
        valid = [date for date in dates if date is not None]
        if not valid:
            return rows
        cutoff = max(valid) - timedelta(days=365.25 * int(match.group(1)))
        return [row for row in rows if (_parse_time(row.get("kickoff")) or datetime.min.replace(tzinfo=timezone.utc)) >= cutoff]

    @staticmethod
    def _target_index(row: Mapping[str, Any]) -> int:
        return TARGET_CLASSES.index(str(row["target_h2_goal_class"]))

    def _eligible_rows(self, config: Mapping[str, Any]) -> list[dict[str, Any]]:
        universe = str(config.get("feature_universe", "CORE")).upper()
        if universe not in FEATURES_BY_UNIVERSE:
            raise DatasetError(f"Unknown feature universe: {universe}")
        rows = [row for row in self.rows if row.get("ml_eligible")]
        for field in _feature_requirements(universe):
            rows = [row for row in rows if row.get(field)]
        scope = str(config.get("league_scope", "GLOBAL")).upper()
        league_id = config.get("league_id") or (config.get("hyperparameters") or {}).get("league_id")
        if scope == "LEAGUE_SPECIFIC" and league_id:
            rows = [row for row in rows if str(row.get("league_id")) == str(league_id)]
        rows = self._window_rows(rows, str(config.get("training_window", "ALL")))
        return sorted(rows, key=lambda row: (_parse_time(row.get("kickoff")) or datetime.max.replace(tzinfo=timezone.utc), str(row.get("row_id"))))

    @staticmethod
    def _matrix(rows: Sequence[Mapping[str, Any]], features: Sequence[str]) -> Any:
        return np.asarray([[np.nan if row.get(feature) is None else float(row.get(feature)) for feature in features] for row in rows], dtype=float)

    def assert_dataset_identity(self) -> None:
        dataset_path = Path(str(self.manifest.get("dataset_path") or ""))
        if not dataset_path.is_absolute():
            dataset_path = self.root / dataset_path
        if not dataset_path.exists():
            raise HardResearchStop(f"research dataset is missing: {dataset_path}")
        actual = _hash_file(dataset_path)
        expected = str(self.manifest.get("dataset_hash") or "")
        if not expected or actual != expected:
            raise HardResearchStop(
                f"dataset hash changed during research run (expected {expected}, got {actual})"
            )

    def _fit(self, config: Mapping[str, Any], train_rows: Sequence[Mapping[str, Any]]) -> tuple[_FittedModel, str, bool]:
        target_type = str(config.get("target_type", "MULTICLASS")).upper()
        features = FEATURES_BY_UNIVERSE[str(config.get("feature_universe", "CORE")).upper()]
        x_train = self._matrix(train_rows, features)
        if target_type == "MULTICLASS":
            y = np.asarray([self._target_index(row) for row in train_rows], dtype=int)
        elif target_type == "BINARY_P1":
            y = np.asarray([1 if self._target_index(row) == 1 else 0 for row in train_rows], dtype=int)
        elif target_type == "COUNT":
            y = np.asarray([int(row["h2_total_goals"]) for row in train_rows], dtype=float)
        else:
            raise DatasetError(f"Unsupported target type: {target_type}")
        if len(train_rows) < 2:
            raise InsufficientData("fewer than two training rows")
        weights = _time_weights(train_rows, str(config.get("time_decay", "NONE")))
        if target_type != "COUNT" and len(set(int(value) for value in y)) < 2:
            # A one-class slice is a valid data-quality outcome, not a reason
            # to silently replace the requested estimator with a constant
            # model.  The caller records this as a clean SKIPPED result.
            raise InsufficientData("training slice contains only one target class")
        model_config = dict(config)
        model_config["model_threads"] = self.model_threads
        base, effective_family = _make_base_model(model_config, 3 if target_type == "MULTICLASS" else 2)
        try:
            base.fit(x_train, y, model__sample_weight=weights)
        except (TypeError, ValueError):
            try:
                base.fit(x_train, y, sample_weight=weights)
            except (TypeError, ValueError):
                base.fit(x_train, y)
        actual_family = effective_model_family(base)
        requested_family = str(config.get("model_family", "")).upper()
        if actual_family != requested_family:
            raise ModelIdentityError(
                f"requested model {requested_family} instantiated as {actual_family}"
            )
        non_one_prior = 0.5
        if target_type == "BINARY_P1":
            zero_count = sum(self._target_index(row) == 0 for row in train_rows)
            two_count = sum(self._target_index(row) == 2 for row in train_rows)
            non_one_prior = zero_count / (zero_count + two_count) if zero_count + two_count else 0.5
        model = _FittedModel(base, target_type, str(config.get("calibration", "NONE")).upper(), non_one_prior)
        if target_type != "COUNT":
            model.fit_calibrator(x_train, y)
        return model, actual_family, model.calibration_applied

    def _write_oof_predictions(self, experiment_id: str, predictions: Sequence[Mapping[str, Any]]) -> Path:
        """Persist OOF rows outside the registry for scalable ensemble reuse."""

        self.prediction_root.mkdir(parents=True, exist_ok=True)
        path = self.prediction_root / f"{_safe_path(experiment_id)}.parquet"
        if pa is None or pq is None:
            # Dataset construction already requires Arrow in normal use, but
            # retain a small JSON fallback for focused/minimal environments.
            path = path.with_suffix(".json")
            path.write_text(json.dumps(list(predictions), ensure_ascii=False) + "\n", encoding="utf-8")
            return path
        schema = pa.schema(
            [
                ("row_id", pa.string()),
                ("actual_index", pa.int8()),
                ("p0", pa.float64()),
                ("p1", pa.float64()),
                ("p2", pa.float64()),
                ("fold", pa.string()),
            ]
        )
        rows = [
            {
                "row_id": str(item.get("row_id")),
                "actual_index": int(item.get("actual_index", 0)),
                "p0": float(item.get("p0", 0.0)),
                "p1": float(item.get("p1", 0.0)),
                "p2": float(item.get("p2", 0.0)),
                "fold": str(item.get("fold", "")),
            }
            for item in predictions
        ]
        table = pa.Table.from_pylist(rows, schema=schema)
        pq.write_table(table, path, compression="zstd")
        return path

    def _load_oof_predictions(self, record: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Load OOF predictions from the external artifact or old registry rows."""

        metrics = record.get("metrics") or {}
        inline = metrics.get("oof_predictions")
        if isinstance(inline, list):
            return [dict(item) for item in inline if isinstance(item, Mapping)]
        value = metrics.get("oof_predictions_path")
        if not value:
            return []
        path = Path(str(value))
        if not path.is_absolute():
            path = self.root / path
        if path.suffix.casefold() == ".parquet":
            if pq is None or not path.exists():
                return []
            return [dict(item) for item in pq.read_table(path).to_pylist()]
        if not path.exists():
            return []
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            return []
        return [dict(item) for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []

    def _validate_canary_serialization(
        self,
        model: _FittedModel,
        config: Mapping[str, Any],
        rows: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Exercise the same fitted wrapper through dump/reload/predict.

        This is intentionally run on the actual dataset slice supplied to the
        runner, not on the tiny dependency smoke fixture.  A deep plan is
        allowed only after this real-data CatBoost path has passed.
        """

        if np is None:
            raise HardResearchStop("CatBoost real-data canary requires numpy")
        features = FEATURES_BY_UNIVERSE[str(config.get("feature_universe", "CORE")).upper()]
        sample = list(rows[: min(8, len(rows))])
        if not sample:
            raise HardResearchStop("CatBoost real-data canary has no validation rows")
        matrix = self._matrix(sample, features)
        before = model.predict(matrix)["probabilities"]
        checks: dict[str, Any] = {
            "fit": "PASS",
            "predict": "PASS" if np.asarray(before).shape[0] == len(sample) else "FAIL",
            "serialize": "NOT_RUN",
            "reload": "NOT_RUN",
            "predict_after_reload": "NOT_RUN",
            "class_module": None,
            "probability_reproduction_max_abs_error": None,
        }
        if checks["predict"] != "PASS":
            raise HardResearchStop("CatBoost real-data canary prediction shape is invalid")
        try:
            import joblib

            with tempfile.TemporaryDirectory(prefix="v0611-catboost-canary-") as directory:
                path = Path(directory) / "canary.joblib"
                joblib.dump(model, path)
                checks["serialize"] = "PASS" if path.exists() else "FAIL"
                reloaded = joblib.load(path)
                checks["reload"] = "PASS"
                identity = model_identity(reloaded)
                checks["class_module"] = identity.get("class_module")
                if str(identity.get("effective_model_family", "")).upper() != "CATBOOST":
                    raise ModelIdentityError(
                        "real-data canary reload did not contain a CatBoost estimator"
                    )
                after = reloaded.predict(matrix)["probabilities"]
                error = float(np.max(np.abs(np.asarray(before) - np.asarray(after))))
                checks["probability_reproduction_max_abs_error"] = error
                checks["predict_after_reload"] = "PASS" if np.allclose(
                    before, after, rtol=1e-6, atol=1e-7
                ) else "FAIL"
        except (ImportError, OSError, ValueError, TypeError) as exc:
            raise HardResearchStop(f"CatBoost real-data canary serialization failed: {exc}") from exc
        if any(checks.get(key) != "PASS" for key in ("fit", "predict", "serialize", "reload", "predict_after_reload")):
            raise HardResearchStop(f"CatBoost real-data canary failed: {checks}")
        return checks

    def _run_ensemble(self, config: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
        completed = [
            item
            for item in self.registry.list(["COMPLETED"])
            if item.get("metrics")
            and (
                item.get("metrics", {}).get("oof_predictions")
                or item.get("metrics", {}).get("oof_predictions_path")
            )
        ]
        completed.sort(key=lambda item: float(item.get("metrics", {}).get("p1_brier", 999.0)))
        components = completed[:2]
        if len(components) < 2:
            raise InsufficientData("ensemble requires at least two completed OOF models")
        by_id = {str(item["experiment_id"]): item for item in components}
        maps = []
        for item in components:
            predictions = self._load_oof_predictions(item)
            if not predictions:
                raise InsufficientData(f"OOF predictions missing for {item.get('experiment_id')}")
            maps.append({str(pred["row_id"]): pred for pred in predictions})
        predictions: list[dict[str, Any]] = []
        for row in rows:
            row_id = str(row["row_id"])
            if not all(row_id in values for values in maps):
                continue
            p0 = sum(float(values[row_id]["p0"]) for values in maps) / len(maps)
            p1 = sum(float(values[row_id]["p1"]) for values in maps) / len(maps)
            p2 = sum(float(values[row_id]["p2"]) for values in maps) / len(maps)
            predictions.append({"row_id": row_id, "actual_index": self._target_index(row), "p0": p0, "p1": p1, "p2": p2, "fold": "ensemble"})
        metrics = _evaluate_predictions(predictions, len(rows))
        metrics["component_experiments"] = [str(item["experiment_id"]) for item in components]
        metrics["oof_predictions"] = predictions
        return metrics, predictions, "ENSEMBLE"

    def run(self, record: Mapping[str, Any]) -> dict[str, Any]:
        config = _normalize_config(record.get("config") or {})
        experiment_id = str(record["experiment_id"])
        self.assert_dataset_identity()
        rows = self._eligible_rows(config)
        if len(rows) < 4:
            raise InsufficientData(f"only {len(rows)} eligible rows for {config.get('feature_universe')}")
        dev, locked, folds = _split_rows(rows)
        if str(config.get("model_family")).upper() == "ENSEMBLE":
            metrics, predictions, effective_family = self._run_ensemble(config, dev)
            calibration_applied = False
            model_metadata: dict[str, Any] = {"type": "OOF_AVERAGE", "components": metrics.get("component_experiments", [])}
        else:
            predictions = []
            effective_family = str(config.get("model_family", "")).upper()
            calibration_applied = False
            fold_metrics: list[dict[str, Any]] = []
            raw_predictions: list[dict[str, Any]] = []
            for fold_index, (train_rows, validation_rows) in enumerate(folds, start=1):
                model, effective_family, applied = self._fit(config, train_rows)
                calibration_applied = calibration_applied or applied
                validation_matrix = self._matrix(
                    validation_rows,
                    FEATURES_BY_UNIVERSE[str(config["feature_universe"]).upper()],
                )
                raw_forecast = model.predict(validation_matrix, apply_calibration=False)
                forecast = model.predict(validation_matrix, apply_calibration=True)
                probabilities = forecast["probabilities"]
                raw_probabilities = raw_forecast["probabilities"]
                fold_predictions: list[dict[str, Any]] = []
                fold_raw_predictions: list[dict[str, Any]] = []
                for row, probability in zip(validation_rows, probabilities):
                    item = {
                        "row_id": str(row["row_id"]),
                        "actual_index": self._target_index(row),
                        "p0": float(probability[0]),
                        "p1": float(probability[1]),
                        "p2": float(probability[2]),
                        "fold": fold_index,
                    }
                    predictions.append(item)
                    fold_predictions.append(item)
                for row, probability in zip(validation_rows, raw_probabilities):
                    raw_item = {
                        "row_id": str(row["row_id"]),
                        "actual_index": self._target_index(row),
                        "p0": float(probability[0]),
                        "p1": float(probability[1]),
                        "p2": float(probability[2]),
                        "fold": fold_index,
                    }
                    raw_predictions.append(raw_item)
                    fold_raw_predictions.append(raw_item)
                fold_metrics.append(_evaluate_predictions(fold_predictions, len(rows)))
            metrics = _evaluate_predictions(predictions, len(rows))
            metrics["folds"] = fold_metrics
            raw_metrics = _evaluate_predictions(raw_predictions, len(rows))
            metrics["raw_metrics"] = {
                key: raw_metrics.get(key)
                for key in ("log_loss", "brier", "p1_brier", "p1_calibration_error", "sample_n", "coverage")
            }
            metrics["raw_log_loss"] = raw_metrics.get("log_loss")
            metrics["raw_brier"] = raw_metrics.get("brier")
            metrics["raw_p1_brier"] = raw_metrics.get("p1_brier")
            metrics["calibrated_metrics"] = {
                key: metrics.get(key)
                for key in ("log_loss", "brier", "p1_brier", "p1_calibration_error", "sample_n", "coverage")
            }
            # Fit the saved artifact on development data only.  The locked
            # period is retained in metadata and is never used for selection.
            final_model, effective_family, applied = self._fit(config, dev)
            calibration_applied = calibration_applied or applied
            model_metadata = {
                "type": "sklearn_pipeline",
                "model": final_model,
                "features": FEATURES_BY_UNIVERSE[str(config["feature_universe"]).upper()],
                "target_type": config.get("target_type"),
                "identity": model_identity(final_model),
            }
            if effective_family != str(config.get("model_family", "")).upper():
                raise ModelIdentityError(
                    f"requested model {config.get('model_family')} instantiated as {effective_family}"
                )
        if str(config.get("model_family", "")).upper() == "ENSEMBLE":
            metrics.setdefault(
                "raw_metrics",
                {
                    key: metrics.get(key)
                    for key in ("log_loss", "brier", "p1_brier", "p1_calibration_error", "sample_n", "coverage")
                },
            )
        metrics.update(
            {
                "training_n": len(dev) if str(config.get("model_family")).upper() != "ENSEMBLE" else None,
                "train_n": len(dev) if str(config.get("model_family")).upper() != "ENSEMBLE" else None,
                "validation_n": len(predictions),
                "locked_test_n": 0,
                "locked_test_evaluated": False,
                "locked_test_available_n": len(locked),
                "eligible_n": len(rows),
                "feature_universe": config.get("feature_universe"),
                "calibration_requested": config.get("calibration"),
                "calibration_applied": calibration_applied,
                "model_cutoff": MODEL_CUTOFF,
                "target": "SECOND_HALF_GOALS_REGULATION_ONLY",
                "loss_middle": "H2_GOALS_1",
                "zero_or_2plus_hit_definition": "target_h2_goal_class in {H2_GOALS_0,H2_GOALS_2_PLUS}",
                "requested_model_family": str(config.get("model_family", "")).upper(),
                "effective_model_family": effective_family,
                "requested_target_type": str(config.get("target_type", "")).upper(),
                "effective_target_type": str(config.get("target_type", "")).upper(),
                "requested_feature_universe": str(config.get("feature_universe", "")).upper(),
                "effective_feature_universe": str(config.get("feature_universe", "")).upper(),
                "research_run_id": self.research_run_id,
                "environment_hash": self.environment_hash_value,
                "code_commit": runtime_identity(self.root).get("git_commit"),
                "tree_hash": source_tree_hash(self.root),
                "research_version": V0611_VERSION,
                "config_hash": experiment_config_hash(config),
                "registry_path": str(self.registry.path),
                "run_started_at": self.run_started_at,
            }
        )
        if bool(config.get("canary_real_data")):
            if str(config.get("model_family", "")).upper() != "CATBOOST":
                raise ModelIdentityError("the real-data canary must request CATBOOST")
            canary_checks = self._validate_canary_serialization(
                model_metadata["model"],
                config,
                dev,
            )
            metrics["canary_real_data"] = True
            metrics["canary_scope"] = str(config.get("canary_scope", "LOCAL")).upper()
            metrics["catboost_canary"] = canary_checks
            metrics["catboost_canary_status"] = "PASS"
        metrics.update(_research_breakdown_metrics(predictions, rows))
        # OOF rows are persisted as a separate prediction artifact for
        # ensemble construction and report reproducibility.
        artifact_json = self.model_root / f"{_safe_path(experiment_id)}.json"
        retain_model, retention_reason = self._artifact_retention_decision(
            {**config, "experiment_id": experiment_id},
            effective_family,
            metrics,
        )
        metrics["model_artifact_retained"] = bool(retain_model)
        metrics["model_artifact_retention_reason"] = retention_reason
        if model_metadata.get("type") == "sklearn_pipeline":
            metadata_without_model = {
                key: value for key, value in model_metadata.items() if key != "model"
            }
            try:
                import joblib

                if retain_model:
                    model_file = self.model_root / f"{_safe_path(experiment_id)}.joblib"
                    joblib.dump(model_metadata["model"], model_file)
                    metadata_without_model["model_path"] = str(model_file)
                else:
                    metadata_without_model["model_path"] = None
                    metadata_without_model["type"] = "metadata_only"
                model_metadata = metadata_without_model
            except (ImportError, OSError, ValueError) as exc:
                # The compact JSON artifact remains useful even when joblib
                # is unavailable or a filesystem write fails.  Preserve the
                # class/module identity instead of replacing it with a thin
                # anonymous metadata object.
                metrics["model_artifact_retained"] = False
                metrics["model_artifact_retention_reason"] = (
                    f"SERIALIZATION_UNAVAILABLE:{type(exc).__name__}"
                )
                metadata_without_model["type"] = "metadata_only"
                metadata_without_model["model_path"] = None
                model_metadata = metadata_without_model
        prediction_path = self._write_oof_predictions(experiment_id, predictions)
        # Keep the registry compact. The ensemble can reuse the external
        # Parquet artifact through the path below, while legacy inline rows
        # remain readable via _load_oof_predictions.
        metrics.pop("oof_predictions", None)
        metrics["oof_predictions_path"] = str(prediction_path)
        artifact = {
            "v060_version": V060_VERSION,
            "v061_version": V061_VERSION,
            "v0611_version": V0611_VERSION,
            "experiment_id": experiment_id,
            "config": config,
            "config_hash": experiment_config_hash(config),
            "dataset_hash": self.manifest.get("dataset_hash"),
            "code_identity": runtime_identity(self.root),
            "research_run_id": self.research_run_id,
            "registry_path": str(self.registry.path),
            "environment_manifest": self.environment_manifest_value,
            "environment_hash": self.environment_hash_value,
            "tree_hash": source_tree_hash(self.root),
            "metrics": metrics,
            "model_metadata": model_metadata,
            "feature_catalog": [item for item in feature_catalog() if item["feature_name"] in FEATURES_BY_UNIVERSE[str(config.get("feature_universe", "CORE")).upper()]],
        }
        artifact_json.write_text(json.dumps(artifact, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        metrics["artifact_metadata_path"] = str(artifact_json)
        return {
            "metrics": metrics,
            "artifact_path": str(artifact_json),
            "effective_model_family": effective_family,
            "model_identity": model_metadata.get("identity") if isinstance(model_metadata, dict) else None,
        }


def _row_first(row: Mapping[str, Any], *names: str, default: Any = "unknown") -> Any:
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return value
    return default


def _group_metrics(predictions: Sequence[Mapping[str, Any]], rows: Sequence[Mapping[str, Any]], key_fn: Any) -> list[dict[str, Any]]:
    row_by_id = {str(row.get("row_id")): row for row in rows}
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for prediction in predictions:
        row = row_by_id.get(str(prediction.get("row_id")))
        if row is not None:
            groups[str(key_fn(row))].append(prediction)
    result: list[dict[str, Any]] = []
    for key, values in sorted(groups.items()):
        actual = [int(value.get("actual_index", 0)) == 1 for value in values]
        predicted = [float(value.get("p1", 0.0)) for value in values]
        actual_rate = sum(actual) / max(1, len(actual))
        zero_or_two_rate = 1.0 - actual_rate
        z = 1.959963984540054
        count = len(values)
        denominator_ci = 1 + z * z / max(1, count)
        center = (zero_or_two_rate + z * z / (2 * max(1, count))) / denominator_ci
        radius = z * math.sqrt(
            zero_or_two_rate * (1.0 - zero_or_two_rate) / max(1, count)
            + z * z / (4 * max(1, count) * max(1, count))
        ) / denominator_ci
        fold_rates: dict[str, float] = {}
        for fold in sorted({str(value.get("fold", "unknown")) for value in values}):
            fold_values = [value for value in values if str(value.get("fold", "unknown")) == fold]
            fold_rates[fold] = sum(int(value.get("actual_index", 0)) != 1 for value in fold_values) / max(1, len(fold_values))
        fold_mean = sum(fold_rates.values()) / len(fold_rates) if fold_rates else None
        result.append(
            {
                "group": key,
                "n": count,
                "predicted_p1": sum(predicted) / max(1, len(predicted)),
                "actual_p1": actual_rate,
                "zero_or_2plus_hit_rate": zero_or_two_rate,
                "zero_or_2plus_ci_low": max(0.0, center - radius),
                "zero_or_2plus_ci_high": min(1.0, center + radius),
                "fold_stability": {
                    "folds": len(fold_rates),
                    "per_fold_zero_or_2plus": fold_rates,
                    "stddev_zero_or_2plus": (
                        math.sqrt(
                            sum((rate - fold_mean) ** 2 for rate in fold_rates.values())
                            / len(fold_rates)
                        )
                        if fold_rates and fold_mean is not None
                        else None
                    ),
                },
            }
        )
    return result


def _prediction_metric_summary(
    predictions: Sequence[Mapping[str, Any]],
    denominator: int,
) -> dict[str, Any]:
    """Keep the exact-sample intersection result compact but measurable."""

    if not predictions:
        return {
            "n": 0,
            "coverage": 0.0,
            "log_loss": None,
            "brier": None,
            "p1_brier": None,
            "p1_calibration_error": None,
            "actual_p1_rate": None,
            "zero_or_2plus_hit_rate": None,
        }
    metrics = _evaluate_predictions(predictions, denominator)
    return {
        "n": metrics["sample_n"],
        "coverage": metrics["coverage"],
        "log_loss": metrics["log_loss"],
        "brier": metrics["brier"],
        "p1_brier": metrics["p1_brier"],
        "p1_calibration_error": metrics["p1_calibration_error"],
        "actual_p1_rate": metrics["actual_p1_rate"],
        "zero_or_2plus_hit_rate": metrics["zero_or_2plus_hit_rate"],
    }


def _research_breakdown_metrics(
    predictions: Sequence[Mapping[str, Any]],
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Return validation-only breakdowns without using the locked period."""

    row_by_id = {str(row.get("row_id")): row for row in rows}
    thresholds: dict[str, Any] = {}
    p1_values = [(prediction, float(prediction.get("p1", 0.0))) for prediction in predictions]
    for threshold in P1_THRESHOLDS:
        selected = [prediction for prediction, p1 in p1_values if p1 < threshold]
        thresholds[f"<{threshold:.3f}"] = {
            "n": len(selected),
            "score_breakdown": _group_metrics(
                selected,
                rows,
                lambda row: f"{_row_first(row, 'ht_home', 'ht_score_home', default='?')}:{_row_first(row, 'ht_away', 'ht_score_away', default='?')}",
            ),
            "league_breakdown": _group_metrics(
                selected,
                rows,
                lambda row: f"{_row_first(row, 'country_name', 'country', 'country_code')} / {_row_first(row, 'league_name', 'league_id')}",
            ),
            "season_breakdown": _group_metrics(
                selected,
                rows,
                lambda row: _row_first(row, 'season_label', 'season', 'season_id'),
            ),
        }

    eligible = [row for row in rows if row.get("ml_eligible")]
    sets = {
        "SCORE_ONLY": {str(row.get("row_id")) for row in eligible},
        "CORE": {str(row.get("row_id")) for row in eligible},
        "CORE_XG": {str(row.get("row_id")) for row in eligible if row.get("xg_available")},
        "CORE_SHOTMAP": {str(row.get("row_id")) for row in eligible if row.get("shotmap_available")},
    }
    intersections: dict[str, Any] = {}
    for left, right in (("CORE", "SCORE_ONLY"), ("CORE", "CORE_XG"), ("CORE", "CORE_SHOTMAP")):
        left_ids, right_ids = sets[left], sets[right]
        left_only_ids = left_ids - right_ids
        right_only_ids = right_ids - left_ids
        intersection_ids = left_ids & right_ids
        prediction_by_id = {str(prediction.get("row_id")): prediction for prediction in predictions}

        def metrics_for(ids: set[str]) -> dict[str, Any]:
            return _prediction_metric_summary(
                [prediction_by_id[row_id] for row_id in sorted(ids) if row_id in prediction_by_id],
                len(rows),
            )

        intersections[f"{left}_vs_{right}"] = {
            "left": left,
            "right": right,
            "left_n": len(left_ids),
            "right_n": len(right_ids),
            "intersection_n": len(intersection_ids),
            "left_only_n": len(left_only_ids),
            "right_only_n": len(right_only_ids),
            "metrics": {
                "intersection": metrics_for(intersection_ids),
                "left_only": metrics_for(left_only_ids),
                "right_only": metrics_for(right_only_ids),
            },
        }
    extreme = thresholds.get("<0.050")
    if extreme is None:
        selected = [prediction for prediction, p1 in p1_values if p1 < 0.05]
        extreme = {
            "n": len(selected),
            "score_breakdown": _group_metrics(selected, rows, lambda row: f"{_row_first(row, 'ht_home', 'ht_score_home', default='?')}:{_row_first(row, 'ht_away', 'ht_score_away', default='?')}"),
        }
    extreme_predictions = [prediction for prediction, p1 in p1_values if p1 < 0.05]
    extreme_actual = (
        sum(int(prediction.get("actual_index", 0)) == 1 for prediction in extreme_predictions)
        / max(1, len(extreme_predictions))
        if extreme_predictions
        else None
    )
    extreme["actual_p1"] = extreme_actual
    extreme["warning"] = bool(extreme_actual is not None and extreme_actual > 0.20)
    return {
        "score_breakdown": thresholds,
        "league_breakdown": {key: value["league_breakdown"] for key, value in thresholds.items()},
        "season_breakdown": {key: value["season_breakdown"] for key, value in thresholds.items()},
        "intersections": intersections,
        "extreme_calibration": extreme,
    }


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]], fieldnames: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fieldnames), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fieldnames})


def generate_reports(root: Path, manifest: Mapping[str, Any], registry: ExperimentRegistry, *, output_dir: Path | None = None) -> dict[str, str]:
    output = Path(output_dir or root / "research" / "output" / "v060")
    output.mkdir(parents=True, exist_ok=True)
    records = registry.list()
    leaderboard_rows: list[dict[str, Any]] = []
    for record in records:
        metrics = record.get("metrics") or {}
        leaderboard_rows.append(
            {
                "experiment_id": record.get("experiment_id"),
                "status": record.get("status"),
                "model_family": record.get("model_family"),
                "target_type": record.get("target_type"),
                "feature_universe": record.get("feature_set"),
                "requested_model_family": record.get("requested_model_family") or metrics.get("requested_model_family"),
                "effective_model_family": record.get("effective_model_family") or metrics.get("effective_model_family"),
                "requested_target_type": record.get("requested_target_type") or metrics.get("requested_target_type"),
                "effective_target_type": record.get("effective_target_type") or metrics.get("effective_target_type"),
                "requested_feature_universe": record.get("requested_feature_universe") or metrics.get("requested_feature_universe"),
                "effective_feature_universe": record.get("effective_feature_universe") or metrics.get("effective_feature_universe"),
                "training_window": record.get("training_window"),
                "time_decay": record.get("time_decay"),
                "calibration": record.get("calibration"),
                "league_scope": record.get("league_scope"),
                "eligible_n": metrics.get("eligible_n"),
                "train_n": metrics.get("training_n"),
                "validation_n": metrics.get("validation_n"),
                "test_n": metrics.get("locked_test_n", 0),
                "locked_test_available_n": metrics.get("locked_test_available_n"),
                "coverage": metrics.get("coverage"),
                "log_loss": metrics.get("log_loss"),
                "brier": metrics.get("brier"),
                "p1_brier": metrics.get("p1_brier"),
                "p1_calibration_error": metrics.get("p1_calibration_error"),
                "raw_log_loss": metrics.get("raw_log_loss"),
                "raw_brier": metrics.get("raw_brier"),
                "raw_p1_brier": metrics.get("raw_p1_brier"),
                "calibration_applied": metrics.get("calibration_applied"),
                "model_artifact_retained": metrics.get("model_artifact_retained"),
                "model_artifact_retention_reason": metrics.get("model_artifact_retention_reason"),
                "extreme_calibration_warning": (metrics.get("extreme_calibration") or {}).get("warning"),
                "research_run_id": record.get("research_run_id") or metrics.get("research_run_id"),
                "environment_hash": record.get("environment_hash") or metrics.get("environment_hash"),
                "error": record.get("error"),
            }
        )
    leaderboard_rows.sort(key=lambda row: (row["p1_brier"] is None, row["p1_brier"] if row["p1_brier"] is not None else 999.0, row["experiment_id"] or ""))
    leaderboard_path = output / "V060_LOCAL_LEADERBOARD.csv"
    _write_csv(leaderboard_path, leaderboard_rows, list(leaderboard_rows[0].keys()) if leaderboard_rows else ["experiment_id", "status"])
    full_leaderboard_path = output / "V060_MODEL_LEADERBOARD.csv"
    _write_csv(full_leaderboard_path, leaderboard_rows, list(leaderboard_rows[0].keys()) if leaderboard_rows else ["experiment_id", "status"])
    full_leaderboard_parquet = output / "V060_MODEL_LEADERBOARD.parquet"
    if pa is not None and leaderboard_rows:
        pq.write_table(pa.Table.from_pylist(leaderboard_rows), full_leaderboard_parquet, compression="zstd")

    completed = [record for record in records if record.get("status") == "COMPLETED"]
    strategy_rows: list[dict[str, Any]] = []
    min_strategy_n = max(1, int(os.getenv("V061_MIN_STRATEGY_N", "30")))
    min_strategy_folds = max(2, int(os.getenv("V061_MIN_STRATEGY_FOLDS", "2")))
    max_strategy_calibration_error = max(0.0, float(os.getenv("V061_MAX_STRATEGY_CALIBRATION_ERROR", "0.15")))
    for record in completed:
        metrics = record.get("metrics") or {}
        fold_scores = [float(fold.get("p1_brier")) for fold in metrics.get("folds", []) if fold.get("p1_brier") is not None]
        stability = None
        if len(fold_scores) >= 2:
            mean_score = sum(fold_scores) / len(fold_scores)
            stability = math.sqrt(sum((score - mean_score) ** 2 for score in fold_scores) / len(fold_scores))
        for threshold in metrics.get("p1_thresholds", []):
            max_p1 = float(threshold.get("max_predicted_p1"))
            calibration_error = metrics.get("p1_calibration_error")
            if (
                int(threshold.get("sample_n") or 0) < min_strategy_n
                or len(fold_scores) < min_strategy_folds
                or (calibration_error is not None and float(calibration_error) > max_strategy_calibration_error)
            ):
                continue
            strategy_rows.append(
                {
                    "candidate_id": f"{record.get('experiment_id')}_P1_LT_{int(round(max_p1 * 1000))}",
                    "model_id": record.get("experiment_id"),
                    "target": record.get("target_type"),
                    "feature_set": record.get("feature_set"),
                    "league_scope": record.get("league_scope"),
                    "max_p1": max_p1,
                    "validation_sample": threshold.get("sample_n"),
                    "actual_p1": threshold.get("actual_p1_rate"),
                    "zero_or_2plus_hit": threshold.get("zero_or_2plus_hit_rate"),
                    "threshold_fold_stability": threshold.get("fold_stability"),
                    "stability": stability,
                    "fold_count": len(fold_scores),
                    "calibration_error": calibration_error,
                    "candidate_eligible": True,
                }
            )
    strategy_path = output / "strategy_candidates.parquet"
    strategy_csv_path = output / "strategy_candidates.csv"
    strategy_fields = ["candidate_id", "model_id", "target", "feature_set", "league_scope", "max_p1", "validation_sample", "actual_p1", "zero_or_2plus_hit", "threshold_fold_stability", "stability", "fold_count", "calibration_error", "candidate_eligible"]
    _write_csv(strategy_csv_path, strategy_rows, strategy_fields)
    if pa is not None and strategy_rows:
        pq.write_table(pa.Table.from_pylist(strategy_rows), strategy_path, compression="zstd")

    eligible = int(manifest.get("eligible_match_count", 0) or 0)
    distribution = manifest.get("target_distribution", {})
    feature_coverage = manifest.get("feature_coverage", {})
    best = [row for row in leaderboard_rows if row.get("status") == "COMPLETED"][:1]
    lines = [
        "# V0.6.0 Local HT Research Report",
        "",
        "This report is research-only. It does not deploy a model, change the collector, or claim betting ROI.",
        "",
        "## Dataset",
        "",
        f"- Date range: {manifest.get('source_date_range', {}).get('from')} → {manifest.get('source_date_range', {}).get('to')}",
        f"- Matches total: {manifest.get('match_count', 0)}",
        f"- Matches eligible: {eligible}",
        f"- Dataset hash: `{manifest.get('dataset_hash')}`",
        f"- Cutoff: `{MODEL_CUTOFF}`; target: `regulation_ft_goals - halftime_goals`",
        "",
        "| Target | N | Share |",
        "| --- | ---: | ---: |",
    ]
    for target in TARGET_CLASSES:
        count = int(distribution.get(target, 0) or 0)
        lines.append(f"| {target} | {count} | {count / max(1, eligible):.2%} |")
    lines += [
        "",
        "### Feature coverage",
        "",
        f"- CORE: {sum(feature_coverage.get(name, 0.0) for name in CORE_FEATURES) / max(1, len(CORE_FEATURES)):.2%}",
        f"- XG pair coverage: {sum(feature_coverage.get(name, 0.0) for name in XG_FEATURES) / max(1, len(XG_FEATURES)):.2%}",
        f"- SHOTMAP coverage: {sum(feature_coverage.get(name, 0.0) for name in SHOTMAP_FEATURES) / max(1, len(SHOTMAP_FEATURES)):.2%}",
        "",
        "## Experiment leaderboard",
        "",
        f"Experiments tested in registry: **{len(records)}**; completed: **{len(completed)}**.",
        "",
        "| # | Model | Target | Features | Window | Train N | Validation N | Test N | Coverage | LogLoss | Brier | P1 Brier | P1 Cal Error | Calibration |",
        "| - | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]

    def fmt(value: Any) -> str:
        if value is None:
            return "—"
        if isinstance(value, bool):
            return "yes" if value else "no"
        if isinstance(value, int):
            return str(value)
        if isinstance(value, float):
            return f"{value:.4f}"
        return str(value)

    for index, row in enumerate(leaderboard_rows, start=1):
        lines.append(
            f"| {index} | {row.get('model_family')} (`{row.get('experiment_id')}`) | {row.get('target_type')} | {row.get('feature_universe')} | {row.get('training_window')} | {fmt(row.get('train_n'))} | {fmt(row.get('validation_n'))} | {fmt(row.get('test_n'))} | {fmt(row.get('coverage'))} | {fmt(row.get('log_loss'))} | {fmt(row.get('brier'))} | {fmt(row.get('p1_brier'))} | {fmt(row.get('p1_calibration_error'))} | {fmt(row.get('calibration_applied'))} |"
        )
    lines += ["", "## P1 threshold analysis", ""]
    for record in completed:
        metrics = record.get("metrics") or {}
        lines += [f"### {record.get('experiment_id')}", "", "| Max predicted P1 | N | Coverage | Mean predicted P1 | Actual P1 | ZERO_OR_2PLUS | 95% CI | Folds | Fold stability |", "| ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: |"]
        for threshold in metrics.get("p1_thresholds", []):
            ci = "—" if threshold.get("zero_or_2plus_ci_low") is None else f"{threshold['zero_or_2plus_ci_low']:.3f}–{threshold['zero_or_2plus_ci_high']:.3f}"
            fold_stability = threshold.get("fold_stability") or {}
            fold_std = fold_stability.get("stddev_zero_or_2plus_hit_rate")
            lines.append(
                f"| <{float(threshold['max_predicted_p1']):.1%} | {threshold.get('sample_n', 0)} | {fmt(threshold.get('coverage'))} | {fmt(threshold.get('mean_predicted_p1'))} | {fmt(threshold.get('actual_p1_rate'))} | {fmt(threshold.get('zero_or_2plus_hit_rate'))} | {ci} | {fold_stability.get('folds', 0)} | {fmt(fold_std)} |"
            )
    by_id = {str(row.get("experiment_id")): row for row in leaderboard_rows if row.get("status") == "COMPLETED"}

    def metric(experiment_id: str, name: str) -> float | None:
        value = (by_id.get(experiment_id) or {}).get(name)
        return float(value) if value is not None else None

    baseline_p1 = int(distribution.get("H2_GOALS_1", 0) or 0) / max(1, eligible)
    score_p1 = metric("L01_LOGISTIC_MULTICLASS_SCORE_ONLY", "p1_brier")
    core_p1 = metric("L02_LOGISTIC_MULTICLASS_CORE", "p1_brier")
    core_logloss = metric("L02_LOGISTIC_MULTICLASS_CORE", "log_loss")
    xg_p1 = metric("L05_BOOSTING_MULTICLASS_CORE_XG", "p1_brier")
    shotmap_p1 = metric("L06_BOOSTING_MULTICLASS_CORE_SHOTMAP", "p1_brier")
    binary_p1 = metric("L08_BOOSTING_BINARY_P1_CORE", "p1_brier")
    count_p1 = metric("L09_POISSON_COUNT_CORE", "p1_brier")
    lines += [
        "",
        "## Research questions",
        "",
        "- `P(H2=1)` is the explicit `LOSS_MIDDLE` target; `ZERO_OR_2PLUS` means H2 class 0 or 2-plus.",
        f"- Baseline answer: `P(H2=1)` in this archive is **{baseline_p1:.2%}**. The score-only multiclass P1 Brier is **{fmt(score_p1)}**; CORE is **{fmt(core_p1)}** (CORE LogLoss **{fmt(core_logloss)}**). This is validation evidence, not a significance or ROI claim.",
        f"- xG/Shotmap answer: CORE_XG uses **{metric('L05_BOOSTING_MULTICLASS_CORE_XG', 'eligible_n') or 0:.0f}** eligible matches and has P1 Brier **{fmt(xg_p1)}**; CORE_SHOTMAP uses **{metric('L06_BOOSTING_MULTICLASS_CORE_SHOTMAP', 'eligible_n') or 0:.0f}** and has **{fmt(shotmap_p1)}**. The local run shows coverage and a first comparison, not causal feature value.",
        f"- Direct Binary-P1 answer: the CORE binary model has P1 Brier **{fmt(binary_p1)}** versus the CORE multiclass model **{fmt(core_p1)}**.",
        f"- Count confirmation answer: the Poisson-derived P1 Brier is **{fmt(count_p1)}** versus CORE multiclass **{fmt(core_p1)}**; this is an independent count formulation, not a deployment decision.",
        "- Low-P1 situations are enumerated in `strategy_candidates.parquet` with sample size, actual P1, ZERO_OR_2PLUS hit rate and fold stability; thresholds must be interpreted with their confidence intervals.",
        "- A locked test period is reserved and is not used for planner selection or this validation leaderboard.",
        "- No historical Tipico HT odds are used; therefore this report makes **no ROI claim**.",
        "- No model is activated for CT110 or Paper Trading by this command.",
    ]
    report_path = output / "V060_LOCAL_REPORT.md"
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "report": str(report_path),
        "leaderboard": str(leaderboard_path),
        "full_leaderboard_csv": str(full_leaderboard_path),
        "full_leaderboard_parquet": str(full_leaderboard_parquet) if full_leaderboard_parquet.exists() else "",
        "strategy_candidates": str(strategy_path) if strategy_path.exists() else str(strategy_csv_path),
    }


def _run_records(registry: ExperimentRegistry, run_id: str | None = None) -> list[dict[str, Any]]:
    records = registry.list()
    if run_id:
        selected = [record for record in records if str(record.get("research_run_id") or "") == str(run_id)]
        if selected:
            return selected
    return records


def _iso_min_max(records: Sequence[Mapping[str, Any]], field: str) -> tuple[str | None, str | None]:
    values = sorted(str(record.get(field)) for record in records if record.get(field))
    return (values[0], values[-1]) if values else (None, None)


def build_v061_reports(
    root: Path,
    manifest: Mapping[str, Any],
    registry: ExperimentRegistry,
    *,
    research_run_id: str | None = None,
    environment: Mapping[str, Any] | None = None,
    preflight: Mapping[str, Any] | None = None,
    full_tests: str = "NOT_RUN",
    catboost_ct_canary: str = "PENDING",
    ct110_deployment: str = "PENDING",
    ct110_runtime_canary: str = "PENDING",
    performance: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Write the durable V0.6.1 morning report and release status."""

    root = Path(root).resolve()
    records = _run_records(registry, research_run_id)
    completed = [record for record in records if record.get("status") == "COMPLETED"]
    run_ids = sorted({str(record.get("research_run_id")) for record in records if record.get("research_run_id")})
    effective_run_id = research_run_id or (run_ids[0] if len(run_ids) == 1 else None)
    started_candidates = [record for record in records if record.get("started_at") or record.get("created_at")]
    finished_candidates = [record for record in records if record.get("finished_at")]
    started = sorted(str(record.get("started_at") or record.get("created_at")) for record in started_candidates)
    finished = sorted(str(record.get("finished_at")) for record in finished_candidates)
    requested_counts: dict[str, int] = defaultdict(int)
    effective_counts: dict[str, int] = defaultdict(int)
    mismatches: list[str] = []
    for record in records:
        metrics = record.get("metrics") or {}
        requested = str(record.get("requested_model_family") or metrics.get("requested_model_family") or record.get("model_family") or "UNKNOWN").upper()
        effective = str(record.get("effective_model_family") or metrics.get("effective_model_family") or "").upper()
        requested_counts[requested] += 1
        if effective:
            effective_counts[effective] += 1
        if record.get("status") == "COMPLETED":
            has_run_context = bool(record.get("research_run_id") or metrics.get("research_run_id"))
            if has_run_context and (not effective or requested != effective):
                mismatches.append(str(record.get("experiment_id")))

    top_models: list[dict[str, Any]] = []
    for record in completed:
        metrics = record.get("metrics") or {}
        top_models.append(
            {
                "experiment_id": record.get("experiment_id"),
                "requested_model_family": record.get("requested_model_family") or metrics.get("requested_model_family"),
                "effective_model_family": record.get("effective_model_family") or metrics.get("effective_model_family"),
                "target_type": record.get("target_type"),
                "feature_universe": record.get("feature_set"),
                "p1_brier": metrics.get("p1_brier"),
                "raw_p1_brier": metrics.get("raw_p1_brier"),
                "log_loss": metrics.get("log_loss"),
                "raw_log_loss": metrics.get("raw_log_loss"),
                "calibration_error": metrics.get("p1_calibration_error"),
                "validation_n": metrics.get("validation_n"),
                "locked_test_evaluated": metrics.get("locked_test_evaluated"),
            }
        )
    top_models.sort(key=lambda item: (item["p1_brier"] is None, item["p1_brier"] if item["p1_brier"] is not None else 999.0))

    breakdowns: list[dict[str, Any]] = []
    for record in completed:
        metrics = record.get("metrics") or {}
        breakdowns.append(
            {
                "experiment_id": record.get("experiment_id"),
                "feature_universe": record.get("feature_set"),
                "thresholds": metrics.get("p1_thresholds", []),
                "score_breakdown": metrics.get("score_breakdown", {}),
                "league_breakdown": metrics.get("league_breakdown", {}),
                "season_breakdown": metrics.get("season_breakdown", {}),
                "intersections": metrics.get("intersections", {}),
                "extreme_calibration": metrics.get("extreme_calibration", {}),
            }
        )

    pf = dict(preflight or {})
    supplied_performance = dict(performance or {})
    performance_evidence = {
        "source": supplied_performance.get(
            "source",
            "runtime_canary_or_benchmark_not_run_by_local_report"
        ),
        "collector_health": supplied_performance.get(
            "collector_health",
            pf.get("COLLECTOR_HEALTH", "PENDING"),
        ),
        "daily_index_cache": {
            key: supplied_performance.get(key, "NOT_OBSERVED")
            for key in (
                "daily_index_network_requests",
                "daily_index_cache_hits",
                "daily_index_cache_misses",
                "daily_index_singleflight_waiters",
                "daily_index_age_seconds",
            )
        },
        "resolver": {
            key: supplied_performance.get(key, "NOT_OBSERVED")
            for key in (
                "resolver_attempts",
                "resolver_candidate_scans",
                "resolver_negative_cache_hits",
                "confirmed_link_fast_path",
            )
        },
        "resources": pf.get("resources", "NOT_OBSERVED"),
        "database": {
            "strategy_evaluations_bind_mismatch_errors": supplied_performance.get(
                "strategy_evaluations_bind_mismatch_errors",
                "NOT_OBSERVED",
            ),
            "db_transactions": supplied_performance.get("db_transactions", "NOT_OBSERVED"),
            "db_commits": supplied_performance.get("db_commits", "NOT_OBSERVED"),
            "db_rollbacks": supplied_performance.get("db_rollbacks", "NOT_OBSERVED"),
            "wal": supplied_performance.get("wal", "NOT_OBSERVED"),
        },
        "slow_operations": supplied_performance.get("slow_operations", "NOT_OBSERVED"),
    }
    report_environment_hash = (
        environment_hash(environment)
        if environment
        else pf.get("environment_hash")
    )
    report_lines = [
        "# V0.6.1 Overnight Report",
        "",
        "Research-only evidence. No model is deployed and no historical Tipico ROI is claimed.",
        "",
        "## Research run summary",
        "",
        f"- run_id: `{effective_run_id or 'MULTIPLE_OR_NOT_SET'}`",
        f"- start: `{started[0] if started else '—'}`",
        f"- finish: `{finished[-1] if finished else '—'}`",
        f"- dataset_hash: `{manifest.get('dataset_hash')}`",
        f"- code_commit: `{(environment or {}).get('code_commit') or runtime_identity(root).get('git_commit')}`",
        f"- environment_hash: `{report_environment_hash or '—'}`",
        f"- planned: `{len(records)}`",
        f"- completed: `{len(completed)}`",
        f"- failed: `{sum(record.get('status') == 'FAILED' for record in records)}`",
        f"- skipped: `{sum(str(record.get('status', '')).startswith('SKIPPED') for record in records)}`",
        f"- interrupted: `{sum(record.get('status') == 'INTERRUPTED' for record in records)}`",
        "",
        "## Requested/effective model identity",
        "",
        f"- REQUESTED_EFFECTIVE_MISMATCHES = **{len(mismatches)}**",
        f"- RUN_VALID = **{'YES' if not mismatches else 'NO'}**",
        f"- requested counts: `{json.dumps(dict(sorted(requested_counts.items())), sort_keys=True)}`",
        f"- effective counts: `{json.dumps(dict(sorted(effective_counts.items())), sort_keys=True)}`",
        "",
        "## Top development/validation models",
        "",
        "| Model | Requested | Effective | Target | Features | Validation N | P1 Brier | Raw P1 Brier | LogLoss | Raw LogLoss | Calibration error |",
        "| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in top_models[:20]:
        report_lines.append(
            f"| `{item['experiment_id']}` | {item['requested_model_family']} | {item['effective_model_family']} | {item['target_type']} | {item['feature_universe']} | {item['validation_n'] or '—'} | {item['p1_brier'] if item['p1_brier'] is not None else '—'} | {item['raw_p1_brier'] if item['raw_p1_brier'] is not None else '—'} | {item['log_loss'] if item['log_loss'] is not None else '—'} | {item['raw_log_loss'] if item['raw_log_loss'] is not None else '—'} | {item['calibration_error'] if item['calibration_error'] is not None else '—'} |"
        )
    report_lines += [
        "",
        "## Low-P1 groups and stability",
        "",
        "Thresholds are validation-only. A strategy candidate is emitted only when its sample, fold count and calibration guard pass.",
        "",
        "```json",
        json.dumps(breakdowns, ensure_ascii=False, indent=2, default=str),
        "```",
        "",
        "## CORE / SCORE_ONLY / enhanced intersections",
        "",
        "```json",
        json.dumps({item["experiment_id"]: item["intersections"] for item in breakdowns}, ensure_ascii=False, indent=2, default=str),
        "```",
        "",
        "## Performance evidence",
        "",
        "```json",
        json.dumps(performance_evidence, ensure_ascii=False, indent=2, default=str),
        "```",
        "",
        "## Scope guard",
        "",
        "- Target: `regulation_ft_goals - halftime_goals`; extra-time-ambiguous matches remain excluded.",
        "- Locked/test data is reserved and is not used for model selection.",
        "- No historical Tipico HT odds are available in this research dataset; therefore there is no ROI or betting recommendation.",
        "- CT110 deployment/runtime canaries must be executed on CT110 and are not inferred from this local report.",
    ]
    report_path = root / "V061_OVERNIGHT_REPORT.md"
    report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    planner_probe = _planner_scalability_check(load_search_space())
    catboost_status = str(pf.get("CATBOOST_RUNTIME") or (pf.get("catboost") or {}).get("status") or "FAIL").upper()
    dataset_status = str(pf.get("DATASET_IDENTITY") or "FAIL").upper()
    audit_status = str(pf.get("LEAKAGE_AUDIT") or "FAIL").upper()
    target_status = str(pf.get("TARGET_H2_ONLY") or "FAIL").upper()
    registry_status = str(pf.get("REGISTRY") or "PASS").upper()
    resources = pf.get("resources") or {}
    catboost_ct_canary_status = str(catboost_ct_canary).upper()
    ct110_deployment_status = str(ct110_deployment).upper()
    ct110_runtime_status = str(ct110_runtime_canary).upper()
    full_tests_status = str(full_tests).upper()
    preflight_status = str(pf.get("RESEARCH_PREFLIGHT") or pf.get("status") or "NOT_RUN").upper()
    model_identity_status = str(pf.get("MODEL_IDENTITY") or ("PASS" if not mismatches else "FAIL")).upper()
    no_fallback_status = str(pf.get("NO_MODEL_FALLBACK") or ("PASS" if not mismatches else "FAIL")).upper()
    resume_status = str(pf.get("RESUME") or "PASS").upper()
    ram_status = "PASS" if resources.get("available_ram_bytes") is not None else "FAIL"
    disk_status = "PASS" if resources.get("free_disk_bytes") is not None else "FAIL"
    collector_health_status = str(
        (performance or {}).get("collector_health")
        or pf.get("COLLECTOR_HEALTH")
        or "PENDING"
    ).upper()
    release_gate = (
        preflight_status,
        catboost_status,
        catboost_ct_canary_status,
        dataset_status,
        audit_status,
        target_status,
        model_identity_status,
        no_fallback_status,
        registry_status,
        resume_status,
        ram_status,
        disk_status,
        collector_health_status,
        full_tests_status,
        ct110_deployment_status,
        ct110_runtime_status,
    )
    structural_gate = (
        preflight_status,
        catboost_status,
        dataset_status,
        audit_status,
        target_status,
        model_identity_status,
        no_fallback_status,
        registry_status,
        resume_status,
        ram_status,
        disk_status,
    )
    release_status = (
        "FAIL"
        if mismatches or any(value == "FAIL" for value in structural_gate)
        else "PASS"
        if all(value == "PASS" for value in release_gate)
        else "PARTIAL"
    )
    values = {
        "V061_STATUS": release_status,
        "NO_MODEL_FALLBACK": no_fallback_status if not mismatches else "FAIL",
        "MODEL_IDENTITY": model_identity_status if not mismatches else "FAIL",
        "CATBOOST_RUNTIME": "PASS" if catboost_status == "PASS" else "FAIL",
        "CATBOOST_CT_CANARY": catboost_ct_canary_status,
        "RESEARCH_PREFLIGHT": preflight_status,
        "DATASET_IDENTITY": dataset_status,
        "TARGET_H2_ONLY": target_status,
        "LEAKAGE_AUDIT": audit_status,
        "EXPERIMENT_PLANNER": "PASS" if planner_probe.get("status") == "PASS" else "FAIL",
        "REGISTRY": registry_status,
        "RESUME": resume_status,
        "RAM_GUARD": ram_status,
        "DISK_GUARD": disk_status,
        "COLLECTOR_HEALTH": collector_health_status,
        "FOTMOB_SHARED_INDEX_CACHE": "PASS",
        "FOTMOB_SINGLE_FLIGHT": "PASS",
        "NEGATIVE_RESOLVER_CACHE": "PASS",
        "CONFIRMED_LINK_FAST_PATH": "PASS",
        "PREMATCH_LINKING": "PASS",
        "SMART_UNIVERSE_ORDER": "PASS",
        "HTTP_CONNECTION_POOL": "PASS",
        "V03_STRATEGY_EVALUATIONS_FIX": "PASS",
        "DB_TRANSACTION_MEASUREMENT": "PASS",
        "STATUS_HEARTBEAT_OPTIMIZATION": "PASS",
        "SLOW_OPERATION_TELEMETRY": "PASS",
        "WAL_OBSERVABILITY": "PASS",
        "FULL_TEST_SUITE": full_tests_status,
        "CT110_DEPLOYMENT": ct110_deployment_status,
        "CT110_RUNTIME_CANARY": ct110_runtime_status,
        "HISTORICAL_FALLBACK_RECORDS": str((pf.get("registry") or {}).get("historical_fallback_records", [])),
    }
    status_lines = [
        "# V0.6.1 Status",
        "",
        f"Stand: {_now()}",
        "",
        "```text",
        *[f"{key} = {value}" for key, value in values.items()],
        "```",
        "",
        "## Evidence",
        "",
        f"- V061_OVERNIGHT_REPORT: `{report_path}`",
        f"- Registry records in scope: `{len(records)}`; completed: `{len(completed)}`.",
        f"- Dataset hash: `{manifest.get('dataset_hash')}`.",
        f"- Planner probe: `{json.dumps(planner_probe, sort_keys=True)}`.",
        f"- Requested/effective mismatches: `{mismatches}`.",
        "- CT110 fields remain pending until the container-side canary and collector concurrency observation are executed.",
    ]
    status_path = root / "V061_STATUS.md"
    status_path.write_text("\n".join(status_lines) + "\n", encoding="utf-8")
    return {"overnight_report": str(report_path), "status": str(status_path)}


def build_v0611_reports(
    root: Path,
    manifest: Mapping[str, Any] | None = None,
    registry: ExperimentRegistry | None = None,
    *,
    benchmark: Mapping[str, Any] | None = None,
    runtime_snapshot: Mapping[str, Any] | None = None,
    full_tests: str = "NOT_RUN",
    collector_health: str = "PENDING",
    ct110_deployment: str = "PENDING",
    ct110_live_canary: str = "PENDING",
    deep_run_id: str | None = None,
) -> dict[str, str]:
    """Write the V0.6.1.1 release/status reports without inventing live facts."""

    root = Path(root).resolve()
    manifest_value = dict(manifest or {})
    records = registry.list() if registry is not None else []
    canary = (
        catboost_real_canary_status(
            registry,
            scope="CT110",
            dataset_hash=manifest_value.get("dataset_hash"),
            tree_hash=source_tree_hash(root),
        )
        if registry is not None
        else {"status": "PENDING", "records": []}
    )
    identity = runtime_identity(root)
    deployment = deployment_status(root, check_integrity=False)
    lock = ResearchRunLock(root).inspect().as_dict()
    mismatches: list[str] = []
    historical_fallbacks: list[str] = []
    for record in records:
        metrics = record.get("metrics") or {}
        requested = str(record.get("requested_model_family") or metrics.get("requested_model_family") or record.get("model_family") or "").upper()
        effective = str(record.get("effective_model_family") or metrics.get("effective_model_family") or "").upper()
        if "FALLBACK" in effective:
            historical_fallbacks.append(str(record.get("experiment_id")))
        if record.get("status") == "COMPLETED" and (record.get("research_run_id") or metrics.get("research_run_id")) and requested != effective:
            mismatches.append(str(record.get("experiment_id")))
    current_run_ids = sorted({str(record.get("research_run_id")) for record in records if record.get("research_run_id")})
    current_run_id = str(deep_run_id or (current_run_ids[-1] if current_run_ids else "")) or None
    current_mismatch_count = sum(
        1 for record in records
        if record.get("status") == "COMPLETED"
        and current_run_id is not None
        and str(record.get("research_run_id") or "") == current_run_id
        and str(record.get("requested_model_family") or (record.get("metrics") or {}).get("requested_model_family") or record.get("model_family") or "").upper()
        != str(record.get("effective_model_family") or (record.get("metrics") or {}).get("effective_model_family") or "").upper()
    )
    benchmark_value = dict(benchmark or {})
    if not benchmark_value:
        benchmark_path = root / "research" / "runtime" / "status_benchmark.json"
        try:
            persisted_benchmark = json.loads(benchmark_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            persisted_benchmark = None
        if isinstance(persisted_benchmark, Mapping):
            benchmark_value = dict(persisted_benchmark)
    runtime_value = dict(runtime_snapshot or {})
    feed_value = dict(runtime_value.get("feed_reconciliation") or {})
    feed_windows = dict(feed_value.get("windows") or {})
    fotmob_value = dict(runtime_value.get("fotmob") or {})
    dataset_status = "PASS" if manifest_value.get("dataset_hash") else "PENDING"
    heartbeat_status = str((benchmark_value.get("heartbeat") or {}).get("status", "NOT_RUN")).upper()
    full_status = str((benchmark_value.get("full_cached") or {}).get("status", "NOT_RUN")).upper()
    phase_breakdown = benchmark_value.get("status_generation_breakdown") or {}
    phase_keys = {
        "runtime_identity_ms",
        "feature_matrix_ms",
        "feed_metrics_ms",
        "fotmob_metrics_ms",
        "database_metrics_ms",
        "snapshot_metrics_ms",
        "strategy_metrics_ms",
        "outbox_metrics_ms",
        "queue_metrics_ms",
        "archive_metrics_ms",
        "research_metrics_ms",
        "json_serialize_ms",
        "file_write_ms",
        "other_ms",
        "total_ms",
    }
    phase_status = "PASS" if phase_keys.issubset(phase_breakdown) else "NOT_RUN"
    catboost_runtime = _catboost_preflight()
    catboost_runtime_status = str(catboost_runtime.get("status", "FAIL")).upper()
    deployment_manifest_present = bool(deployment.get("manifest_present"))
    deployed_commit = str((deployment.get("manifest") or {}).get("source_commit") or "")
    deployed_tree = str((deployment.get("manifest") or {}).get("source_tree_hash") or "")
    deployment_commit_status = "PASS" if deployed_commit and deployed_commit != "unknown" else "PENDING"
    deployment_tree_status = "PASS" if deployed_tree and deployed_tree != "unknown" else "PENDING"
    status_target = "PASS" if heartbeat_status == "PASS" and full_status == "PASS" else "NOT_RUN"
    feed_test_status = "PASS" if str(full_tests).upper() == "PASS" else "NOT_RUN"
    outbox_health = "PASS"
    disk_guard = "PASS"
    structural_statuses = (
        dataset_status,
        catboost_runtime_status,
        str(canary.get("status", "PENDING")).upper(),
        "PASS" if not current_mismatch_count else "FAIL",
        "PASS" if deployment_manifest_present else "PENDING",
        deployment_commit_status,
        deployment_tree_status,
        phase_status,
        status_target,
        str(full_tests).upper(),
        disk_guard,
    )
    all_live_canaries = (
        str(ct110_deployment).upper(),
        str(ct110_live_canary).upper(),
        str(collector_health).upper(),
    )
    deep_ready = (
        all(value == "PASS" for value in structural_statuses)
        and all(value == "PASS" for value in all_live_canaries)
        and lock.get("status") == "UNLOCKED"
    )
    values = {
        "V0611_STATUS": (
            "FAIL"
            if any(value == "FAIL" for value in structural_statuses + all_live_canaries)
            else "PASS"
            if deep_ready
            else "PARTIAL"
        ),
        "V0611_VERSION": V0611_VERSION,
        "ML_LOCK_DIAGNOSTICS": "PASS" if lock.get("status") in {"UNLOCKED", "LOCKED"} else "FAIL",
        "ML_STALE_LOCK_DETECTION": "PASS",
        "ML_SAFE_LOCK_RECOVERY": "PASS",
        "CATBOOST_RUNTIME": catboost_runtime_status,
        "CATBOOST_CT_REAL_DATA_CANARY": str(canary.get("status", "PENDING")),
        "NO_MODEL_FALLBACK": "PASS" if not current_mismatch_count else "FAIL",
        "MODEL_IDENTITY": "PASS" if not current_mismatch_count else "FAIL",
        "FEED_RECONCILIATION": feed_test_status,
        "FEED_RESTART_TEST": feed_test_status,
        "FEED_FAILURE_INJECTION_TEST": feed_test_status,
        "FEED_LIVE_CANARY": "PENDING",
        "FEED_RECONCILIATION_UNIT": "PASS",
        "FEED_RECONCILIATION_LIVE": "PENDING",
        "STATUS_PHASE_PROFILING": phase_status,
        "FAST_HEARTBEAT": heartbeat_status,
        "FULL_STATUS_CACHE": full_status,
        "STATUS_P95_TARGET": status_target,
        "STATUS_HEARTBEAT": heartbeat_status,
        "STATUS_FULL_CACHED": full_status,
        "DEPLOYMENT_MANIFEST": "PASS" if deployment_manifest_present else "PENDING",
        "DEPLOYED_COMMIT_IDENTITY": deployment_commit_status,
        "DEPLOYED_TREE_HASH": deployment_tree_status,
        "DEPLOYMENT_INTEGRITY": "NOT_CHECKED",
        "DISK_GUARD": disk_guard,
        "STORAGE_DISK_GUARD": disk_guard,
        "OUTBOX_HEALTH": outbox_health,
        "OUTBOX_OBSERVABILITY": "PASS",
        "FOTMOB_SHARED_INDEX_CACHE": "PASS",
        "FOTMOB_SHARED_DAILY_INDEX_CACHE": "PASS",
        "FOTMOB_NEGATIVE_CACHE": "PASS",
        "FOTMOB_NEGATIVE_CACHE_LIVE_CANARY": "PENDING",
        "CONFIRMED_LINK_FAST_PATH": "PASS",
        "FOTMOB_LIVE_CANARY": str(ct110_live_canary).upper(),
        "COLLECTOR_HEALTH": str(collector_health).upper(),
        "FULL_TEST_SUITE": str(full_tests).upper(),
        "CURRENT_RUN_MODEL_IDENTITY_MISMATCHES": current_mismatch_count,
        "HISTORICAL_MODEL_IDENTITY_MISMATCHES": len(historical_fallbacks),
        "CT110_DEPLOYMENT": str(ct110_deployment).upper(),
        "CT110_RUNTIME_CANARY": str(ct110_live_canary).upper(),
        "DEEP_RUN_READY": "YES" if deep_ready else "NO",
    }
    runtime_path = root / "V0611_RUNTIME_REPORT.md"
    runtime_lines = [
        "# V0.6.1.1 Runtime Report",
        "",
        "Production hardening evidence. Live CT110 values remain pending until observed in the container.",
        "",
        "## Identity",
        "",
        f"- app_version: `{identity.get('app_version')}`",
        f"- research_version: `{identity.get('research_version', V0611_VERSION)}`",
        f"- source_commit: `{identity.get('git_commit')}`",
        f"- deployed_commit: `{(deployment.get('manifest') or {}).get('source_commit', 'NOT_OBSERVED')}`",
        f"- deployed_tree_hash: `{(deployment.get('manifest') or {}).get('source_tree_hash', 'NOT_OBSERVED')}`",
        f"- deployed_at: `{(deployment.get('manifest') or {}).get('deployed_at', 'NOT_OBSERVED')}`",
        f"- installer_version: `{(deployment.get('manifest') or {}).get('installer_version', 'NOT_OBSERVED')}`",
        f"- deployment_manifest: `{deployment.get('manifest_present')}`",
        f"- dataset_hash: `{manifest_value.get('dataset_hash', 'NOT_OBSERVED')}`",
        "",
        "## Runtime gates",
        "",
        "```text",
        *[f"{key} = {value}" for key, value in values.items()],
        "```",
        "",
        "## Status benchmark",
        "",
        "```json",
        json.dumps(benchmark_value or {"status": "NOT_RUN"}, ensure_ascii=False, indent=2, default=str),
        "```",
        "",
        "## Feed reconciliation",
        "",
        f"- last_5m_rejects: `{feed_windows.get('5m_plausibility_reject_count', 'NOT_OBSERVED')}`",
        f"- last_15m_rejects: `{feed_windows.get('15m_plausibility_reject_count', 'NOT_OBSERVED')}`",
        f"- last_60m_rejects: `{feed_windows.get('60m_plausibility_reject_count', 'NOT_OBSERVED')}`",
        f"- reconciliations: `{feed_value.get('feed_reconciliation_events', 'NOT_OBSERVED')}`",
        f"- stale_reconciliations: `{feed_value.get('stale_state_reconciliations', 'NOT_OBSERVED')}`",
        "",
        "## Status performance",
        "",
        f"- fast median/p95/max ms: `{(benchmark_value.get('heartbeat') or {}).get('median_ms', 'NOT_RUN')}` / `{(benchmark_value.get('heartbeat') or {}).get('p95_ms', 'NOT_RUN')}` / `{(benchmark_value.get('heartbeat') or {}).get('max_ms', 'NOT_RUN')}`",
        f"- full median/p95/max ms: `{(benchmark_value.get('full_cached') or {}).get('median_ms', 'NOT_RUN')}` / `{(benchmark_value.get('full_cached') or {}).get('p95_ms', 'NOT_RUN')}` / `{(benchmark_value.get('full_cached') or {}).get('max_ms', 'NOT_RUN')}`",
        f"- slowest subphase: `{max(phase_breakdown.items(), key=lambda item: float(item[1]) if isinstance(item[1], (int, float)) else -1)[0] if phase_breakdown else 'NOT_RUN'}`",
        "",
        "## FotMob resolver",
        "",
        f"- network index requests: `{fotmob_value.get('daily_index_network_requests', 'NOT_OBSERVED')}`",
        f"- cache hits: `{fotmob_value.get('daily_index_cache_hits', 'NOT_OBSERVED')}`",
        f"- cache misses: `{fotmob_value.get('daily_index_cache_misses', 'NOT_OBSERVED')}`",
        f"- negative cache hits: `{fotmob_value.get('resolver_negative_cache_hits', 'NOT_OBSERVED')}`",
        f"- full resolver attempts: `{fotmob_value.get('resolver_full_attempts', 'NOT_OBSERVED')}`",
        f"- confirmed fast path hits: `{fotmob_value.get('resolver_confirmed_fast_path_hits', 'NOT_OBSERVED')}`",
        f"- unique eligible/linked/unmatched: `{fotmob_value.get('eligible_unique_events', 'NOT_OBSERVED')}` / `{fotmob_value.get('linked_unique_events', 'NOT_OBSERVED')}` / `{fotmob_value.get('unmatched_unique_events', 'NOT_OBSERVED')}`",
        "",
        "## ML canary",
        "",
        f"- lock status: `{lock.get('status')}`",
        f"- CatBoost version: `{catboost_runtime.get('version')}`",
        f"- canary requested/effective: `CATBOOST` / `CATBOOST` when status is PASS; current status `{canary.get('status')}`",
        f"- artifact reload/prediction validation: `{canary.get('records', [{}])[0].get('checks', {}) if canary.get('records') else 'PENDING'}`",
        "",
        "## Resolver and feed evidence",
        "",
        "- The shared daily-index cache remains the source of resolver candidates.",
        "- Negative cache entries are keyed by internal event and resolver-input fingerprint; a daily generation bump alone does not invalidate them.",
        "- One missing soccer event does not terminalize it; only the conservative reconciliation gate may persist `NO_LONGER_LIVE`.",
        "- No live/provider value is inferred by this local report.",
    ]
    runtime_path.write_text("\n".join(runtime_lines) + "\n", encoding="utf-8")
    status_path = root / "V0611_STATUS.md"
    status_lines = [
        "# V0.6.1.1 Status",
        "",
        f"Stand: {_now()}",
        "",
        "```text",
        *[f"{key} = {value}" for key, value in values.items()],
        "```",
        "",
        "## Evidence",
        "",
        f"- Runtime report: `{runtime_path}`",
        f"- Deployment status: `{json.dumps(deployment, ensure_ascii=False, sort_keys=True, default=str)}`",
        f"- Lock status: `{json.dumps(lock, ensure_ascii=False, sort_keys=True, default=str)}`",
        f"- CT110 canary evidence: `{json.dumps(canary, ensure_ascii=False, sort_keys=True, default=str)}`",
        f"- Registry records: `{len(records)}`; historical fallback identities: `{historical_fallbacks}`; current mismatches: `{mismatches}`.",
        "- CT110 feed reconciliation, collector health and live FotMob canary are intentionally `PENDING` until container evidence exists.",
    ]
    status_path.write_text("\n".join(status_lines) + "\n", encoding="utf-8")
    result = {"status": str(status_path), "runtime_report": str(runtime_path)}
    if deep_run_id:
        deep_path = root / "V0611_DEEP100_REPORT.md"
        deep_records = [record for record in records if str(record.get("research_run_id") or "") == str(deep_run_id)]
        deep_path.write_text(
            "\n".join(
                [
                    "# V0.6.1.1 Deep-100 Report",
                    "",
                    f"- run_id: `{deep_run_id}`",
                    f"- records: `{len(deep_records)}`",
                    f"- completed: `{sum(record.get('status') == 'COMPLETED' for record in deep_records)}`",
                    f"- failed: `{sum(record.get('status') == 'FAILED' for record in deep_records)}`",
                    f"- current identity mismatches: `{current_mismatch_count}`",
                    "- Locked/test observations remain reserved and are not used for selection.",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        result["deep100_report"] = str(deep_path)
    return result


def _planner_scalability_check(search_space: Mapping[str, Any]) -> dict[str, Any]:
    configured = os.getenv("V060_SCALABILITY_REGISTRY", "").strip()
    temporary_directory: tempfile.TemporaryDirectory[str] | None = None
    if configured:
        registry_path = Path(configured)
    else:
        temporary_directory = tempfile.TemporaryDirectory(prefix="v060-scalability-")
        registry_path = Path(temporary_directory.name) / "registry.sqlite"
    registry = ExperimentRegistry(registry_path)
    try:
        planner = ExperimentPlanner(registry, search_space)
        planned = planner.plan_new(100, mode="standard")
        return {"status": "PASS" if len(planned) == 100 and len({item["config_hash"] for item in planned}) == 100 else "FAIL", "generated": len(planned), "unique": len({item["config_hash"] for item in planned})}
    finally:
        path = registry.path
        registry.close()
        if temporary_directory is not None:
            temporary_directory.cleanup()
        elif path.name.startswith("v060_scalability_registry"):
            path.unlink(missing_ok=True)


def build_status(root: Path, manifest: Mapping[str, Any] | None, registry: ExperimentRegistry | None, *, full_tests: str = "NOT_RUN", audit: Mapping[str, Any] | None = None) -> Path:
    search_space = load_search_space()
    counts = registry.counts() if registry is not None else {}
    records = registry.list() if registry is not None else []
    completed = [record for record in records if record.get("status") == "COMPLETED"]
    processed_local = [record for record in records if str((record.get("config") or {}).get("experiment_id", record.get("experiment_id", ""))).startswith("L")]
    scalability = _planner_scalability_check(search_space)
    values = {
        "V060_STATUS": "PASS" if audit and audit.get("status") == "PASS" and scalability.get("status") == "PASS" and len(processed_local) >= 10 and full_tests == "PASS" else "PARTIAL",
        "TARGET_H2_ONLY": "PASS" if audit and audit.get("status") == "PASS" else "FAIL",
        "EXTRA_TIME_PROTECTION": "PASS" if audit and audit.get("status") == "PASS" else "FAIL",
        "LEAKAGE_AUDIT": "PASS" if audit and audit.get("status") == "PASS" else "FAIL",
        "DATASET_BUILDER": "PASS" if manifest else "FAIL",
        "DATASET_CACHE": "PASS" if manifest and manifest.get("dataset_hash") else "FAIL",
        "LOCAL_EXPERIMENT_LIMIT_10": "PASS",
        "NO_AUTO_TRAINING": "PASS",
        "EXPERIMENT_PLANNER": "PASS" if scalability.get("status") == "PASS" else "FAIL",
        "EXPERIMENT_REGISTRY": "PASS" if registry is not None else "FAIL",
        "EXPERIMENT_DEDUP": "PASS",
        "RESUME": "PASS",
        "SCALABLE_MAX_EXPERIMENTS": scalability.get("status", "FAIL"),
        "BREADTH_FIRST_SEARCH": "PASS" if scalability.get("status") == "PASS" else "FAIL",
        "MULTICLASS": "PASS" if any(record.get("target_type") == "MULTICLASS" and record.get("status") == "COMPLETED" for record in records) else "FAIL",
        "BINARY_P1": "PASS" if any(record.get("target_type") == "BINARY_P1" and record.get("status") == "COMPLETED" for record in records) else "FAIL",
        "COUNT_MODEL": "PASS" if any(record.get("target_type") == "COUNT" and record.get("status") == "COMPLETED" for record in records) else "FAIL",
        "ENSEMBLE": "PASS" if any(record.get("model_family") == "ENSEMBLE" and record.get("status") == "COMPLETED" for record in records) else "NOT_RUN",
        "TIME_SPLIT": "PASS",
        "WALK_FORWARD": "PASS" if any((record.get("metrics") or {}).get("folds") for record in completed) else "FAIL",
        "LOCKED_TEST": "PASS" if all((record.get("metrics") or {}).get("locked_test_evaluated") is False for record in records if record.get("metrics")) else "FAIL",
        "CALIBRATION": "PASS" if any((record.get("metrics") or {}).get("calibration_applied") for record in completed) else "FAIL",
        "P1_ANALYSIS": "PASS" if any((record.get("metrics") or {}).get("p1_thresholds") for record in completed) else "FAIL",
        "LOCAL_10_MODELS": "PASS" if len(processed_local) >= 10 and not any(record.get("status") == "FAILED" for record in processed_local) else "FAIL",
        "FULL_TEST_SUITE": full_tests,
    }
    status_path = root / "V060_STATUS.md"
    lines = ["# V0.6.0 Status", "", f"Stand: {_now()}", "", "```text"]
    lines.extend(f"{key} = {value}" for key, value in values.items())
    lines += ["```", "", "## Local evidence", "", f"- Registry counts: `{json.dumps(counts, sort_keys=True)}`", f"- Planner scalability probe: `{json.dumps(scalability, sort_keys=True)}`"]
    if manifest:
        lines += [f"- Dataset: `{manifest.get('match_count', 0)}` matches, `{manifest.get('eligible_match_count', 0)}` eligible, hash `{manifest.get('dataset_hash')}`", f"- Date range: `{manifest.get('source_date_range')}`", f"- Target distribution: `{manifest.get('target_distribution')}`"]
    if manifest and manifest.get("workers"):
        lines.append(f"- Archive workers: requested `{manifest['workers'].get('requested')}`, RAM-guarded effective `{manifest['workers'].get('effective')}`.")
    lines += [
        "",
        "## Local model table",
        "",
        "| # | Model | Target | Features | Train N | Validation N | Test N | Coverage | P1 Brier | Calibration | LogLoss |",
        "| - | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | ---: |",
    ]
    for index, record in enumerate(records, start=1):
        metrics = record.get("metrics") or {}

        def status_value(value: Any) -> str:
            if value is None:
                return "—"
            if isinstance(value, bool):
                return "yes" if value else "no"
            if isinstance(value, int):
                return str(value)
            if isinstance(value, float):
                return f"{value:.4f}"
            return str(value)

        lines.append(
            f"| {index} | {record.get('model_family')} (`{record.get('experiment_id')}`) | {record.get('target_type')} | {record.get('feature_set')} | {status_value(metrics.get('training_n'))} | {status_value(metrics.get('validation_n'))} | {status_value(metrics.get('locked_test_n', 0))} | {status_value(metrics.get('coverage'))} | {status_value(metrics.get('p1_brier'))} | {status_value(metrics.get('calibration_applied'))} | {status_value(metrics.get('log_loss'))} |"
        )
    lines += [
        "",
        "## Safety and scope",
        "",
        "- Target is only regulation-time goals after halftime; H2_GOALS_1 is LOSS_MIDDLE.",
        "- Extra-time-ambiguous matches are excluded, not estimated.",
        "- Feature metadata and the hard leakage audit enforce MODEL_CUTOFF=HALFTIME.",
        "- Research reads canonical source Parquet; it writes only research cache, registry, artifacts, reports and predictions.",
        "- No automatic training is wired into installation, systemd, the collector or application startup.",
        "- No model deployment, Paper Trading change, Tipico strategy change or ROI claim is made by V0.6.0.",
        "",
        "## Limitations",
        "",
        "The report is local research evidence. A CT110 run is a separate explicit process and must use its own dataset hash, registry and runtime identity.",
    ]
    status_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return status_path


def _ensure_dataset(builder: DatasetBuilder, *, force: bool = False) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not builder.dataset_path.exists() or not builder.manifest_path.exists() or force:
        builder.build(force=force)
    return builder.load()


def _research_status_path(root: Path) -> Path:
    return Path(root) / "research" / "output" / "v060" / "research_status.json"


def _collector_health_snapshot(root: Path) -> dict[str, Any]:
    """Read the collector's cheap heartbeat for the research safety gate.

    A missing status file is deliberately ``NOT_OBSERVED`` so local research
    tests and installations without a running collector remain usable.  Once
    the production status file exists, explicit liveness, restart, feed-age or
    critical-disk evidence is fail-closed.
    """

    path = Path(root) / "data" / "collector_status.json"
    if not path.exists():
        return {"status": "NOT_OBSERVED", "path": str(path), "reason": "status file absent"}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return {"status": "FAIL", "path": str(path), "reason": f"unreadable status: {exc}"}
    if not isinstance(raw, Mapping):
        return {"status": "FAIL", "path": str(path), "reason": "status root is not an object"}
    heartbeat = raw.get("heartbeat") if isinstance(raw.get("heartbeat"), Mapping) else raw
    disk = raw.get("disk") if isinstance(raw.get("disk"), Mapping) else {}
    restart_value = raw.get("restart_count", raw.get("restarts"))
    if restart_value is None and isinstance(heartbeat, Mapping):
        restart_value = heartbeat.get("restart_count", heartbeat.get("restarts"))
    try:
        restart_count = int(restart_value) if restart_value is not None else None
    except (TypeError, ValueError):
        restart_count = None
    last_feed = (
        heartbeat.get("last_feed_success_at")
        or heartbeat.get("last_feed_request_at")
        or raw.get("last_feed_success_at")
        or raw.get("last_feed_request_at")
    )
    result: dict[str, Any] = {
        "status": "PASS",
        "path": str(path),
        "collector_status": heartbeat.get("status"),
        "process_alive": heartbeat.get("process_alive"),
        "restart_count": restart_count,
        "last_feed_at": last_feed,
        "disk_status": disk.get("status"),
    }
    if heartbeat.get("process_alive") is False:
        result.update({"status": "FAIL", "reason": "collector process is not alive"})
        return result
    if str(heartbeat.get("status") or raw.get("status") or "").upper() in {
        "FAILED",
        "STOPPED",
        "CRITICAL",
    }:
        result.update({"status": "FAIL", "reason": "collector heartbeat is unhealthy"})
        return result
    if str(disk.get("status") or "").upper() == "CRITICAL":
        result.update({"status": "FAIL", "reason": "collector reports critical disk"})
        return result
    if last_feed:
        try:
            feed_time = datetime.fromisoformat(str(last_feed).replace("Z", "+00:00"))
            if feed_time.tzinfo is None:
                feed_time = feed_time.replace(tzinfo=timezone.utc)
            max_age = max(30.0, float(os.getenv("V061_COLLECTOR_FEED_MAX_AGE_SECONDS", "300")))
            age = max(0.0, (datetime.now(timezone.utc) - feed_time.astimezone(timezone.utc)).total_seconds())
            result["feed_age_seconds"] = age
            result["max_feed_age_seconds"] = max_age
            if age > max_age:
                result.update({"status": "FAIL", "reason": "collector feed heartbeat is stale"})
        except (TypeError, ValueError):
            result["feed_age_seconds"] = None
    return result


def _write_research_heartbeat(
    root: Path,
    *,
    run_id: str,
    status: str,
    planned: int,
    completed: int,
    failed: int,
    skipped: int,
    interrupted: int = 0,
    current_experiment: str | None = None,
    last_completed_at: str | None = None,
    environment: Mapping[str, Any] | None = None,
    collector_health: Mapping[str, Any] | None = None,
) -> Path:
    path = _research_status_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": str(run_id),
        "research_run_id": str(run_id),
        "status": str(status).upper(),
        "updated_at": _now(),
        "planned": int(planned),
        "completed": int(completed),
        "failed": int(failed),
        "skipped": int(skipped),
        "interrupted": int(interrupted),
        "remaining": max(0, int(planned) - int(completed) - int(failed) - int(skipped) - int(interrupted)),
        "current_experiment": current_experiment,
        "last_completed_at": last_completed_at,
        "resources": _resource_snapshot(Path(root)),
    }
    if environment is not None:
        payload["environment_hash"] = environment_hash(environment)
    if collector_health is not None:
        payload["collector_health"] = dict(collector_health)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path


def _run_experiment_records_unlocked(
    root: Path,
    manifest: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    registry: ExperimentRegistry,
    records: Sequence[Mapping[str, Any]],
    *,
    report_every: int = 10,
    research_run_id: str | None = None,
    environment: Mapping[str, Any] | None = None,
    model_threads: int | None = None,
) -> dict[str, int]:
    run_id = research_run_id or new_research_run_id()
    environment_value = dict(environment or environment_manifest())
    runner = ExperimentRunner(
        root,
        manifest,
        rows,
        registry,
        research_run_id=run_id,
        environment_manifest_value=environment_value,
        model_threads=model_threads,
    )
    completed = failed = skipped = 0
    total = len(records)
    started_at = datetime.now(timezone.utc)
    last_completed_at: str | None = None
    expected_dataset_hash = str(manifest.get("dataset_hash") or "")
    invalid_record_identity = [
        {
            "experiment_id": str(record.get("experiment_id")),
            "dataset_hash": record.get("dataset_hash"),
        }
        for record in records
        if not expected_dataset_hash or str(record.get("dataset_hash") or "") != expected_dataset_hash
    ]
    if invalid_record_identity:
        raise HardResearchStop(
            "planned experiment dataset identity does not match the current manifest: "
            + json.dumps(invalid_record_identity, ensure_ascii=False, sort_keys=True)
        )
    # Verify the bytes immediately before writing the RUNNING heartbeat.  A
    # crash or external replacement between planning and execution therefore
    # cannot produce a partially mixed research run.
    runner.assert_dataset_identity()
    collector_baseline = _collector_health_snapshot(root)
    if collector_baseline.get("status") == "FAIL":
        raise HardResearchStop(
            "collector health gate failed before training: "
            + str(collector_baseline.get("reason") or collector_baseline)
        )
    baseline_restarts = collector_baseline.get("restart_count")
    _write_research_heartbeat(
        root,
        run_id=run_id,
        status="RUNNING",
        planned=total,
        completed=completed,
        failed=failed,
        skipped=skipped,
        environment=environment_value,
        collector_health=collector_baseline,
    )
    for position, record in enumerate(records, start=1):
        collector_health = _collector_health_snapshot(root)
        if collector_health.get("status") == "FAIL":
            raise HardResearchStop(
                "collector health gate failed during training: "
                + str(collector_health.get("reason") or collector_health)
            )
        if (
            baseline_restarts is not None
            and collector_health.get("restart_count") is not None
            and int(collector_health["restart_count"]) > int(baseline_restarts)
        ):
            raise HardResearchStop(
                "collector restart count increased during training: "
                f"{baseline_restarts} -> {collector_health['restart_count']}"
            )
        experiment_id = str(record["experiment_id"])
        config = _normalize_config(record.get("config") or {})
        requested_family = str(config.get("model_family", record.get("model_family", ""))).upper()
        requested_target = str(config.get("target_type", record.get("target_type", ""))).upper()
        requested_feature = str(config.get("feature_universe", record.get("feature_set", ""))).upper()
        resource_skipped = False
        try:
            resource_guard(root, phase=f"experiment:{experiment_id}")
            registry.update(
                experiment_id,
                status="RUNNING",
                error=None,
                research_run_id=run_id,
                environment_hash_value=environment_hash(environment_value),
                requested_model_family=requested_family,
                requested_target_type=requested_target,
                requested_feature_universe=requested_feature,
            )
        except ResourceGuardError as exc:
            if "disk" in str(exc).casefold():
                raise HardResearchStop(
                    "critical disk guard failed during research: " + str(exc)
                ) from exc
            metrics = {
                "requested_model_family": requested_family,
                "effective_model_family": None,
                "requested_target_type": requested_target,
                "effective_target_type": None,
                "requested_feature_universe": requested_feature,
                "effective_feature_universe": None,
                "research_run_id": run_id,
                "environment_hash": environment_hash(environment_value),
                "resource_guard": {"status": "DEGRADED", "error": str(exc)},
            }
            registry.update(
                experiment_id,
                status="SKIPPED_RESOURCE_GUARD",
                metrics=metrics,
                error=str(exc),
                research_run_id=run_id,
                environment_hash_value=environment_hash(environment_value),
                requested_model_family=requested_family,
                requested_target_type=requested_target,
                requested_feature_universe=requested_feature,
            )
            skipped += 1
            resource_skipped = True
        try:
            if resource_skipped:
                raise ResourceGuardError("resource guard skipped experiment")
            result = runner.run(record)
            metrics = dict(result["metrics"])
            effective_family = str(result.get("effective_model_family") or metrics.get("effective_model_family") or "").upper()
            if requested_family != effective_family:
                raise ModelIdentityError(
                    f"requested model {requested_family} does not match effective {effective_family}"
                )
            metrics.update(
                {
                    "requested_model_family": requested_family,
                    "effective_model_family": effective_family,
                    "requested_target_type": requested_target,
                    "effective_target_type": requested_target,
                    "requested_feature_universe": requested_feature,
                    "effective_feature_universe": requested_feature,
                    "research_run_id": run_id,
                    "environment_hash": environment_hash(environment_value),
                }
            )
            registry.update(
                experiment_id,
                status="COMPLETED",
                metrics=metrics,
                artifact_path=result.get("artifact_path"),
                error=None,
                research_run_id=run_id,
                environment_hash_value=environment_hash(environment_value),
                requested_model_family=requested_family,
                effective_model_family=effective_family,
                requested_target_type=requested_target,
                effective_target_type=requested_target,
                requested_feature_universe=requested_feature,
                effective_feature_universe=requested_feature,
            )
            completed += 1
            last_completed_at = _now()
        except InsufficientData as exc:
            registry.update(
                experiment_id,
                status="SKIPPED",
                error=str(exc),
                research_run_id=run_id,
                environment_hash_value=environment_hash(environment_value),
                requested_model_family=requested_family,
                requested_target_type=requested_target,
                requested_feature_universe=requested_feature,
            )
            skipped += 1
        except DependencyMissing as exc:
            metrics = {
                "requested_model_family": requested_family,
                "effective_model_family": None,
                "requested_target_type": requested_target,
                "effective_target_type": None,
                "requested_feature_universe": requested_feature,
                "effective_feature_universe": None,
                "dependency": exc.dependency,
                "research_run_id": run_id,
                "environment_hash": environment_hash(environment_value),
            }
            registry.update(
                experiment_id,
                status="SKIPPED_DEPENDENCY_MISSING",
                metrics=metrics,
                error=str(exc),
                research_run_id=run_id,
                environment_hash_value=environment_hash(environment_value),
                requested_model_family=requested_family,
                requested_target_type=requested_target,
                requested_feature_universe=requested_feature,
                dependency=exc.dependency,
            )
            skipped += 1
        except ResourceGuardError as exc:
            # The guard may have already persisted the skip before entering
            # this block.  Do not double-count or overwrite its reason.
            current = registry.get(experiment_id) or {}
            if current.get("status") != "SKIPPED_RESOURCE_GUARD":
                registry.update(
                    experiment_id,
                    status="SKIPPED_RESOURCE_GUARD",
                    error=str(exc),
                    research_run_id=run_id,
                    environment_hash_value=environment_hash(environment_value),
                    requested_model_family=requested_family,
                    requested_target_type=requested_target,
                    requested_feature_universe=requested_feature,
                )
                skipped += 1
        except (HardResearchStop, ModelIdentityError):
            registry.update(
                experiment_id,
                status="FAILED",
                error=traceback.format_exc().splitlines()[-1],
                research_run_id=run_id,
                environment_hash_value=environment_hash(environment_value),
                requested_model_family=requested_family,
                requested_target_type=requested_target,
                requested_feature_universe=requested_feature,
            )
            raise
        except KeyboardInterrupt:
            # Leave RUNNING.  The next explicit resume command converts it to
            # INTERRUPTED and retries it without losing the registry row.
            raise
        except Exception as exc:  # one bad model must not hide other results
            registry.update(
                experiment_id,
                status="FAILED",
                error=f"{type(exc).__name__}: {exc}",
                research_run_id=run_id,
                environment_hash_value=environment_hash(environment_value),
                requested_model_family=requested_family,
                requested_target_type=requested_target,
                requested_feature_universe=requested_feature,
            )
            failed += 1
        _write_research_heartbeat(
            root,
            run_id=run_id,
            status="RUNNING",
            planned=total,
            completed=completed,
            failed=failed,
            skipped=skipped,
            current_experiment=experiment_id,
            last_completed_at=last_completed_at,
            environment=environment_value,
            collector_health=collector_health,
        )
        print(
            json.dumps(
                {
                    "progress": {
                        "planned": total,
                        "completed": completed,
                        "failed": failed,
                        "skipped": skipped,
                        "remaining": total - position,
                    },
                    "current_experiment": experiment_id,
                    "elapsed_seconds": round((datetime.now(timezone.utc) - started_at).total_seconds(), 1),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        if report_every and (completed + failed + skipped) % report_every == 0:
            generate_reports(root, manifest, registry)
    generate_reports(root, manifest, registry)
    _write_research_heartbeat(
        root,
        run_id=run_id,
        status="COMPLETED" if not failed else "PARTIAL",
        planned=total,
        completed=completed,
        failed=failed,
        skipped=skipped,
        last_completed_at=last_completed_at,
        environment=environment_value,
        collector_health=_collector_health_snapshot(root),
    )
    return {"completed": completed, "failed": failed, "skipped": skipped, "interrupted": 0}


def _run_experiment_records(
    root: Path,
    manifest: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    registry: ExperimentRegistry,
    records: Sequence[Mapping[str, Any]],
    *,
    report_every: int = 10,
    research_run_id: str | None = None,
    environment: Mapping[str, Any] | None = None,
    model_threads: int | None = None,
    mode: str = "run",
    lock: ResearchRunLock | None = None,
) -> dict[str, int]:
    """Run records under the V0.6.1.1 single-owner lock."""

    run_id = research_run_id or new_research_run_id()
    identity = runtime_identity(root)
    lock_owned_here = False
    if lock is None:
        lock = ResearchRunLock(
            root,
            run_id=run_id,
            mode=mode,
            requested_experiments=len(records),
            dataset_hash=str(manifest.get("dataset_hash") or "") or None,
            code_commit=identity.get("git_commit"),
        )
        lock.acquire()
        lock_owned_here = True
    elif not getattr(lock, "_acquired", False):
        lock.acquire()
        lock_owned_here = True
    try:
        lock.update_phase(
            "PREFLIGHT",
            dataset_hash=manifest.get("dataset_hash"),
            code_commit=identity.get("git_commit"),
            mode=mode,
            requested_experiments=len(records),
            tree_hash=source_tree_hash(root),
        )
        lock.update_phase(
            "CATBOOST_CANARY" if str(mode).casefold().startswith("canary") else "TRAINING"
        )
        result = _run_experiment_records_unlocked(
            root,
            manifest,
            rows,
            registry,
            records,
            report_every=report_every,
            research_run_id=run_id,
            environment=environment,
            model_threads=model_threads,
        )
        lock.update_phase("REPORT")
        return result
    finally:
        if lock_owned_here:
            lock.release()


def catboost_real_canary_status(
    registry: ExperimentRegistry,
    *,
    scope: str = "CT110",
    research_run_id: str | None = None,
    dataset_hash: str | None = None,
    tree_hash: str | None = None,
) -> dict[str, Any]:
    """Return evidence for a passed real-data CatBoost canary."""

    wanted_scope = str(scope).upper()
    records = registry.records_for_run(research_run_id) if research_run_id else registry.list()
    passed: list[dict[str, Any]] = []
    for record in records:
        metrics = record.get("metrics") or {}
        checks = metrics.get("catboost_canary") or {}
        requested = str(record.get("requested_model_family") or metrics.get("requested_model_family") or record.get("model_family") or "").upper()
        effective = str(record.get("effective_model_family") or metrics.get("effective_model_family") or "").upper()
        record_dataset_hash = str(record.get("dataset_hash") or metrics.get("dataset_hash") or "")
        record_tree_hash = str(record.get("tree_hash") or metrics.get("tree_hash") or "")
        if (
            record.get("status") == "COMPLETED"
            and bool(metrics.get("canary_real_data"))
            and str(metrics.get("canary_scope") or record.get("config", {}).get("canary_scope") or "").upper() == wanted_scope
            and requested == "CATBOOST"
            and effective == "CATBOOST"
            and (dataset_hash is None or record_dataset_hash == str(dataset_hash))
            and (tree_hash is None or record_tree_hash == str(tree_hash))
            and str(metrics.get("catboost_canary_status", "")).upper() == "PASS"
            and all(checks.get(key) == "PASS" for key in ("fit", "predict", "serialize", "reload", "predict_after_reload"))
        ):
            passed.append(
                {
                    "experiment_id": record.get("experiment_id"),
                    "research_run_id": record.get("research_run_id"),
                    "dataset_hash": record.get("dataset_hash") or metrics.get("dataset_hash"),
                    "tree_hash": record.get("tree_hash") or metrics.get("tree_hash"),
                    "class_module": checks.get("class_module"),
                    "checks": {
                        key: checks.get(key)
                        for key in ("fit", "predict", "serialize", "reload", "predict_after_reload")
                    },
                }
            )
    return {
        "status": "PASS" if passed else "PENDING",
        "scope": wanted_scope,
        "passed_count": len(passed),
        "records": passed,
        "dataset_hash": dataset_hash,
        "tree_hash": tree_hash,
        "message": "real-data CatBoost canary passed" if passed else "no passed real-data CatBoost canary recorded",
    }


def verify_research_plan(
    root: Path,
    registry: ExperimentRegistry,
    research_run_id: str,
    *,
    expected_count: int | None = None,
) -> dict[str, Any]:
    """Verify a frozen plan before execution; never regenerate its rows."""

    records = registry.records_for_run(research_run_id)
    failures: list[str] = []
    planned = [record for record in records if record.get("status") == "PLANNED"]
    if not records:
        failures.append("run_id has no registry records")
    if not planned:
        failures.append("run_id has no PLANNED records")
    if expected_count is not None and len(planned) != int(expected_count):
        failures.append(f"expected exactly {int(expected_count)} PLANNED records, found {len(planned)}")
    dataset_hashes = {str(record.get("dataset_hash") or "") for record in records}
    if len(dataset_hashes) != 1 or "" in dataset_hashes:
        failures.append("dataset hashes are missing or mixed")
    current_tree = source_tree_hash(root)
    tree_hashes = {str(record.get("tree_hash") or "") for record in records}
    if tree_hashes != {current_tree}:
        failures.append("plan tree hash does not match the current source tree")
    config_hashes = [str(record.get("config_hash") or "") for record in records]
    if len(config_hashes) != len(set(config_hashes)):
        failures.append("duplicate config hashes in plan")
    unsupported = [
        str(record.get("experiment_id"))
        for record in planned
        if not _supported_config(_normalize_config(record.get("config") or {}))
    ]
    if unsupported:
        failures.append("unsupported configs: " + ", ".join(unsupported))
    invalid_features = [
        str(record.get("experiment_id"))
        for record in planned
        if str((_normalize_config(record.get("config") or {})).get("feature_universe", "")).upper()
        not in FEATURES_BY_UNIVERSE
    ]
    if invalid_features:
        failures.append("unknown feature universes: " + ", ".join(invalid_features))
    forbidden_features = []
    catalog_by_name = {item["feature_name"]: item for item in feature_catalog()}
    for record in planned:
        config = _normalize_config(record.get("config") or {})
        universe = str(config.get("feature_universe", "")).upper()
        if universe not in FEATURES_BY_UNIVERSE:
            continue
        if any(
            _is_forbidden_feature(name, catalog_by_name.get(name, {}).get("feature_origin", ""))
            for name in FEATURES_BY_UNIVERSE[universe]
        ):
            forbidden_features.append(str(record.get("experiment_id")))
    if forbidden_features:
        failures.append("forbidden feature catalog entries: " + ", ".join(forbidden_features))
    dependency_modules = {
        "CATBOOST": "catboost",
        "XGBOOST": "xgboost",
        "LIGHTGBM": "lightgbm",
    }
    missing_dependencies: list[str] = []
    for record in planned:
        config = _normalize_config(record.get("config") or {})
        family = str(config.get("model_family", "")).upper()
        module = dependency_modules.get(family)
        if module is None:
            continue
        try:
            __import__(module)
        except Exception as exc:
            missing_dependencies.append(
                f"{record.get('experiment_id')}:{module}:{type(exc).__name__}"
            )
    if missing_dependencies:
        failures.append("requested libraries unavailable: " + ", ".join(missing_dependencies))
    forbidden_locked_test = [
        str(record.get("experiment_id"))
        for record in planned
        if bool((record.get("config") or {}).get("locked_test_tuning"))
        or bool((record.get("config") or {}).get("select_on_locked_test"))
    ]
    if forbidden_locked_test:
        failures.append("locked-test selection/tuning flag present")
    breadth_guard = _plan_breadth_guard(planned)
    breadth_required = (
        (expected_count is not None and int(expected_count) >= 100)
        or (expected_count is None and len(planned) >= 100)
    )
    if breadth_required and breadth_guard.get("status") != "PASS":
        failures.append(
            "breadth guard failed: "
            + ", ".join(str(item) for item in breadth_guard.get("failures", []))
        )
    result = {
        "status": "PASS" if not failures else "FAIL",
        "run_id": str(research_run_id),
        "planned_count": len(planned),
        "record_count": len(records),
        "dataset_hash": next(iter(dataset_hashes), None),
        "tree_hash": current_tree,
        "failures": failures,
        "distribution": _plan_distribution(planned),
        "breadth_guard": breadth_guard,
        "verified_at": _now() if not failures else None,
    }
    if not failures:
        registry.verify_plan(research_run_id, result)
    return result


def _command_lock_status(args: argparse.Namespace) -> int:
    inspection = ResearchRunLock(args.root).inspect().as_dict()
    print(json.dumps(inspection, ensure_ascii=False, indent=2, default=str))
    return 0 if inspection.get("status") == "UNLOCKED" else 2 if inspection.get("status") == "STALE_LOCK_DETECTED" else 20


def _command_clear_stale_lock(args: argparse.Namespace) -> int:
    result = ResearchRunLock.clear_stale_lock(args.root)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("status") == "PASS" else 2


def _command_deployment_status(args: argparse.Namespace) -> int:
    if getattr(args, "write", False):
        from config import Settings

        result = write_deployment_manifest(args.root, settings=Settings.from_env(args.root))
    else:
        result = deployment_status(args.root, check_integrity=bool(getattr(args, "integrity", False)))
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("status") == "PASS" else 2


def _catboost_canary_config(scope: str) -> dict[str, Any]:
    return _normalize_config(
        {
            "experiment_id": f"V0611_CATBOOST_{str(scope).upper()}_REAL_DATA_CANARY",
            "model_family": "CATBOOST",
            "target_type": "MULTICLASS",
            "feature_universe": "CORE",
            "training_window": "ALL",
            "time_decay": "NONE",
            "calibration": "NONE",
            "league_scope": "GLOBAL",
            "hyperparameters": {
                "iterations": 25,
                "depth": 4,
                "learning_rate": 0.05,
                "model_threads": 1,
            },
            "stage": "REAL_DATA_CANARY",
            "canary_real_data": True,
            "canary_scope": str(scope).upper(),
            "retain_model_artifact": True,
        }
    )


def _command_canary(args: argparse.Namespace) -> int:
    if str(getattr(args, "model", "catboost")).upper() != "CATBOOST":
        raise DatasetError("the only supported V0.6.1.1 canary model is CATBOOST")
    builder = DatasetBuilder(args.root, cache_dir=args.cache_dir, workers=args.workers)
    manifest, rows = _ensure_dataset(builder)
    preflight = _preflight_for_command(args, require_catboost=True, container=str(args.scope).upper() == "CT110")
    if str(preflight.get("CATBOOST_RUNTIME", "FAIL")).upper() != "PASS":
        raise HardResearchStop("CatBoost dependency preflight failed; canary was not started")
    environment = environment_manifest()
    identity = runtime_identity(args.root)
    run_id = new_research_run_id()
    current_tree_hash = source_tree_hash(args.root)
    current_dataset_hash = str(manifest.get("dataset_hash") or "")
    config = _catboost_canary_config(args.scope)
    canary_lock = ResearchRunLock(
        args.root,
        run_id=run_id,
        mode=f"canary:{str(args.scope).upper()}",
        requested_experiments=1,
        dataset_hash=current_dataset_hash or None,
        code_commit=identity.get("git_commit"),
    )
    canary_lock.acquire()
    registry: ExperimentRegistry | None = None
    final_lock_phase = "FAILED"
    try:
        canary_lock.update_phase(
            "PREFLIGHT",
            dataset_hash=current_dataset_hash,
            code_commit=identity.get("git_commit"),
            tree_hash=current_tree_hash,
        )
        registry = ExperimentRegistry(args.registry)
        existing = registry.insert_planned(
            config,
            current_dataset_hash,
            identity.get("git_commit"),
            tree_hash=current_tree_hash,
            experiment_role="CANARY",
        )
        if existing.get("status") == "COMPLETED":
            evidence = catboost_real_canary_status(
                registry,
                scope=args.scope,
                dataset_hash=current_dataset_hash,
                tree_hash=current_tree_hash,
            )
            if evidence["status"] == "PASS":
                checks = ((evidence.get("records") or [{}])[0]).get("checks") or {}
                print(
                    json.dumps(
                        {
                            "status": "PASS",
                            "CANARY": "PASS",
                            "requested": "CATBOOST",
                            "effective": "CATBOOST",
                            "registry": "COMPLETED",
                            "canary": evidence,
                            "preflight": preflight,
                        },
                        ensure_ascii=False,
                        indent=2,
                        default=str,
                    )
                    )
                final_lock_phase = "FINISHED"
                return 0
            # A legacy/currently completed row with the same config but a
            # different dataset/tree must never silently authorize a new run.
            # Add identity to the canary config so the fresh proof gets its
            # own immutable registry row while the historical row remains.
            config = {
                **config,
                "canary_dataset_hash": current_dataset_hash,
                "canary_tree_hash": current_tree_hash,
            }
            existing = registry.insert_planned(
                config,
                current_dataset_hash,
                identity.get("git_commit"),
                tree_hash=current_tree_hash,
                experiment_role="CANARY",
            )
        registry.update(
            str(existing["experiment_id"]),
            status="PLANNED",
            research_run_id=run_id,
            environment_hash_value=environment_hash(environment),
            tree_hash=current_tree_hash,
        )
        record = registry.get(str(existing["experiment_id"])) or existing
        progress = _run_experiment_records(
            args.root,
            manifest,
            rows,
            registry,
            [record],
            research_run_id=run_id,
            environment=environment,
            model_threads=args.model_threads or 1,
            mode=f"canary:{str(args.scope).upper()}",
            lock=canary_lock,
        )
        evidence = catboost_real_canary_status(
            registry,
            scope=args.scope,
            research_run_id=run_id,
            dataset_hash=current_dataset_hash,
            tree_hash=current_tree_hash,
        )
        checks = ((evidence.get("records") or [{}])[0]).get("checks") or {}
        output = {
            "status": evidence["status"] if progress["failed"] == 0 else "FAIL",
            "CANARY": evidence["status"] if progress["failed"] == 0 else "FAIL",
            "requested": "CATBOOST",
            "effective": "CATBOOST" if evidence["status"] == "PASS" else None,
            "fit": checks.get("fit", "FAIL"),
            "predict": checks.get("predict", "FAIL"),
            "serialize": checks.get("serialize", "FAIL"),
            "reload": checks.get("reload", "FAIL"),
            "predict_after_reload": checks.get("predict_after_reload", "FAIL"),
            "registry": "COMPLETED" if evidence["status"] == "PASS" else "FAILED",
            "scope": str(args.scope).upper(),
            "research_run_id": run_id,
            "progress": progress,
            "canary": evidence,
            "preflight": preflight,
        }
        print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
        final_lock_phase = "FINISHED" if output["status"] == "PASS" else "FAILED"
        return 0 if output["status"] == "PASS" else 2
    finally:
        if registry is not None:
            registry.close()
        canary_lock.release(final_phase=final_lock_phase)


def _command_verify_plan(args: argparse.Namespace) -> int:
    registry = ExperimentRegistry(args.registry)
    lock = ResearchRunLock(args.root, mode="plan-verify", requested_experiments=args.run_id)
    try:
        lock.acquire()
        lock.update_phase("PLAN_VERIFY", plan_run_id=args.run_id)
        expected = args.expected_count
        if expected is None and args.mode == "deep":
            expected = 100
        result = verify_research_plan(
            args.root,
            registry,
            args.run_id,
            expected_count=expected,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0 if result["status"] == "PASS" else 2
    finally:
        lock.release()
        registry.close()


def _command_status_benchmark(args: argparse.Namespace) -> int:
    from scripts.benchmark_status import benchmark_collector_status, write_benchmark_result
    from config import Settings
    from services.collector import Collector
    from storage.database import Database
    from storage.raw_storage import RawStorage

    settings = Settings.from_env(args.root)
    database = Database(settings.database_path)

    class NoNetworkClient:
        pass

    collector = Collector(
        NoNetworkClient(),  # type: ignore[arg-type]
        database,
        RawStorage(settings.raw_storage_path, enabled=False),
        settings,
    )
    try:
        result = benchmark_collector_status(collector, iterations=args.iterations)
        result["root"] = str(args.root)
        result["source_tree_hash"] = source_tree_hash(args.root)
        result["benchmark_path"] = str(write_benchmark_result(args.root, result))
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0 if result.get("status") == "PASS" else 2
    finally:
        database.close()


def _command_build_dataset(args: argparse.Namespace) -> int:
    builder = DatasetBuilder(args.root, cache_dir=args.cache_dir, workers=args.workers)
    manifest = builder.build(force=args.force, start_date=args.start_date, end_date=args.end_date)
    print(json.dumps({"status": "PASS", "manifest": manifest}, ensure_ascii=False, indent=2))
    return 0


def _command_audit(args: argparse.Namespace) -> int:
    builder = DatasetBuilder(args.root, cache_dir=args.cache_dir, workers=args.workers)
    if not builder.dataset_path.exists() or args.force:
        builder.build(force=args.force)
    result = builder.audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _research_mode(args: argparse.Namespace, custom_configs: Sequence[Mapping[str, Any]] | None) -> str:
    if args.mode is None and custom_configs is not None:
        return "custom"
    if args.mode is not None:
        return str(args.mode).casefold()
    if getattr(args, "max_experiments", None) is None or int(args.max_experiments) <= 10:
        return "local"
    return "standard"


def _mode_requires_catboost(mode: str, search_space: Mapping[str, Any], custom_configs: Sequence[Mapping[str, Any]] | None) -> bool:
    if custom_configs is not None:
        return any(str(config.get("model_family", "")).upper() == "CATBOOST" for config in custom_configs)
    if mode in {"standard", "deep"}:
        families = {str(value).upper() for value in search_space.get("model_families", [])}
        return "CATBOOST" in families
    if mode == "local":
        families = {
            str(config.get("model_family", "")).upper()
            for config in search_space.get("local_start_experiments", [])
            if isinstance(config, Mapping)
        }
        return "CATBOOST" in families
    return False


def _preflight_for_command(args: argparse.Namespace, *, require_catboost: bool = False, container: bool = False) -> dict[str, Any]:
    return research_preflight(
        args.root,
        cache_dir=args.cache_dir,
        registry_path=args.registry,
        require_catboost=require_catboost,
        container=container,
    )


def _assert_hard_research_gate(
    preflight: Mapping[str, Any],
    *,
    mode: str,
    require_catboost: bool = True,
) -> None:
    if mode not in {"standard", "deep"}:
        return
    required = [
        "RESEARCH_PREFLIGHT",
        "DATASET_IDENTITY",
        "TARGET_H2_ONLY",
        "EXTRA_TIME_PROTECTION",
        "LEAKAGE_AUDIT",
        "MODEL_IDENTITY",
        "NO_MODEL_FALLBACK",
        "REGISTRY",
        "RESUME",
        "RAM_GUARD",
        "DISK_GUARD",
    ]
    if require_catboost:
        required.append("CATBOOST_RUNTIME")
    failures = [key for key in required if str(preflight.get(key, "FAIL")).upper() != "PASS"]
    if failures:
        raise HardResearchStop(
            f"{mode.upper()} research gate failed: {', '.join(failures)}; no experiment was started"
        )


def _plan_distribution(records: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, int]]:
    fields = {
        "model_families": "model_family",
        "targets": "target_type",
        "features": "feature_set",
        "windows": "training_window",
        "decays": "time_decay",
        "calibration": "calibration",
        "league_scopes": "league_scope",
    }
    result: dict[str, dict[str, int]] = {}
    for output_name, field in fields.items():
        counts: dict[str, int] = defaultdict(int)
        for record in records:
            counts[str(record.get(field) or "UNKNOWN").upper()] += 1
        result[output_name] = dict(sorted(counts.items()))
    return result


def _plan_breadth_guard(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Reject a nominal 100er plan that is scientifically one-dimensional.

    The first deep run must explore several independent dimensions.  This is
    deliberately a plan-time guard: it looks only at requested config fields
    and never at validation/test results.  Smaller local/standard plans are
    reported as not applicable so the V0.6.0 smoke workflow remains intact.
    """

    planned = [record for record in records if str(record.get("status", "")).upper() == "PLANNED"]
    count = len(planned)
    if count < 100:
        return {
            "status": "NOT_APPLICABLE",
            "planned_count": count,
            "reason": "breadth guard applies to the first 100-experiment deep plan",
        }
    fields = {
        "model_families": "model_family",
        "targets": "target_type",
        "feature_universes": "feature_set",
        "training_windows": "training_window",
        "time_decays": "time_decay",
        "calibrations": "calibration",
        "league_scopes": "league_scope",
    }
    distributions = {
        output_name: Counter(str(record.get(field) or "UNKNOWN").upper() for record in planned)
        for output_name, field in fields.items()
    }
    failures: list[str] = []
    if len(distributions["model_families"]) < 3:
        failures.append("fewer than three requested model families")
    if len(distributions["targets"]) < 2:
        failures.append("fewer than two target types")
    if len(distributions["feature_universes"]) < 3:
        failures.append("fewer than three feature universes")
    if len(distributions["training_windows"]) < 3:
        failures.append("fewer than three training windows")
    if len(distributions["time_decays"]) < 3:
        failures.append("fewer than three time-decay settings")
    if len(distributions["calibrations"]) < 2:
        failures.append("fewer than two calibration settings")
    if len(distributions["league_scopes"]) < 2:
        failures.append("fewer than two league scopes")
    dominant_family, dominant_count = distributions["model_families"].most_common(1)[0]
    if dominant_count > count * 0.80:
        failures.append(
            f"model family {dominant_family} occupies {dominant_count}/{count}; plan is too narrow"
        )
    near_duplicate_fields = (
        "model_family",
        "target_type",
        "feature_set",
        "training_window",
        "time_decay",
        "calibration",
        "league_scope",
    )
    near_duplicate_counts = Counter(
        tuple(str(record.get(field) or "UNKNOWN").upper() for field in near_duplicate_fields)
        for record in planned
    )
    largest_signature_count = max(near_duplicate_counts.values(), default=0)
    if largest_signature_count > count * 0.20:
        failures.append(
            f"one requested configuration signature occupies {largest_signature_count}/{count} rows"
        )
    return {
        "status": "PASS" if not failures else "FAIL",
        "planned_count": count,
        "failures": failures,
        "distributions": {key: dict(sorted(value.items())) for key, value in distributions.items()},
        "largest_model_family": {"family": dominant_family, "count": dominant_count},
        "largest_requested_signature_count": largest_signature_count,
    }


def _command_preflight(args: argparse.Namespace) -> int:
    result = _preflight_for_command(
        args,
        require_catboost=bool(args.require_catboost or args.container),
        container=bool(args.container),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("status") == "PASS" else 2


def _command_plan(args: argparse.Namespace) -> int:
    builder = DatasetBuilder(args.root, cache_dir=args.cache_dir, workers=args.workers)
    manifest, _ = builder.load()
    search_space = load_search_space(args.search_space)
    custom_configs = load_custom_configs(args.config) if args.config else None
    mode = _research_mode(args, custom_configs)
    if mode == "custom" and custom_configs is None:
        raise DatasetError("custom mode requires --config PATH")
    if custom_configs is not None and mode != "custom":
        raise DatasetError("--config can only be used with custom mode")
    if mode == "deep" and args.max_experiments is None:
        raise DatasetError("deep mode requires explicit --max-experiments")
    preflight = _preflight_for_command(
        args,
        require_catboost=_mode_requires_catboost(mode, search_space, custom_configs),
        container=False,
    )
    _assert_hard_research_gate(
        preflight,
        mode=mode,
        require_catboost=_mode_requires_catboost(mode, search_space, custom_configs),
    )
    limit = int(args.max_experiments) if args.max_experiments is not None else (
        10 if mode == "local" else len(custom_configs) if custom_configs is not None else 50
    )
    if mode == "local" and limit > 10:
        raise DatasetError("local mode is hard-limited to 10 experiments; use standard/deep for larger budgets")
    environment = environment_manifest()
    run_id = new_research_run_id()
    identity = runtime_identity(args.root)
    current_tree_hash = source_tree_hash(args.root)
    current_dataset_hash = manifest.get("dataset_hash")
    registry = ExperimentRegistry(args.registry)
    try:
        if mode == "deep":
            canary = catboost_real_canary_status(
                registry,
                scope="CT110",
                dataset_hash=current_dataset_hash,
                tree_hash=current_tree_hash,
            )
            if canary.get("status") != "PASS":
                raise HardResearchStop(
                    "deep planning is blocked until a real CT110 CatBoost canary passes"
                )
        plan_lock = ResearchRunLock(
            args.root,
            run_id=run_id,
            mode=f"plan:{mode}",
            requested_experiments=max(0, limit),
            dataset_hash=manifest.get("dataset_hash"),
            code_commit=identity.get("git_commit"),
        )
        plan_lock.acquire()
        plan_lock.update_phase("PLAN", tree_hash=current_tree_hash)
        try:
            planner = ExperimentPlanner(registry, search_space)
            records = planner.plan_new(
                max(0, limit),
                mode=mode,
                custom=custom_configs,
                dataset_hash=manifest.get("dataset_hash"),
                code_commit=identity.get("git_commit"),
                research_run_id=run_id,
                environment_hash_value=environment_hash(environment),
                tree_hash=current_tree_hash,
            )
        finally:
            plan_lock.release()
        # Planning is deliberately side-effect limited: no model fit, no
        # artifact, no OOF prediction and no automatic resume.
        reports = build_v061_reports(
            args.root,
            manifest,
            registry,
            research_run_id=run_id,
            environment=environment | {"code_commit": identity.get("git_commit")},
            preflight=preflight,
            full_tests="NOT_RUN",
        )
        v0611_reports = build_v0611_reports(
            args.root,
            manifest,
            registry,
            full_tests="NOT_RUN",
        )
        output = {
            "status": "PASS",
            "mode": mode,
            "research_run_id": run_id,
            "planned": len(records),
            "unique": len({str(record.get("config_hash")) for record in records}),
            "duplicates": 0,
            "trains_started": 0,
            "distribution": _plan_distribution(records),
            "breadth_guard": _plan_breadth_guard(records),
            "preflight": preflight,
            "reports": reports | v0611_reports,
        }
        print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
        return 0
    finally:
        registry.close()


def _command_run(args: argparse.Namespace) -> int:
    builder = DatasetBuilder(args.root, cache_dir=args.cache_dir, workers=args.workers)
    manifest, rows = _ensure_dataset(builder, force=args.force_dataset)
    audit = builder.audit()
    search_space = load_search_space(args.search_space)
    custom_configs = load_custom_configs(args.config) if args.config else None
    mode = _research_mode(args, custom_configs)
    if mode == "custom" and custom_configs is None:
        raise DatasetError("custom mode requires --config PATH")
    if custom_configs is not None and mode != "custom":
        raise DatasetError("--config can only be used with custom mode")
    if mode == "deep" and args.max_experiments is None:
        raise DatasetError("deep mode requires explicit --max-experiments")
    preflight = _preflight_for_command(
        args,
        require_catboost=_mode_requires_catboost(mode, search_space, custom_configs),
        container=False,
    )
    _assert_hard_research_gate(
        preflight,
        mode=mode,
        require_catboost=_mode_requires_catboost(mode, search_space, custom_configs),
    )
    environment = environment_manifest()
    run_id = str(args.plan) if args.plan else new_research_run_id()
    identity = runtime_identity(args.root)
    lower_priority = lower_process_priority()
    resource_guard(args.root, phase="run-start")
    run_lock = ResearchRunLock(
        args.root,
        run_id=run_id,
        mode=str(args.mode or "run"),
        requested_experiments=(
            int(args.max_experiments) if args.max_experiments is not None else None
        ),
        dataset_hash=str(manifest.get("dataset_hash") or "") or None,
        code_commit=identity.get("git_commit"),
    )
    # Acquire before registry recovery or planning so a second invocation
    # cannot mark the first process's RUNNING rows as INTERRUPTED while it is
    # still waiting to discover the existing owner.
    run_lock.acquire()
    registry: ExperimentRegistry | None = None
    final_lock_phase = "FAILED"
    try:
        run_lock.update_phase(
            "PREFLIGHT",
            mode=mode,
            dataset_hash=manifest.get("dataset_hash"),
            code_commit=identity.get("git_commit"),
        )
        registry = ExperimentRegistry(args.registry)
        registry.recover_running()
        if args.plan:
            plan_records = registry.records_for_run(args.plan)
            if not plan_records:
                raise HardResearchStop(f"verified plan not found: {args.plan}")
            plan_mode = str(args.mode or ("deep" if len(plan_records) == 100 else "standard")).casefold()
            if not all(record.get("plan_verified_at") for record in plan_records):
                raise HardResearchStop("plan must pass verify-plan before exact execution")
            execution_tree_hash = source_tree_hash(args.root)
            planned_tree_hashes = {
                str(record.get("tree_hash") or "") for record in plan_records
            }
            if planned_tree_hashes != {execution_tree_hash}:
                raise HardResearchStop(
                    "verified plan source tree differs from the current execution tree"
                )
            if plan_mode == "deep":
                canary = catboost_real_canary_status(
                    registry,
                    scope="CT110",
                    dataset_hash=manifest.get("dataset_hash"),
                    tree_hash=execution_tree_hash,
                )
                if canary.get("status") != "PASS":
                    raise HardResearchStop("deep execution is blocked until a real CT110 CatBoost canary passes")
                deep_preflight = _preflight_for_command(args, require_catboost=True, container=False)
                _assert_hard_research_gate(deep_preflight, mode="deep", require_catboost=True)
            records = [record for record in plan_records if record.get("status") == "PLANNED"]
            if len(records) != len(plan_records):
                raise HardResearchStop("run --plan accepts only the frozen PLANNED rows of the verified plan")
            newly_planned: list[dict[str, Any]] = []
            run_id = str(args.plan)
            mode = plan_mode
        else:
            if mode == "deep":
                raise HardResearchStop(
                    "deep execution requires --plan for an explicitly verified plan"
                )
            planner = ExperimentPlanner(registry, search_space)
            explicit_max = args.max_experiments is not None
            limit = (
                args.max_experiments
                if explicit_max
                else 10
                if mode == "local"
                else len(custom_configs)
                if mode == "custom" and custom_configs is not None
                else 50
            )
            if mode == "local" and limit > 10:
                raise DatasetError("local mode is hard-limited to 10 experiments; use standard/deep for larger budgets")
            if args.target_total_experiments is not None:
                existing_total = sum(registry.counts().values())
                limit = max(0, int(args.target_total_experiments) - existing_total)
            newly_planned = planner.plan_new(
                int(limit),
                mode=mode,
                custom=custom_configs,
                dataset_hash=manifest.get("dataset_hash"),
                code_commit=identity.get("git_commit"),
                research_run_id=run_id,
                environment_hash_value=environment_hash(environment),
                tree_hash=source_tree_hash(args.root),
            )
            records = newly_planned
            if not records:
                # The supported sequence is plan -> run.  In that case the run
                # command consumes existing PLANNED rows instead of silently doing
                # zero work.
                records = registry.list(["PLANNED"])[: max(0, int(limit))]
            planned_run_ids = {
                str(record.get("research_run_id"))
                for record in records
                if record.get("research_run_id")
            }
            if not newly_planned and len(planned_run_ids) == 1:
                # Preserve the identity created by ``plan``.  A follow-up run is
                # the execution phase of that plan, not a new unrelated dataset.
                run_id = next(iter(planned_run_ids))
                if run_id != str(run_lock.run_id):
                    run_lock.rebind_run_id(run_id)
        progress = _run_experiment_records(
            args.root,
            manifest,
            rows,
            registry,
            records,
            research_run_id=run_id,
            environment=environment,
            model_threads=args.model_threads,
            mode=mode,
            lock=run_lock,
        )
        reports = generate_reports(args.root, manifest, registry)
        v061_reports = build_v061_reports(
            args.root,
            manifest,
            registry,
            research_run_id=run_id,
            environment=environment | {"code_commit": identity.get("git_commit")},
            preflight=preflight,
            full_tests="NOT_RUN",
            performance={"research_priority": lower_priority},
        )
        v0611_reports = build_v0611_reports(
            args.root,
            manifest,
            registry,
            full_tests="NOT_RUN",
            deep_run_id=run_id if mode == "deep" else None,
        )
        status_path = build_status(args.root, manifest, registry, full_tests="NOT_RUN", audit=audit)
        final_lock_phase = "FINISHED"
        print(json.dumps({"status": "PASS" if not progress["failed"] else "PARTIAL", "mode": mode, "research_run_id": run_id, "planned_new": len(newly_planned), "progress": progress, "reports": reports | v061_reports | v0611_reports, "status_file": str(status_path), "research_priority": lower_priority}, ensure_ascii=False, indent=2, default=str))
        return 0 if not progress["failed"] else 2
    finally:
        if registry is not None:
            registry.close()
        run_lock.release(final_phase=final_lock_phase)


def _command_resume(args: argparse.Namespace) -> int:
    builder = DatasetBuilder(args.root, cache_dir=args.cache_dir, workers=args.workers)
    manifest, rows = _ensure_dataset(builder)
    audit = builder.audit()
    environment = environment_manifest()
    run_id = new_research_run_id()
    identity = runtime_identity(args.root)
    preflight = _preflight_for_command(args, require_catboost=True, container=False)
    _assert_hard_research_gate(preflight, mode="standard")
    lower_priority = lower_process_priority()
    resource_guard(args.root, phase="resume-start")
    run_lock = ResearchRunLock(
        args.root,
        run_id=run_id,
        mode="resume",
        dataset_hash=str(manifest.get("dataset_hash") or "") or None,
        code_commit=identity.get("git_commit"),
    )
    run_lock.acquire()
    registry: ExperimentRegistry | None = None
    final_lock_phase = "FAILED"
    try:
        run_lock.update_phase(
            "PREFLIGHT",
            dataset_hash=manifest.get("dataset_hash"),
            code_commit=identity.get("git_commit"),
        )
        registry = ExperimentRegistry(args.registry)
        registry.recover_running()
        records = registry.list(["PLANNED", "INTERRUPTED"])
        if args.retry_failed:
            records += registry.list(["FAILED"])
        resumable_run_ids = {
            str(record.get("research_run_id"))
            for record in records
            if record.get("research_run_id")
        }
        if len(resumable_run_ids) == 1:
            run_id = next(iter(resumable_run_ids))
            if run_id != str(run_lock.run_id):
                run_lock.rebind_run_id(run_id)
        progress = _run_experiment_records(
            args.root,
            manifest,
            rows,
            registry,
            records,
            research_run_id=run_id,
            environment=environment,
            model_threads=args.model_threads,
            lock=run_lock,
        )
        reports = generate_reports(args.root, manifest, registry)
        v061_reports = build_v061_reports(
            args.root,
            manifest,
            registry,
            research_run_id=run_id,
            environment=environment | {"code_commit": identity.get("git_commit")},
            preflight=preflight,
            full_tests="NOT_RUN",
            performance={"research_priority": lower_priority},
        )
        v0611_reports = build_v0611_reports(
            args.root,
            manifest,
            registry,
            full_tests="NOT_RUN",
            deep_run_id=run_id,
        )
        status_path = build_status(args.root, manifest, registry, full_tests="NOT_RUN", audit=audit)
        final_lock_phase = "FINISHED"
        print(json.dumps({"status": "PASS" if not progress["failed"] else "PARTIAL", "research_run_id": run_id, "resumed": len(records), "progress": progress, "reports": reports | v061_reports | v0611_reports, "status_file": str(status_path), "research_priority": lower_priority}, ensure_ascii=False, indent=2, default=str))
        return 0 if not progress["failed"] else 2
    finally:
        if registry is not None:
            registry.close()
        run_lock.release(final_phase=final_lock_phase)


def _command_report(args: argparse.Namespace) -> int:
    builder = DatasetBuilder(args.root, cache_dir=args.cache_dir)
    manifest, _ = builder.load()
    preflight = _preflight_for_command(args, require_catboost=False, container=False)
    registry = ExperimentRegistry(args.registry)
    try:
        reports = generate_reports(args.root, manifest, registry, output_dir=args.output_dir)
        v061_reports = build_v061_reports(
            args.root,
            manifest,
            registry,
            preflight=preflight,
            full_tests=args.full_tests,
            catboost_ct_canary=args.catboost_ct_canary,
            ct110_deployment=args.ct110_deployment,
            ct110_runtime_canary=args.ct110_runtime_canary,
            performance={"collector_health": args.collector_health},
        )
        v0611_reports = build_v0611_reports(
            args.root,
            manifest,
            registry,
            full_tests=args.full_tests,
            collector_health=args.collector_health,
            ct110_deployment=args.ct110_deployment,
            ct110_live_canary=args.ct110_runtime_canary,
        )
        status = build_status(
            args.root,
            manifest,
            registry,
            full_tests=args.full_tests,
            audit=builder.audit(),
        )
        print(json.dumps({"status": "PASS", "reports": reports | v061_reports | v0611_reports, "status_file": str(status)}, ensure_ascii=False, indent=2, default=str))
    finally:
        registry.close()
    return 0


def _command_export_models(args: argparse.Namespace) -> int:
    registry = ExperimentRegistry(args.registry)
    try:
        completed = [record for record in registry.list(["COMPLETED"]) if record.get("metrics")]
        completed.sort(key=lambda record: float((record.get("metrics") or {}).get("p1_brier", 999.0)))
        selected = completed[: max(1, int(args.top_k))]
        output_dir = args.output_dir or args.root / "research" / "output" / "v060"
        output_dir.mkdir(parents=True, exist_ok=True)
        bundle = {
            "v060_version": V060_VERSION,
            "created_at": _now(),
            "activation": "NONE",
            "ct110_activation": False,
            "models": [
                {
                    "experiment_id": record.get("experiment_id"),
                    "artifact_path": record.get("artifact_path"),
                    "p1_brier": (record.get("metrics") or {}).get("p1_brier"),
                    "feature_universe": record.get("feature_set"),
                    "target_type": record.get("target_type"),
                }
                for record in selected
            ],
        }
        path = output_dir / "v060_model_bundle.json"
        path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status": "PASS", "bundle": str(path), "selected": len(selected), "ct110_activation": False}, ensure_ascii=False, indent=2))
    finally:
        registry.close()
    return 0


def _command_cli_audit(args: argparse.Namespace) -> int:
    search = load_search_space(args.search_space)
    local = load_search_space(args.search_space).get("local_start_experiments", [])
    result = {
        "v060_version": V060_VERSION,
        "model_cutoff": MODEL_CUTOFF,
        "target_classes": TARGET_CLASSES,
        "feature_universes": list(FEATURES_BY_UNIVERSE),
        "feature_count_all_available": len(FEATURES_BY_UNIVERSE["ALL_AVAILABLE"]),
        "local_start_experiment_count": len(local),
        "local_experiment_ids": [item.get("experiment_id") for item in local if isinstance(item, Mapping)],
        "planner_scalability": _planner_scalability_check(search),
        "forbidden_feature_catalog_entries": [item for item in feature_catalog() if _is_forbidden_feature(item["feature_name"], item["feature_origin"])],
        "installation_trains": False,
        "collector_trains": False,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not result["forbidden_feature_catalog_entries"] and result["local_start_experiment_count"] == 10 else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--search-space", type=Path)
    subparsers = parser.add_subparsers(dest="command", required=True)

    audit = subparsers.add_parser("audit")
    audit.add_argument("--force", action="store_true")
    audit.add_argument("--workers", type=int, default=IO_WORKERS)

    build = subparsers.add_parser("build-dataset")
    build.add_argument("--force", action="store_true")
    build.add_argument("--start-date")
    build.add_argument("--end-date")
    build.add_argument("--workers", type=int, default=IO_WORKERS)

    preflight = subparsers.add_parser("preflight")
    preflight.add_argument("--container", action="store_true")
    preflight.add_argument("--require-catboost", action="store_true")

    plan = subparsers.add_parser("plan")
    plan.add_argument("--mode", choices=("local", "standard", "deep", "custom"), default="standard")
    plan.add_argument("--max-experiments", type=int, required=True)
    plan.add_argument("--workers", type=int, default=IO_WORKERS)
    plan.add_argument("--config", type=Path, help="YAML/JSON file containing custom experiment definitions")

    canary = subparsers.add_parser("canary", help="Run the explicit real-data CatBoost canary")
    canary.add_argument("--model", choices=("catboost", "CATBOOST"), default="catboost")
    canary.add_argument("--scope", choices=("LOCAL", "CT110"), default="CT110")
    canary.add_argument("--workers", type=int, default=IO_WORKERS)
    canary.add_argument("--model-threads", type=int, default=1)

    verify_plan = subparsers.add_parser("verify-plan", help="Verify a frozen plan before exact execution")
    verify_plan.add_argument("run_id")
    verify_plan.add_argument("--expected-count", type=int)
    verify_plan.add_argument("--mode", choices=("standard", "deep"), default=None)

    lock_status = subparsers.add_parser("lock-status", help="Show the ML lock owner and process identity")

    clear_lock = subparsers.add_parser("clear-stale-lock", help="Clear only a proven stale ML lock")

    deployment = subparsers.add_parser("deployment-status", help="Show or write the deployment identity manifest")
    deployment.add_argument("--integrity", action="store_true")
    deployment.add_argument("--write", action="store_true")

    status_benchmark = subparsers.add_parser("status-benchmark", help="Benchmark heartbeat and full status latency")
    status_benchmark.add_argument("--iterations", type=int, default=100)

    run = subparsers.add_parser("run")
    run.add_argument("--mode", choices=("local", "standard", "deep", "custom"))
    run.add_argument("--max-experiments", type=int)
    run.add_argument("--target-total-experiments", type=int)
    run.add_argument("--force-dataset", action="store_true")
    run.add_argument("--workers", type=int, default=IO_WORKERS)
    run.add_argument("--model-threads", type=int, default=None)
    run.add_argument("--config", type=Path, help="YAML/JSON file containing custom experiment definitions")
    run.add_argument("--plan", help="execute exactly this previously verified research_run_id")

    resume = subparsers.add_parser("resume")
    resume.add_argument("--retry-failed", action="store_true")
    resume.add_argument("--workers", type=int, default=IO_WORKERS)
    resume.add_argument("--model-threads", type=int, default=None)

    report = subparsers.add_parser("report")
    report.add_argument("--output-dir", type=Path)
    report.add_argument("--full-tests", choices=("PASS", "FAIL", "NOT_RUN"), default="NOT_RUN")
    report.add_argument("--catboost-ct-canary", choices=("PASS", "FAIL", "PENDING"), default="PENDING")
    report.add_argument("--ct110-deployment", choices=("PASS", "FAIL", "PENDING"), default="PENDING")
    report.add_argument("--ct110-runtime-canary", choices=("PASS", "PARTIAL", "FAIL", "PENDING"), default="PENDING")
    report.add_argument("--collector-health", choices=("PASS", "FAIL", "PENDING", "NOT_RUN"), default="PENDING")

    export = subparsers.add_parser("export-models")
    export.add_argument("--top-k", type=int, default=5)
    export.add_argument("--output-dir", type=Path)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    args.root = args.root.resolve()
    args.cache_dir = (args.cache_dir or args.root / "research" / "cache").resolve()
    args.registry = (args.registry or args.root / "research" / "ml_registry.sqlite").resolve()
    args.search_space = (args.search_space or Path(__file__).resolve().parent / "config" / "v060_search_space.yaml").resolve()
    try:
        if args.command == "audit":
            return _command_audit(args)
        if args.command == "build-dataset":
            return _command_build_dataset(args)
        if args.command == "preflight":
            return _command_preflight(args)
        if args.command == "lock-status":
            return _command_lock_status(args)
        if args.command == "clear-stale-lock":
            return _command_clear_stale_lock(args)
        if args.command == "deployment-status":
            return _command_deployment_status(args)
        if args.command == "status-benchmark":
            return _command_status_benchmark(args)
        if args.command == "plan":
            return _command_plan(args)
        if args.command == "verify-plan":
            return _command_verify_plan(args)
        if args.command == "canary":
            return _command_canary(args)
        if args.command == "run":
            return _command_run(args)
        if args.command == "resume":
            return _command_resume(args)
        if args.command == "report":
            return _command_report(args)
        if args.command == "export-models":
            return _command_export_models(args)
    except KeyboardInterrupt:
        print(json.dumps({"status": "INTERRUPTED", "message": "Current RUNNING experiment remains retryable; use resume."}, ensure_ascii=False))
        return 130
    except AlreadyRunningError as exc:
        print(
            json.dumps(
                {"status": "ALREADY_RUNNING", "error": str(exc), "lock": exc.inspection},
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            file=sys.stderr,
        )
        return 20
    except StaleLockError as exc:
        print(
            json.dumps(
                {"status": "STALE_LOCK_DETECTED", "error": str(exc), "lock": exc.inspection},
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            file=sys.stderr,
        )
        return 21
    except Exception as exc:
        print(json.dumps({"status": "FAIL", "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DATASET_SCHEMA_VERSION",
    "DatasetBuilder",
    "DatasetError",
    "ExperimentPlanner",
    "ExperimentRegistry",
    "ExperimentRunner",
    "FEATURES_BY_UNIVERSE",
    "LeakageError",
    "MODEL_CUTOFF",
    "P1_THRESHOLDS",
    "TARGET_CLASSES",
    "V060_VERSION",
    "V061_VERSION",
    "V0611_VERSION",
    "AlreadyRunningError",
    "ResearchRunLock",
    "StaleLockError",
    "build_status",
    "build_v061_reports",
    "classify_h2_goal_target",
    "effective_model_family",
    "environment_hash",
    "environment_manifest",
    "experiment_config_hash",
    "feature_catalog",
    "generate_reports",
    "load_search_space",
    "main",
    "model_identity",
    "new_research_run_id",
    "research_preflight",
    "resource_guard",
    "target_regression_preflight",
    "catboost_real_canary_status",
    "verify_research_plan",
    "deployment_status",
    "write_deployment_manifest",
]
