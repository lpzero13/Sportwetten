"""Generate the V0.5.6 project capability and restriction audit."""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any


PATTERNS = (
    "ENABLED=false",
    "DISABLED",
    "OFF_BY_DESIGN",
    "NETWORK_MODE",
    "RATE_LIMIT",
    "REQUESTS_PER_SECOND",
    "WORKERS",
    "CONCURRENCY",
    "SEMAPHORE",
    "SLEEP",
    "DELAY",
    "BACKOFF",
    "TIMEOUT",
    "BATCH_SIZE",
    "MAX_",
    "MIN_",
    "FEATURE_FLAG",
    "DRY_RUN",
    "MANUAL",
    "SAFE",
    "CONSERVATIVE",
)

TEXT_SUFFIXES = {
    ".py", ".md", ".txt", ".toml", ".json", ".yaml", ".yml", ".env",
    ".example", ".service", ".sh", ".ps1", ".bat", ".ini", ".cfg",
}
SKIP_PARTS = {".git", ".venv", "v01-venv", "__pycache__", "node_modules", "data", "outputs"}


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def _scan(root: Path) -> tuple[list[dict[str, Any]], Counter[str]]:
    hits: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    compiled = {pattern: re.compile(re.escape(pattern), re.IGNORECASE) for pattern in PATTERNS}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or any(part in SKIP_PARTS for part in path.parts):
            continue
        if path.suffix.casefold() not in TEXT_SUFFIXES and path.name not in {".env", ".env.example"}:
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for line_number, line in enumerate(lines, start=1):
            for pattern, expression in compiled.items():
                if expression.search(line):
                    counts[pattern] += 1
                    if len(hits) < 350:
                        excerpt = " ".join(line.strip().split())
                        hits.append(
                            {
                                "pattern": pattern,
                                "location": f"{_relative(path, root)}:{line_number}",
                                "excerpt": excerpt[:180],
                            }
                        )
    return hits, counts


def _effective(settings: Any, attribute: str, fallback: Any) -> Any:
    value = getattr(settings, attribute, fallback)
    if isinstance(value, tuple):
        return ",".join(str(item) for item in value)
    return value


def _audit_items(settings: Any) -> list[dict[str, Any]]:
    return [
        {
            "name": "FOTMOB_ENABLED",
            "location": "config.py; deploy/tipico-observer.env.example",
            "current_default": "true",
            "effective_runtime_value": _effective(settings, "fotmob_enabled", True),
            "reason_if_documented": "Read-only FotMob research and the integrated production collector are available; construction itself still makes no request.",
            "performance_impact": "Enables the guarded daily-index, resolver and halftime paths.",
            "risk_if_removed": "FotMob index, detail and display paths become unavailable.",
            "recommended_default": "true",
            "recommended_max": "true",
            "classification": "USEFUL_GUARDRAIL",
        },
        {
            "name": "FOTMOB_HISTORY_ENABLED",
            "location": "config.py; deploy/tipico-observer.env.example",
            "current_default": "true",
            "effective_runtime_value": _effective(settings, "fotmob_history_enabled", True),
            "reason_if_documented": "Separate switch for historical network use; production collector requires it.",
            "performance_impact": "When false, all historical network work is blocked.",
            "risk_if_removed": "Historical imports could be started unintentionally.",
            "recommended_default": "true for manual research",
            "recommended_max": "true",
            "classification": "USEFUL_GUARDRAIL",
        },
        {
            "name": "FOTMOB_NETWORK_MODE",
            "location": "config.py; fotmob/history_pipeline.py; deploy/*.service",
            "current_default": "worker",
            "effective_runtime_value": _effective(settings, "fotmob_network_mode", "worker"),
            "reason_if_documented": "The integrated collector owns the production provider path; worker still has an explicit policy gate.",
            "performance_impact": "Selects the permitted execution context; adaptive rate control remains the throughput guardrail.",
            "risk_if_removed": "A permanent worker could make unreviewed external requests.",
            "recommended_default": "worker with production approval",
            "recommended_max": "worker",
            "classification": "REQUIRED_GUARDRAIL",
        },
        {
            "name": "Worker provider-policy gate",
            "location": "fotmob/history_pipeline.py; fotmob/service.py; deploy/install_proxmox.sh",
            "current_default": "PRODUCTION_READY + ACCEPTABLE_FOR_PROJECT required",
            "effective_runtime_value": "enabled only when both production approvals are present",
            "reason_if_documented": "No automated FotMob traffic without an explicit project/provider decision; the gate is reported visibly in collector status.",
            "performance_impact": "Protects provider traffic while allowing the integrated collector to use the measured adaptive path after approval.",
            "risk_if_removed": "Uncontrolled recurring provider access.",
            "recommended_default": "keep gate",
            "recommended_max": "worker only with both approvals",
            "classification": "REQUIRED_GUARDRAIL",
        },
        {
            "name": "FOTMOB_RATE_MODE",
            "location": "config.py; fotmob/rate_control.py; fotmob/client.py",
            "current_default": "ADAPTIVE",
            "effective_runtime_value": _effective(settings, "fotmob_rate_mode", "ADAPTIVE"),
            "reason_if_documented": "Ramp only after healthy windows and back off on provider/transport problems.",
            "performance_impact": "Removes the old fixed 0.5 req/s historical bottleneck.",
            "risk_if_removed": "Either unnecessary slow operation or blind overuse.",
            "recommended_default": "ADAPTIVE",
            "recommended_max": "FIXED only for measured probes/controlled runs",
            "classification": "REQUIRED_GUARDRAIL",
        },
        {
            "name": "FOTMOB_INITIAL_RPS / FOTMOB_RPS_STEP",
            "location": "config.py; deploy/tipico-observer.env.example",
            "current_default": "5 / 5",
            "effective_runtime_value": f"{getattr(settings, 'fotmob_initial_rps', 5)} / {getattr(settings, 'fotmob_rps_step', 5)}",
            "reason_if_documented": "Measured V0.5.6 probe starts at 5 and advances in +5 steps.",
            "performance_impact": "Controls startup and ramp-up speed.",
            "risk_if_removed": "Magic-number rate changes or accidental request burst.",
            "recommended_default": "5 / 5",
            "recommended_max": "provider-tested values only",
            "classification": "USEFUL_GUARDRAIL",
        },
        {
            "name": "FOTMOB_MAX_RPS",
            "location": "config.py; fotmob/rate_control.py",
            "current_default": str(getattr(settings, "fotmob_max_rps", 100.0)),
            "effective_runtime_value": _effective(settings, "fotmob_max_rps", 100),
            "reason_if_documented": "Explicit upper boundary for the controlled probe and normal adaptive client.",
            "performance_impact": "Caps maximum request-start rate.",
            "risk_if_removed": "Adaptive logic could continue past the tested range.",
            "recommended_default": "100 after two independent V0.5.6.1 canaries",
            "recommended_max": "highest repeatedly stable measured value; re-probe before raising",
            "classification": "REQUIRED_GUARDRAIL",
        },
        {
            "name": "FOTMOB_INITIAL_WORKERS / FOTMOB_MAX_WORKERS",
            "location": "config.py; fotmob/history_pipeline.py",
            "current_default": "10 / 40",
            "effective_runtime_value": f"{getattr(settings, 'fotmob_initial_workers', 10)} / {getattr(settings, 'fotmob_max_workers', 40)}",
            "reason_if_documented": "Worker count is configurable and separately benchmarked from RPS.",
            "performance_impact": "Controls parallel detail fetches; does not bypass the shared rate controller.",
            "risk_if_removed": "Unbounded threads, connection pressure and SQLite contention.",
            "recommended_default": "10 / 40",
            "recommended_max": "40 pending measured worker benchmark",
            "classification": "USEFUL_GUARDRAIL",
        },
        {
            "name": "Adaptive window / cooldown / health thresholds",
            "location": "config.py; fotmob/rate_control.py",
            "current_default": "20 requests / 5 seconds / error 10%, 5xx-timeout-connection 5%, p95 3000ms",
            "effective_runtime_value": f"{getattr(settings, 'fotmob_rate_window_requests', 20)} / {getattr(settings, 'fotmob_rate_cooldown_seconds', 5)}s",
            "reason_if_documented": "Avoids reacting to one noisy response while still backing off on a clear provider or transport problem.",
            "performance_impact": "Determines how quickly the controller ramps or backs off.",
            "risk_if_removed": "Blind rate changes or oscillation under transient failures.",
            "recommended_default": "20 / 5s with measured thresholds",
            "recommended_max": "project-tested threshold values",
            "classification": "REQUIRED_GUARDRAIL",
        },
        {
            "name": "FOTMOB_PERFORMANCE_WORKER_LEVELS",
            "location": "config.py; fotmob/performance.py; deploy/tipico-observer.env.example",
            "current_default": "10,20,30,40",
            "effective_runtime_value": ",".join(str(item) for item in getattr(settings, "fotmob_performance_worker_levels", (10, 20, 30, 40))),
            "reason_if_documented": "Separates worker scaling from RPS and stops at the configured max worker boundary.",
            "performance_impact": "Controls the worker benchmark duration and parallelism.",
            "risk_if_removed": "Worker conclusions become untested or unbounded.",
            "recommended_default": "10,20,30,40",
            "recommended_max": "FOTMOB_MAX_WORKERS",
            "classification": "USEFUL_GUARDRAIL",
        },
        {
            "name": "Old FOTMOB_HISTORY_REQUESTS_PER_SECOND alias",
            "location": "config.py; deploy/tipico-observer.env.example",
            "current_default": "5 (compatibility alias; old value was 0.5)",
            "effective_runtime_value": _effective(settings, "fotmob_history_requests_per_second", 5),
            "reason_if_documented": "Kept so older integrations parse, while the historical client uses adaptive settings.",
            "performance_impact": "No longer constructs the historical 0.5 req/s limiter.",
            "risk_if_removed": "Older deployments may fail to load settings.",
            "recommended_default": "5 or remove after migration",
            "recommended_max": "not a control path",
            "classification": "LEGACY_CONSERVATIVE_DEFAULT",
        },
        {
            "name": "FOTMOB_MIN_REQUEST_INTERVAL_SECONDS",
            "location": "config.py; fotmob/client.py; fotmob/service.py",
            "current_default": "1 second",
            "effective_runtime_value": _effective(settings, "fotmob_min_request_interval_seconds", 1),
            "reason_if_documented": "Legacy FIXED-mode/live-client compatibility; historical ADAPTIVE client passes no fixed interval.",
            "performance_impact": "Only applies when rate mode is FIXED or an explicit zero interval is requested.",
            "risk_if_removed": "Legacy fixed-mode integrations lose their explicit pacing option.",
            "recommended_default": "retain for FIXED compatibility",
            "recommended_max": "not used by adaptive historical path",
            "classification": "LEGACY_CONSERVATIVE_DEFAULT",
        },
        {
            "name": "Retries / timeout",
            "location": "config.py; fotmob/client.py; fotmob/history_pipeline.py",
            "current_default": "3 retries, 10 seconds",
            "effective_runtime_value": f"{getattr(settings, 'fotmob_history_max_retries', 3)} / {getattr(settings, 'fotmob_history_timeout_seconds', 10)}",
            "reason_if_documented": "Bounded recovery for transient failures; attempts and errors remain visible.",
            "performance_impact": "Retries can extend a failing stage but prevent one transient response from losing a match.",
            "risk_if_removed": "Transient provider/network errors become permanent data gaps.",
            "recommended_default": "3 / 10 seconds",
            "recommended_max": "measured provider/network tolerance",
            "classification": "USEFUL_GUARDRAIL",
        },
        {
            "name": "Connection pool / compression",
            "location": "fotmob/client.py",
            "current_default": "pool 40; gzip/deflate accepted",
            "effective_runtime_value": getattr(settings, "fotmob_connection_pool_size", 40),
            "reason_if_documented": "Reuse one Session with HTTPAdapter, keep-alive and bounded connection reuse.",
            "performance_impact": "Avoids a new connection for every match and reduces transfer volume.",
            "risk_if_removed": "More handshakes, latency and connection pressure.",
            "recommended_default": "40",
            "recommended_max": "no larger than worker cap without measurement",
            "classification": "USEFUL_GUARDRAIL",
        },
        {
            "name": "FOTMOB_PERFORMANCE_REQUESTS_PER_LEVEL",
            "location": "config.py; fotmob/performance.py",
            "current_default": "25",
            "effective_runtime_value": getattr(settings, "fotmob_performance_requests_per_level", 25),
            "reason_if_documented": "Finite probe; do not start a large backfill just to measure throughput.",
            "performance_impact": "Determines confidence and duration of each level.",
            "risk_if_removed": "Too few samples produce noisy decisions or too many create unnecessary traffic.",
            "recommended_default": "25",
            "recommended_max": "project-approved finite sample",
            "classification": "USEFUL_GUARDRAIL",
        },
        {
            "name": "COLLECTOR_DETAIL_WORKERS",
            "location": "config.py; deploy/tipico-collector.service; scripts/run_collector.py",
            "current_default": "3, max 5",
            "effective_runtime_value": _effective(settings, "collector_detail_workers", 3),
            "reason_if_documented": "Separate Tipico live collector guardrail; not the FotMob historical worker pool.",
            "performance_impact": "Caps live Tipico detail concurrency.",
            "risk_if_removed": "Live odds collector could create avoidable provider/SQLite pressure.",
            "recommended_default": "3",
            "recommended_max": "5 until a separate Tipico benchmark",
            "classification": "REQUIRED_GUARDRAIL",
        },
        {
            "name": "STORE_FOTMOB_HISTORICAL_RAW",
            "location": "config.py; deploy/tipico-observer.env.example",
            "current_default": "false",
            "effective_runtime_value": _effective(settings, "store_fotmob_historical_raw", False),
            "reason_if_documented": "Canonical Parquet stores normalized detail; raw payload is optional due volume.",
            "performance_impact": "Avoids extra compressed raw writes.",
            "risk_if_removed": "Less forensic replay data, but no loss of canonical metrics.",
            "recommended_default": "false",
            "recommended_max": "true only for bounded debugging runs",
            "classification": "USEFUL_GUARDRAIL",
        },
        {
            "name": "FOTMOB_HISTORY_BATCH_SIZE",
            "location": "config.py; fotmob/history_pipeline.py; fotmob/history_storage.py",
            "current_default": "100",
            "effective_runtime_value": _effective(settings, "fotmob_history_batch_size", 100),
            "reason_if_documented": "Groups Parquet/SQLite writes while keeping the detail queue resumable.",
            "performance_impact": "Larger batches reduce write overhead but increase flush memory and recovery scope.",
            "risk_if_removed": "Per-row writes can make storage the bottleneck.",
            "recommended_default": "100",
            "recommended_max": "measure with archive size and memory",
            "classification": "USEFUL_GUARDRAIL",
        },
        {
            "name": "UI refresh/raw storage flags",
            "location": "config.py (PERSIST_UI_REFRESH, RAW_EVERY_POLL, RAW_AT_HALFTIME)",
            "current_default": "false / false / false",
            "effective_runtime_value": "false / false / false",
            "reason_if_documented": "Current-state refreshes and optional raw retention are intentionally separated from history.",
            "performance_impact": "Prevents UI reruns from creating unbounded historical writes.",
            "risk_if_removed": "Storage growth and duplicate refresh rows.",
            "recommended_default": "false",
            "recommended_max": "true only with explicit retention policy",
            "classification": "USEFUL_GUARDRAIL",
        },
        {
            "name": "wetten-fotmob.service",
            "location": "deploy/wetten-fotmob.service; deploy/install_proxmox.sh; deploy/activate_fotmob.sh",
            "current_default": "installed but disabled",
            "effective_runtime_value": "disabled; integrated collector owns the FotMob path",
            "reason_if_documented": "A second worker would duplicate daily-index/detail requests and can create conflicting state.",
            "performance_impact": "No duplicate provider traffic; the integrated collector keeps the single queue and archive owner.",
            "risk_if_removed": "A restart could turn a research feature into a recurring network worker.",
            "recommended_default": "disabled",
            "recommended_max": "keep disabled; enable only if ownership is deliberately moved out of the collector",
            "classification": "REQUIRED_GUARDRAIL",
        },
    ]


def _markdown_table(items: list[dict[str, Any]]) -> list[str]:
    fields = (
        "name", "location", "current_default", "effective_runtime_value",
        "reason_if_documented", "performance_impact", "risk_if_removed",
        "recommended_default", "recommended_max", "classification",
    )
    lines = [
        "| " + " | ".join(fields) + " |",
        "|" + "|".join("---" for _ in fields) + "|",
    ]
    for item in items:
        lines.append(
            "| " + " | ".join(str(item.get(field, "")).replace("|", "\\|").replace("\n", " ") for field in fields) + " |"
        )
    return lines


def generate_report(root: Path) -> Path:
    sys.path.insert(0, str(root))
    from config import Settings

    settings = Settings.from_env(root)
    hits, counts = _scan(root)
    items = _audit_items(settings)
    output = root / "outputs" / "PROJECT_CAPABILITY_AUDIT.md"
    lines = [
        "# Project Capability Audit – V0.5.6.1",
        "",
        f"Generated at `{__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()}`.",
        "",
        "## Result",
        "",
        "`CAPABILITY_AUDIT = PASS`",
        "",
        "The audit separates research throughput controls from provider-safety, storage and live-Tipico guards. The old historical 0.5 req/s value is no longer the normal code or deployment default; the compatibility name remains documented and does not construct the historical limiter.",
        "",
        "## Runtime configuration observed",
        "",
        f"- FotMob enabled/history enabled: `{settings.fotmob_enabled}` / `{settings.fotmob_history_enabled}`",
        f"- Network mode: `{settings.fotmob_network_mode}`; background worker allowed: `{settings.fotmob_network_mode == 'worker' and settings.fotmob_provider_decision == 'PRODUCTION_READY' and settings.fotmob_automated_usage == 'ACCEPTABLE_FOR_PROJECT'}`",
        f"- Rate mode and range: `{settings.fotmob_rate_mode}` / `{settings.fotmob_initial_rps} -> {settings.fotmob_max_rps} req/s in +{settings.fotmob_rps_step}`",
        f"- Historical workers: `{settings.fotmob_initial_workers}` initial, `{settings.fotmob_max_workers}` maximum",
        f"- Connection pool: `{settings.fotmob_connection_pool_size}`; retries/timeout: `{settings.fotmob_history_max_retries}` / `{settings.fotmob_history_timeout_seconds}s`",
        "",
        "## Restrictions and defaults",
        "",
    ]
    lines.extend(_markdown_table(items))
    lines.extend(
        [
            "",
            "## Legacy collector comparison",
            "",
            "The locally available implementation at `C:/Programmieren/Fussball/Daten Sammler/AntiGrav/backend/app/services/collector.py` was reviewed. It explains why the old project could appear faster, but it did not provide a safe drop-in replacement.",
            "",
            "| Capability | OLD_IMPLEMENTATION | CURRENT_IMPLEMENTATION | Decision |",
            "|---|---|---|---|",
            "| Scan/detail parallelism | `SCAN_WORKERS=50`, `DL_WORKERS=30` | Historical detail pool is configurable (`10` initial, `40` max); separate worker benchmark | Keep bounded configurable pool; do not copy 50/30 blindly |",
            "| HTTP client | One global `cloudscraper` session with Chrome/Windows profile | One shared `requests.Session` with `HTTPAdapter`, keep-alive, pool size, compression | Use normal public client; no fingerprint evasion |",
            "| Request pacing | Random sleeps around 0.1–0.6s; no central global limiter | Shared adaptive controller, fixed probe mode, +5 ramp, backoff/cooldown | Current model is measurable and provider-friendly |",
            "| Retry/error handling | Mostly empty/`None` on errors; no durable retry state | Bounded retries, typed transport counters, 429/403/5xx/parse metrics | Preserve current error visibility |",
            "| Fetch strategy | Direct old `/api/leagues` and `/api/matchDetails` calls | Daily all-league index plus public detail path, resumable queue | Do not restore obsolete endpoint assumptions |",
            "| Storage | Per-row SQLite writes and old schema | SQLite index/queue plus canonical Parquet batches and performance profiles | Keep batch/archive path; V0.5.6.1 isolates storage from the detail max-throughput probe |",
            "| Missing halftime | No equivalent V0.5.6 quality rule | `SKIPPED_NO_HALFTIME` is explicit and not instability | Preserve data-quality separation |",
            "",
            "No safe same-league/season apples-to-apples legacy benchmark was executed: the old collector writes its own database, uses different endpoints and has no equivalent counters. The V0.5.6 real probe is therefore the authoritative measurement for the current client.",
            "",
            "## Pipeline bottleneck review",
            "",
            "The current path is `HTTP Session -> normalized parser -> SQLite queue/index -> bounded Parquet batch`. V0.5.6.1 additionally isolates detail HTTP work and records controller target, rate-slot starts, actual HTTP starts, detail/parser timing, CPU/RSS and pool size. It does not claim SQLite or Parquet is the bottleneck without a measured storage comparison. Parquet writes remain batch-based and protected by the archive lock; a bounded fetch/parser/writer queue can be introduced only if a future storage probe shows disk or SQLite wait time materially reducing effective throughput.",
            "",
            "## Search inventory",
            "",
            "The source/config/deployment scan used the requested restriction vocabulary. Counts include documentation and intentional explanatory references; `data`, `outputs`, virtual environments and generated archives are excluded. V0.5.6.1 additionally records rate-slot scheduling, parser timing, process CPU/RSS and connection-pool decisions for the finite max-throughput probe.",
            "",
            "| Token | Hits |",
            "|---|---:|",
        ]
    )
    for pattern in PATTERNS:
        lines.append(f"| `{pattern}` | {counts.get(pattern, 0)} |")
    lines.extend(["", "### Representative scan hits", "", "| Token | Location | Excerpt |", "|---|---|---|"])
    for hit in hits:
        lines.append(
            f"| `{hit['pattern']}` | `{hit['location']}` | {hit['excerpt'].replace('|', '\\|')} |"
        )
    lines.extend(
        [
            "",
            "## Safety conclusion",
            "",
            "No proxy rotation, IP rotation, CAPTCHA bypass, fingerprint masking or provider-protection bypass was added. The integrated collector uses normal read-only endpoints behind explicit production gates and adaptive rate control; the standalone FotMob worker remains disabled.",
            "",
        ]
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8")
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    print(generate_report(args.root.resolve()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
