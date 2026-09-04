"""Central configuration for the Tipico Live Observer."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return max(1, int(value))
    except ValueError:
        return default


def _env_nonnegative_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return max(0, int(value))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _env_int_tuple(name: str, default: tuple[int, ...]) -> tuple[int, ...]:
    value = os.getenv(name)
    if value is None:
        return default
    values: list[int] = []
    for item in value.split(","):
        try:
            parsed = int(item.strip())
        except ValueError:
            continue
        if parsed >= 0:
            values.append(parsed)
    return tuple(values) or default


def _env_choice(name: str, default: str, choices: tuple[str, ...]) -> str:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().casefold()
    for choice in choices:
        if normalized == choice.casefold():
            return choice
    return default


LIVE_EVENT_REFRESH_SECONDS = 10
EVENT_MARKET_REFRESH_SECONDS = 5
STORE_RAW_RESPONSES = True
STORE_ODDS_HISTORY = True
PERSIST_UI_REFRESH = False
RAW_EVERY_POLL = False
RAW_PAPER_ENTRY = True
RAW_AT_HALFTIME = False
RAW_COMPRESSION = "zstd"
PARQUET_COMPRESSION = "zstd"
DEBUG_RAW_RETENTION_DAYS = 7
TIPICO_LANGUAGE = "de"
TIPICO_LICENSE_REGION = "DE"
REQUEST_TIMEOUT_SECONDS = 10
COLLECTOR_FEED_REFRESH_SECONDS = 10
COLLECTOR_PREMATCH_REFRESH_SECONDS = 60
COLLECTOR_DETAIL_WORKERS = 3
COLLECTOR_CORE_REFRESH_SECONDS = 30
COLLECTOR_HALFTIME_DELAYS_SECONDS = (0, 20, 60)
COLLECTOR_STRATEGIC_MINUTES = (55, 60, 65, 70, 75, 80, 85, 90)
COLLECTOR_RETRY_DELAYS_SECONDS = (1, 3, 10)
STALE_PREMATCH_GRACE_HOURS = 6.0
COLLECTION_METRICS_CACHE_TTL_SECONDS = 30.0
# V0.6.1: shared FotMob/index and runtime-observability controls.
SLOW_OPERATION_THRESHOLD_MS = 500.0
STATUS_HEAVY_METRICS_TTL_SECONDS = 15.0
# V0.6.1.1: keep the heartbeat cheap while allowing the full status to be
# cached independently from queue/feed liveness.
STATUS_HEARTBEAT_INTERVAL_SECONDS = 2.0
STATUS_FULL_REFRESH_TTL_SECONDS = 10.0
STATUS_ARCHIVE_METRICS_TTL_SECONDS = 60.0
STATUS_BENCHMARK_ITERATIONS = 100
COLLECTOR_SQL_TRACE_ENABLED = False
MAX_LIVE_ODDS_AGE_SECONDS = 10
DEFAULT_TOTAL_STAKE_EUR = 30
SNAPSHOT_HT_STABLE_DELAY_SECONDS = 45
SNAPSHOT_OUTBOX_EXPORT_INTERVAL_SECONDS = 300
SNAPSHOT_OUTBOX_BATCH_SIZE = 100
SNAPSHOT_PRE_ENABLED = True
SNAPSHOT_HT_ENABLED = True
SNAPSHOT_HT_STABLE_ENABLED = True
SNAPSHOT_60_ENABLED = True
SNAPSHOT_70_ENABLED = True
SNAPSHOT_80_ENABLED = True
SNAPSHOT_FIRST_H2_GOAL_REOPEN_ENABLED = True
SNAPSHOT_85_ENABLED = True
SNAPSHOT_90_ENABLED = True
SNAPSHOT_FINAL_ENABLED = True

# FotMob is enabled for the integrated production collector.  The collector
# owns the daily index, automatic resolver and halftime enrichment.  The
# separate ``wetten-fotmob.service`` remains disabled to avoid a duplicate
# provider worker.
FOTMOB_ENABLED = True
FOTMOB_BASE_URL = "https://www.fotmob.com"
FOTMOB_API_BASE_URL = "https://www.fotmob.com/api"
FOTMOB_MATCH_DETAILS_PATH = "/data/matchDetails?matchId={match_id}"
FOTMOB_POLL_SECONDS = 30
FOTMOB_TIMEOUT_SECONDS = 10
FOTMOB_MAX_RETRIES = 3
FOTMOB_MIN_REQUEST_INTERVAL_SECONDS = 1.0
FOTMOB_MATCHING_TOLERANCE_MINUTES = 15
FOTMOB_HT_STABLE_DELAY_SECONDS = 45
FOTMOB_SNAPSHOT_OUTBOX_EXPORT_INTERVAL_SECONDS = 300
FOTMOB_SNAPSHOT_OUTBOX_BATCH_SIZE = 100
FOTMOB_PROVIDER_DECISION = "PRODUCTION_READY"
FOTMOB_AUTOMATED_USAGE = "ACCEPTABLE_FOR_PROJECT"
FOTMOB_PROVIDER_DECISION_VALUES = ("PRODUCTION_READY", "LIMITED_USE", "NOT_SUITABLE")
FOTMOB_AUTOMATED_USAGE_VALUES = (
    "ACCEPTABLE_FOR_PROJECT",
    "UNCLEAR",
    "NOT_ACCEPTABLE",
)
FOTMOB_LEAGUE_PATH = "/leagues/{league_id}"
FOTMOB_SEASON_PATH = "/leagues/{league_id}?season={season_label}"
FOTMOB_DAILY_MATCHES_PATH = (
    "/data/matches?date={date}&timezone={timezone}&ccode3={ccode3}"
    "&includeNextDayLateNight=true"
)
FOTMOB_ALL_LEAGUES_PATH = "/data/allLeagues?locale={locale}&country={country}"
FOTMOB_DAILY_TIMEZONE = "Europe/Berlin"
FOTMOB_DAILY_CCODE3 = "DEU"
FOTMOB_DAILY_LOCALE = "de"
FOTMOB_DAILY_INDEX_CACHE_TTL_SECONDS = 300.0
FOTMOB_NEGATIVE_RESOLVE_NO_CANDIDATE_TTL_SECONDS = 600.0
FOTMOB_NEGATIVE_RESOLVE_AMBIGUOUS_TTL_SECONDS = 300.0
FOTMOB_NEGATIVE_RESOLVE_NO_DATA_TTL_SECONDS = 1800.0
FOTMOB_HISTORY_ENABLED = True
# Historical research starts at a measurable throughput and can ramp in
# configured steps.  The old 0.5 req/s value is retained only as a legacy
# environment-variable compatibility name below; it is no longer the default.
FOTMOB_RATE_MODE = "ADAPTIVE"
FOTMOB_RATE_MODE_VALUES = ("ADAPTIVE", "FIXED", "CONSERVATIVE")
FOTMOB_INITIAL_RPS = 5.0
FOTMOB_RPS_STEP = 5.0
FOTMOB_MIN_RPS = 0.5
# V0.5.6.1: two independent three-day canaries reached 100 target RPS with
# 100% success and no 429/403/5xx/timeout/parse failures. Windows scheduling
# currently delivers about 63 effective starts/s, so this is a measured
# ceiling rather than an expectation of 100 completed matches/s.
FOTMOB_MAX_RPS = 100.0
FOTMOB_INITIAL_WORKERS = 10
FOTMOB_MAX_WORKERS = 40
FOTMOB_RATE_WINDOW_REQUESTS = 20
FOTMOB_RATE_COOLDOWN_SECONDS = 5.0
FOTMOB_MAX_ERROR_RATE = 0.10
FOTMOB_MAX_5XX_RATE = 0.05
FOTMOB_MAX_TIMEOUT_RATE = 0.05
FOTMOB_MAX_CONNECTION_ERROR_RATE = 0.05
FOTMOB_MAX_P95_LATENCY_MS = 3000.0
FOTMOB_CONNECTION_POOL_SIZE = 40
FOTMOB_PERFORMANCE_REQUESTS_PER_LEVEL = 25
FOTMOB_PERFORMANCE_WORKER_LEVELS = (10, 20, 30, 40)
FOTMOB_PERFORMANCE_STABLE_CONFIRMATIONS = 2
# Backwards-compatible setting names.  Code paths use the adaptive settings.
FOTMOB_HISTORY_WORKERS = FOTMOB_INITIAL_WORKERS
FOTMOB_HISTORY_REQUESTS_PER_SECOND = FOTMOB_INITIAL_RPS
FOTMOB_HISTORY_TIMEOUT_SECONDS = 10
FOTMOB_HISTORY_MAX_RETRIES = 3
FOTMOB_HISTORY_STALE_MINUTES = 30
FOTMOB_HISTORY_MAX_RETRY_ATTEMPTS = 3
FOTMOB_HISTORY_BATCH_SIZE = 100
STORE_FOTMOB_HISTORICAL_RAW = False
FOTMOB_NETWORK_MODE = "worker"
FOTMOB_NETWORK_MODE_VALUES = ("off", "manual", "worker")
FOTMOB_ARCHIVE_ROOT = ""
FOTMOB_HISTORY_LEAGUE_ID = "54"
FOTMOB_HT_ENRICHMENT_ENABLED = True
# V0.5.7: selected-match live display only.  These settings control the
# volatile UI path and do not enable any historical or halftime worker.
DEFAULT_FOTMOB_LIVE_REFRESH_SECONDS = 10
FOTMOB_LIVE_CACHE_TTL_SECONDS = 8
FOTMOB_LIVE_PENDING_MINUTE = 10
FOTMOB_LIVE_NO_DATA_PAYLOAD_THRESHOLD = 3

# V0.5.8 smart-universe policy.  The catalog is refreshed explicitly/infrequently;
# the live feed itself remains the complete Tipico radar on every poll.
SMART_UNIVERSE_ENABLED = True
SMART_UNIVERSE_CACHE_TTL_SECONDS = 300.0
SMART_UNIVERSE_DISCOVERY_PROBE_SECONDS = 900.0
FOTMOB_COVERAGE_MIN_SAMPLE_SIZE = 5
FOTMOB_COVERAGE_FULL_RATIO = 0.90
FOTMOB_COVERAGE_NO_DATA_RATIO = 0.10
TIPICO_MARKET_CAPABILITY_MIN_SAMPLE_SIZE = 5
TIPICO_MARKET_CAPABILITY_MIN_RATIO = 0.50
# Feed reconciliation is intentionally conservative.  Operators may lower
# the observation/time thresholds for a test fixture, but production defaults
# require independent structurally valid responses before stale state can be
# reconciled.
FEED_STALE_RECONCILIATION_MIN_OBSERVATIONS = 3
FEED_STALE_RECONCILIATION_MIN_SECONDS = 60.0
DISK_WARN_FREE_GB = 5.0
DISK_CRITICAL_FREE_GB = 2.0


@dataclass(slots=True)
class Settings:
    """Runtime settings with environment-variable overrides."""

    root_dir: Path = field(default_factory=lambda: Path(__file__).resolve().parent)
    tipico_base_url: str = "https://sports.tipico.de"
    tipico_language: str = TIPICO_LANGUAGE
    tipico_license_region: str = TIPICO_LICENSE_REGION
    soccer_group_id: str = "1101"
    region_tree_sport: str = "1101"
    live_event_refresh_seconds: int = LIVE_EVENT_REFRESH_SECONDS
    event_market_refresh_seconds: int = EVENT_MARKET_REFRESH_SECONDS
    store_raw_responses: bool = STORE_RAW_RESPONSES
    store_odds_history: bool = STORE_ODDS_HISTORY
    persist_ui_refresh: bool = PERSIST_UI_REFRESH
    raw_every_poll: bool = RAW_EVERY_POLL
    raw_paper_entry: bool = RAW_PAPER_ENTRY
    raw_at_halftime: bool = RAW_AT_HALFTIME
    raw_compression: str = RAW_COMPRESSION
    parquet_compression: str = PARQUET_COMPRESSION
    debug_raw_retention_days: int = DEBUG_RAW_RETENTION_DAYS
    request_timeout_seconds: int = REQUEST_TIMEOUT_SECONDS
    stale_overview_seconds: int = 30
    stale_detail_seconds: int = 15
    collector_feed_refresh_seconds: int = COLLECTOR_FEED_REFRESH_SECONDS
    collector_prematch_refresh_seconds: int = COLLECTOR_PREMATCH_REFRESH_SECONDS
    collector_detail_workers: int = COLLECTOR_DETAIL_WORKERS
    collector_core_refresh_seconds: int = COLLECTOR_CORE_REFRESH_SECONDS
    collector_halftime_delays_seconds: tuple[int, ...] = COLLECTOR_HALFTIME_DELAYS_SECONDS
    collector_strategic_minutes: tuple[int, ...] = COLLECTOR_STRATEGIC_MINUTES
    collector_retry_delays_seconds: tuple[int, ...] = COLLECTOR_RETRY_DELAYS_SECONDS
    stale_prematch_grace_hours: float = STALE_PREMATCH_GRACE_HOURS
    collection_metrics_cache_ttl_seconds: float = COLLECTION_METRICS_CACHE_TTL_SECONDS
    slow_operation_threshold_ms: float = SLOW_OPERATION_THRESHOLD_MS
    status_heavy_metrics_ttl_seconds: float = STATUS_HEAVY_METRICS_TTL_SECONDS
    status_heartbeat_interval_seconds: float = STATUS_HEARTBEAT_INTERVAL_SECONDS
    status_full_refresh_ttl_seconds: float = STATUS_FULL_REFRESH_TTL_SECONDS
    status_archive_metrics_ttl_seconds: float = STATUS_ARCHIVE_METRICS_TTL_SECONDS
    status_benchmark_iterations: int = STATUS_BENCHMARK_ITERATIONS
    collector_sql_trace_enabled: bool = COLLECTOR_SQL_TRACE_ENABLED
    max_live_odds_age_seconds: int = MAX_LIVE_ODDS_AGE_SECONDS
    default_total_stake_eur: int = DEFAULT_TOTAL_STAKE_EUR
    snapshot_ht_stable_delay_seconds: int = SNAPSHOT_HT_STABLE_DELAY_SECONDS
    snapshot_outbox_export_interval_seconds: int = SNAPSHOT_OUTBOX_EXPORT_INTERVAL_SECONDS
    snapshot_outbox_batch_size: int = SNAPSHOT_OUTBOX_BATCH_SIZE
    snapshot_pre_enabled: bool = SNAPSHOT_PRE_ENABLED
    snapshot_ht_enabled: bool = SNAPSHOT_HT_ENABLED
    snapshot_ht_stable_enabled: bool = SNAPSHOT_HT_STABLE_ENABLED
    snapshot_60_enabled: bool = SNAPSHOT_60_ENABLED
    snapshot_70_enabled: bool = SNAPSHOT_70_ENABLED
    snapshot_80_enabled: bool = SNAPSHOT_80_ENABLED
    snapshot_first_h2_goal_reopen_enabled: bool = SNAPSHOT_FIRST_H2_GOAL_REOPEN_ENABLED
    snapshot_85_enabled: bool = SNAPSHOT_85_ENABLED
    snapshot_90_enabled: bool = SNAPSHOT_90_ENABLED
    snapshot_final_enabled: bool = SNAPSHOT_FINAL_ENABLED
    fotmob_enabled: bool = FOTMOB_ENABLED
    fotmob_base_url: str = FOTMOB_BASE_URL
    fotmob_api_base_url: str = FOTMOB_API_BASE_URL
    fotmob_match_details_path: str = FOTMOB_MATCH_DETAILS_PATH
    fotmob_poll_seconds: int = FOTMOB_POLL_SECONDS
    fotmob_timeout_seconds: int = FOTMOB_TIMEOUT_SECONDS
    fotmob_max_retries: int = FOTMOB_MAX_RETRIES
    fotmob_min_request_interval_seconds: float = FOTMOB_MIN_REQUEST_INTERVAL_SECONDS
    fotmob_matching_tolerance_minutes: int = FOTMOB_MATCHING_TOLERANCE_MINUTES
    fotmob_ht_stable_delay_seconds: int = FOTMOB_HT_STABLE_DELAY_SECONDS
    fotmob_snapshot_outbox_export_interval_seconds: int = FOTMOB_SNAPSHOT_OUTBOX_EXPORT_INTERVAL_SECONDS
    fotmob_snapshot_outbox_batch_size: int = FOTMOB_SNAPSHOT_OUTBOX_BATCH_SIZE
    fotmob_provider_decision: str = FOTMOB_PROVIDER_DECISION
    fotmob_automated_usage: str = FOTMOB_AUTOMATED_USAGE
    fotmob_league_path: str = FOTMOB_LEAGUE_PATH
    fotmob_season_path: str = FOTMOB_SEASON_PATH
    fotmob_daily_matches_path: str = FOTMOB_DAILY_MATCHES_PATH
    fotmob_all_leagues_path: str = FOTMOB_ALL_LEAGUES_PATH
    fotmob_daily_timezone: str = FOTMOB_DAILY_TIMEZONE
    fotmob_daily_ccode3: str = FOTMOB_DAILY_CCODE3
    fotmob_daily_locale: str = FOTMOB_DAILY_LOCALE
    fotmob_daily_index_cache_ttl_seconds: float = FOTMOB_DAILY_INDEX_CACHE_TTL_SECONDS
    fotmob_negative_resolve_no_candidate_ttl_seconds: float = FOTMOB_NEGATIVE_RESOLVE_NO_CANDIDATE_TTL_SECONDS
    fotmob_negative_resolve_ambiguous_ttl_seconds: float = FOTMOB_NEGATIVE_RESOLVE_AMBIGUOUS_TTL_SECONDS
    fotmob_negative_resolve_no_data_ttl_seconds: float = FOTMOB_NEGATIVE_RESOLVE_NO_DATA_TTL_SECONDS
    fotmob_history_enabled: bool = FOTMOB_HISTORY_ENABLED
    fotmob_rate_mode: str = FOTMOB_RATE_MODE
    fotmob_initial_rps: float = FOTMOB_INITIAL_RPS
    fotmob_rps_step: float = FOTMOB_RPS_STEP
    fotmob_min_rps: float = FOTMOB_MIN_RPS
    fotmob_max_rps: float = FOTMOB_MAX_RPS
    fotmob_initial_workers: int = FOTMOB_INITIAL_WORKERS
    fotmob_max_workers: int = FOTMOB_MAX_WORKERS
    fotmob_rate_window_requests: int = FOTMOB_RATE_WINDOW_REQUESTS
    fotmob_rate_cooldown_seconds: float = FOTMOB_RATE_COOLDOWN_SECONDS
    fotmob_max_error_rate: float = FOTMOB_MAX_ERROR_RATE
    fotmob_max_5xx_rate: float = FOTMOB_MAX_5XX_RATE
    fotmob_max_timeout_rate: float = FOTMOB_MAX_TIMEOUT_RATE
    fotmob_max_connection_error_rate: float = FOTMOB_MAX_CONNECTION_ERROR_RATE
    fotmob_max_p95_latency_ms: float = FOTMOB_MAX_P95_LATENCY_MS
    fotmob_connection_pool_size: int = FOTMOB_CONNECTION_POOL_SIZE
    fotmob_performance_requests_per_level: int = FOTMOB_PERFORMANCE_REQUESTS_PER_LEVEL
    fotmob_performance_worker_levels: tuple[int, ...] = FOTMOB_PERFORMANCE_WORKER_LEVELS
    fotmob_performance_stable_confirmations: int = FOTMOB_PERFORMANCE_STABLE_CONFIRMATIONS
    # Legacy aliases remain visible for existing integrations and reports.
    fotmob_history_workers: int = FOTMOB_HISTORY_WORKERS
    fotmob_history_requests_per_second: float = FOTMOB_HISTORY_REQUESTS_PER_SECOND
    fotmob_history_timeout_seconds: int = FOTMOB_HISTORY_TIMEOUT_SECONDS
    fotmob_history_max_retries: int = FOTMOB_HISTORY_MAX_RETRIES
    fotmob_history_stale_minutes: int = FOTMOB_HISTORY_STALE_MINUTES
    fotmob_history_max_retry_attempts: int = FOTMOB_HISTORY_MAX_RETRY_ATTEMPTS
    fotmob_history_batch_size: int = FOTMOB_HISTORY_BATCH_SIZE
    store_fotmob_historical_raw: bool = STORE_FOTMOB_HISTORICAL_RAW
    fotmob_network_mode: str = FOTMOB_NETWORK_MODE
    fotmob_archive_root: str = FOTMOB_ARCHIVE_ROOT
    fotmob_history_league_id: str = FOTMOB_HISTORY_LEAGUE_ID
    fotmob_ht_enrichment_enabled: bool = FOTMOB_HT_ENRICHMENT_ENABLED
    fotmob_live_refresh_seconds: int = DEFAULT_FOTMOB_LIVE_REFRESH_SECONDS
    fotmob_live_cache_ttl_seconds: int = FOTMOB_LIVE_CACHE_TTL_SECONDS
    fotmob_live_pending_minute: int = FOTMOB_LIVE_PENDING_MINUTE
    fotmob_live_no_data_payload_threshold: int = FOTMOB_LIVE_NO_DATA_PAYLOAD_THRESHOLD
    smart_universe_enabled: bool = SMART_UNIVERSE_ENABLED
    smart_universe_cache_ttl_seconds: float = SMART_UNIVERSE_CACHE_TTL_SECONDS
    smart_universe_discovery_probe_seconds: float = SMART_UNIVERSE_DISCOVERY_PROBE_SECONDS
    fotmob_coverage_min_sample_size: int = FOTMOB_COVERAGE_MIN_SAMPLE_SIZE
    fotmob_coverage_full_ratio: float = FOTMOB_COVERAGE_FULL_RATIO
    fotmob_coverage_no_data_ratio: float = FOTMOB_COVERAGE_NO_DATA_RATIO
    tipico_market_capability_min_sample_size: int = TIPICO_MARKET_CAPABILITY_MIN_SAMPLE_SIZE
    tipico_market_capability_min_ratio: float = TIPICO_MARKET_CAPABILITY_MIN_RATIO
    feed_stale_reconciliation_min_observations: int = FEED_STALE_RECONCILIATION_MIN_OBSERVATIONS
    feed_stale_reconciliation_min_seconds: float = FEED_STALE_RECONCILIATION_MIN_SECONDS
    disk_warn_free_gb: float = DISK_WARN_FREE_GB
    disk_critical_free_gb: float = DISK_CRITICAL_FREE_GB

    @property
    def database_path(self) -> Path:
        return self.root_dir / "data" / "tipico.db"

    @property
    def raw_storage_path(self) -> Path:
        return self.root_dir / "data" / "raw"

    @property
    def log_path(self) -> Path:
        return self.root_dir / "logs" / "tipico.log"

    @property
    def collector_status_path(self) -> Path:
        return self.root_dir / "data" / "collector_status.json"

    @property
    def archive_path(self) -> Path:
        configured = os.getenv("WETTEN_ARCHIVE_PATH")
        return Path(configured).expanduser() if configured else self.root_dir / "data" / "archive"

    @property
    def fotmob_archive_path(self) -> Path:
        """Canonical FotMob root, separate from the Tipico archive root."""

        configured = str(self.fotmob_archive_root or os.getenv("FOTMOB_ARCHIVE_ROOT", "")).strip()
        return Path(configured).expanduser() if configured else self.archive_path / "fotmob"

    @property
    def halftime_reports_path(self) -> Path:
        return self.root_dir / "data" / "halftime_reports"

    @classmethod
    def from_env(cls, root_dir: Path | None = None) -> "Settings":
        """Build settings without requiring a dotenv file."""

        # Do not let an old deployment's explicit 0.5 req/s compatibility
        # variable silently reintroduce the retired historical bottleneck.
        # Operators can still choose a lower value explicitly through the new
        # FOTMOB_INITIAL_RPS setting.
        initial_rps = max(0.0, _env_float("FOTMOB_INITIAL_RPS", FOTMOB_INITIAL_RPS))
        min_rps = max(0.0, _env_float("FOTMOB_MIN_RPS", FOTMOB_MIN_RPS))
        max_rps = max(
            min_rps,
            initial_rps,
            _env_float("FOTMOB_MAX_RPS", FOTMOB_MAX_RPS),
        )
        initial_workers = max(1, _env_int("FOTMOB_INITIAL_WORKERS", FOTMOB_INITIAL_WORKERS))
        max_workers = max(
            initial_workers,
            _env_int("FOTMOB_MAX_WORKERS", FOTMOB_MAX_WORKERS),
        )
        worker_levels = tuple(
            value
            for value in _env_int_tuple(
                "FOTMOB_PERFORMANCE_WORKER_LEVELS", FOTMOB_PERFORMANCE_WORKER_LEVELS
            )
            if value > 0
        ) or FOTMOB_PERFORMANCE_WORKER_LEVELS

        return cls(
            root_dir=root_dir or Path(__file__).resolve().parent,
            tipico_base_url=os.getenv("TIPICO_BASE_URL", "https://sports.tipico.de").rstrip("/"),
            tipico_language=os.getenv("TIPICO_LANGUAGE", TIPICO_LANGUAGE),
            tipico_license_region=os.getenv("TIPICO_LICENSE_REGION", TIPICO_LICENSE_REGION),
            soccer_group_id=os.getenv("TIPICO_SOCCER_GROUP_ID", "1101"),
            region_tree_sport=os.getenv("TIPICO_REGION_TREE_SPORT", "1101"),
            live_event_refresh_seconds=_env_int(
                "LIVE_EVENT_REFRESH_SECONDS", LIVE_EVENT_REFRESH_SECONDS
            ),
            event_market_refresh_seconds=_env_int(
                "EVENT_MARKET_REFRESH_SECONDS", EVENT_MARKET_REFRESH_SECONDS
            ),
            store_raw_responses=_env_bool("STORE_RAW_RESPONSES", STORE_RAW_RESPONSES),
            store_odds_history=_env_bool("STORE_ODDS_HISTORY", STORE_ODDS_HISTORY),
            persist_ui_refresh=_env_bool("PERSIST_UI_REFRESH", PERSIST_UI_REFRESH),
            raw_every_poll=_env_bool("RAW_EVERY_POLL", RAW_EVERY_POLL),
            raw_paper_entry=_env_bool("RAW_PAPER_ENTRY", RAW_PAPER_ENTRY),
            raw_at_halftime=_env_bool("RAW_AT_HALFTIME", RAW_AT_HALFTIME),
            raw_compression=os.getenv("RAW_COMPRESSION", RAW_COMPRESSION).strip().lower() or RAW_COMPRESSION,
            parquet_compression=os.getenv("PARQUET_COMPRESSION", PARQUET_COMPRESSION).strip().lower() or PARQUET_COMPRESSION,
            debug_raw_retention_days=_env_int("DEBUG_RAW_RETENTION_DAYS", DEBUG_RAW_RETENTION_DAYS),
            request_timeout_seconds=_env_int(
                "REQUEST_TIMEOUT_SECONDS", REQUEST_TIMEOUT_SECONDS
            ),
            stale_overview_seconds=_env_int("STALE_OVERVIEW_SECONDS", 30),
            stale_detail_seconds=_env_int("STALE_DETAIL_SECONDS", 15),
            collector_feed_refresh_seconds=_env_int(
                "COLLECTOR_FEED_REFRESH_SECONDS", COLLECTOR_FEED_REFRESH_SECONDS
            ),
            collector_prematch_refresh_seconds=_env_int(
                "COLLECTOR_PREMATCH_REFRESH_SECONDS", COLLECTOR_PREMATCH_REFRESH_SECONDS
            ),
            collector_detail_workers=min(
                5,
                _env_int("COLLECTOR_DETAIL_WORKERS", COLLECTOR_DETAIL_WORKERS),
            ),
            collector_core_refresh_seconds=_env_int(
                "COLLECTOR_CORE_REFRESH_SECONDS", COLLECTOR_CORE_REFRESH_SECONDS
            ),
            collector_halftime_delays_seconds=_env_int_tuple(
                "COLLECTOR_HALFTIME_DELAYS_SECONDS", COLLECTOR_HALFTIME_DELAYS_SECONDS
            ),
            collector_strategic_minutes=_env_int_tuple(
                "COLLECTOR_STRATEGIC_MINUTES", COLLECTOR_STRATEGIC_MINUTES
            ),
            collector_retry_delays_seconds=_env_int_tuple(
                "COLLECTOR_RETRY_DELAYS_SECONDS", COLLECTOR_RETRY_DELAYS_SECONDS
            ),
            stale_prematch_grace_hours=max(
                0.0,
                _env_float("STALE_PREMATCH_GRACE_HOURS", STALE_PREMATCH_GRACE_HOURS),
            ),
            collection_metrics_cache_ttl_seconds=max(
                0.0,
                _env_float(
                    "COLLECTION_METRICS_CACHE_TTL_SECONDS",
                    COLLECTION_METRICS_CACHE_TTL_SECONDS,
                ),
            ),
            slow_operation_threshold_ms=max(
                0.0,
                _env_float("SLOW_OPERATION_THRESHOLD_MS", SLOW_OPERATION_THRESHOLD_MS),
            ),
            status_heavy_metrics_ttl_seconds=max(
                0.0,
                _env_float(
                    "STATUS_HEAVY_METRICS_TTL_SECONDS",
                    STATUS_HEAVY_METRICS_TTL_SECONDS,
                ),
            ),
            status_heartbeat_interval_seconds=max(
                0.1,
                _env_float(
                    "STATUS_HEARTBEAT_INTERVAL_SECONDS",
                    STATUS_HEARTBEAT_INTERVAL_SECONDS,
                ),
            ),
            status_full_refresh_ttl_seconds=max(
                1.0,
                _env_float(
                    "STATUS_FULL_REFRESH_TTL_SECONDS",
                    STATUS_FULL_REFRESH_TTL_SECONDS,
                ),
            ),
            status_archive_metrics_ttl_seconds=max(
                1.0,
                _env_float(
                    "STATUS_ARCHIVE_METRICS_TTL_SECONDS",
                    STATUS_ARCHIVE_METRICS_TTL_SECONDS,
                ),
            ),
            status_benchmark_iterations=_env_int(
                "STATUS_BENCHMARK_ITERATIONS", STATUS_BENCHMARK_ITERATIONS
            ),
            collector_sql_trace_enabled=_env_bool(
                "COLLECTOR_SQL_TRACE_ENABLED", COLLECTOR_SQL_TRACE_ENABLED
            ),
            max_live_odds_age_seconds=_env_int(
                "MAX_LIVE_ODDS_AGE_SECONDS", MAX_LIVE_ODDS_AGE_SECONDS
            ),
            default_total_stake_eur=_env_int(
                "DEFAULT_TOTAL_STAKE_EUR", DEFAULT_TOTAL_STAKE_EUR
            ),
            snapshot_ht_stable_delay_seconds=_env_int(
                "SNAPSHOT_HT_STABLE_DELAY_SECONDS", SNAPSHOT_HT_STABLE_DELAY_SECONDS
            ),
            snapshot_outbox_export_interval_seconds=_env_int(
                "SNAPSHOT_OUTBOX_EXPORT_INTERVAL_SECONDS",
                SNAPSHOT_OUTBOX_EXPORT_INTERVAL_SECONDS,
            ),
            snapshot_outbox_batch_size=_env_int(
                "SNAPSHOT_OUTBOX_BATCH_SIZE", SNAPSHOT_OUTBOX_BATCH_SIZE
            ),
            snapshot_pre_enabled=_env_bool("SNAPSHOT_PRE_ENABLED", SNAPSHOT_PRE_ENABLED),
            snapshot_ht_enabled=_env_bool("SNAPSHOT_HT_ENABLED", SNAPSHOT_HT_ENABLED),
            snapshot_ht_stable_enabled=_env_bool(
                "SNAPSHOT_HT_STABLE_ENABLED", SNAPSHOT_HT_STABLE_ENABLED
            ),
            snapshot_60_enabled=_env_bool("SNAPSHOT_60_ENABLED", SNAPSHOT_60_ENABLED),
            snapshot_70_enabled=_env_bool("SNAPSHOT_70_ENABLED", SNAPSHOT_70_ENABLED),
            snapshot_80_enabled=_env_bool("SNAPSHOT_80_ENABLED", SNAPSHOT_80_ENABLED),
            snapshot_first_h2_goal_reopen_enabled=_env_bool(
                "SNAPSHOT_FIRST_H2_GOAL_REOPEN_ENABLED",
                SNAPSHOT_FIRST_H2_GOAL_REOPEN_ENABLED,
            ),
            snapshot_85_enabled=_env_bool("SNAPSHOT_85_ENABLED", SNAPSHOT_85_ENABLED),
            snapshot_90_enabled=_env_bool("SNAPSHOT_90_ENABLED", SNAPSHOT_90_ENABLED),
            snapshot_final_enabled=_env_bool("SNAPSHOT_FINAL_ENABLED", SNAPSHOT_FINAL_ENABLED),
            fotmob_enabled=_env_bool("FOTMOB_ENABLED", FOTMOB_ENABLED),
            fotmob_base_url=os.getenv("FOTMOB_BASE_URL", FOTMOB_BASE_URL).rstrip("/"),
            fotmob_api_base_url=os.getenv("FOTMOB_API_BASE_URL", FOTMOB_API_BASE_URL).rstrip("/"),
            fotmob_match_details_path=os.getenv(
                "FOTMOB_MATCH_DETAILS_PATH", FOTMOB_MATCH_DETAILS_PATH
            ),
            fotmob_poll_seconds=_env_int("FOTMOB_POLL_SECONDS", FOTMOB_POLL_SECONDS),
            fotmob_timeout_seconds=_env_int(
                "FOTMOB_TIMEOUT_SECONDS", FOTMOB_TIMEOUT_SECONDS
            ),
            fotmob_max_retries=_env_nonnegative_int("FOTMOB_MAX_RETRIES", FOTMOB_MAX_RETRIES),
            fotmob_min_request_interval_seconds=max(
                0.0,
                _env_float(
                    "FOTMOB_MIN_REQUEST_INTERVAL_SECONDS",
                    FOTMOB_MIN_REQUEST_INTERVAL_SECONDS,
                ),
            ),
            fotmob_matching_tolerance_minutes=_env_int(
                "FOTMOB_MATCHING_TOLERANCE_MINUTES",
                FOTMOB_MATCHING_TOLERANCE_MINUTES,
            ),
            fotmob_ht_stable_delay_seconds=_env_int(
                "FOTMOB_HT_STABLE_DELAY_SECONDS", FOTMOB_HT_STABLE_DELAY_SECONDS
            ),
            fotmob_snapshot_outbox_export_interval_seconds=_env_int(
                "FOTMOB_SNAPSHOT_OUTBOX_EXPORT_INTERVAL_SECONDS",
                FOTMOB_SNAPSHOT_OUTBOX_EXPORT_INTERVAL_SECONDS,
            ),
            fotmob_snapshot_outbox_batch_size=_env_int(
                "FOTMOB_SNAPSHOT_OUTBOX_BATCH_SIZE",
                FOTMOB_SNAPSHOT_OUTBOX_BATCH_SIZE,
            ),
            fotmob_provider_decision=_env_choice(
                "FOTMOB_PROVIDER_DECISION",
                FOTMOB_PROVIDER_DECISION,
                FOTMOB_PROVIDER_DECISION_VALUES,
            ),
            fotmob_automated_usage=_env_choice(
                "FOTMOB_AUTOMATED_USAGE",
                FOTMOB_AUTOMATED_USAGE,
                FOTMOB_AUTOMATED_USAGE_VALUES,
            ),
            fotmob_league_path=os.getenv("FOTMOB_LEAGUE_PATH", FOTMOB_LEAGUE_PATH),
            fotmob_season_path=os.getenv("FOTMOB_SEASON_PATH", FOTMOB_SEASON_PATH),
            fotmob_daily_matches_path=os.getenv(
                "FOTMOB_DAILY_MATCHES_PATH", FOTMOB_DAILY_MATCHES_PATH
            ),
            fotmob_all_leagues_path=os.getenv(
                "FOTMOB_ALL_LEAGUES_PATH", FOTMOB_ALL_LEAGUES_PATH
            ),
            fotmob_daily_timezone=os.getenv(
                "FOTMOB_DAILY_TIMEZONE", FOTMOB_DAILY_TIMEZONE
            ).strip()
            or FOTMOB_DAILY_TIMEZONE,
            fotmob_daily_ccode3=os.getenv(
                "FOTMOB_DAILY_CCODE3", FOTMOB_DAILY_CCODE3
            ).strip().upper()
            or FOTMOB_DAILY_CCODE3,
            fotmob_daily_locale=os.getenv(
                "FOTMOB_DAILY_LOCALE", FOTMOB_DAILY_LOCALE
            ).strip()
            or FOTMOB_DAILY_LOCALE,
            fotmob_daily_index_cache_ttl_seconds=max(
                0.0,
                _env_float(
                    "FOTMOB_DAILY_INDEX_CACHE_TTL_SECONDS",
                    FOTMOB_DAILY_INDEX_CACHE_TTL_SECONDS,
                ),
            ),
            fotmob_negative_resolve_no_candidate_ttl_seconds=max(
                0.0,
                _env_float(
                    "FOTMOB_NEGATIVE_RESOLVE_NO_CANDIDATE_TTL_SECONDS",
                    FOTMOB_NEGATIVE_RESOLVE_NO_CANDIDATE_TTL_SECONDS,
                ),
            ),
            fotmob_negative_resolve_ambiguous_ttl_seconds=max(
                0.0,
                _env_float(
                    "FOTMOB_NEGATIVE_RESOLVE_AMBIGUOUS_TTL_SECONDS",
                    FOTMOB_NEGATIVE_RESOLVE_AMBIGUOUS_TTL_SECONDS,
                ),
            ),
            fotmob_negative_resolve_no_data_ttl_seconds=max(
                0.0,
                _env_float(
                    "FOTMOB_NEGATIVE_RESOLVE_NO_DATA_TTL_SECONDS",
                    FOTMOB_NEGATIVE_RESOLVE_NO_DATA_TTL_SECONDS,
                ),
            ),
            fotmob_history_enabled=_env_bool("FOTMOB_HISTORY_ENABLED", FOTMOB_HISTORY_ENABLED),
            fotmob_rate_mode=_env_choice(
                "FOTMOB_RATE_MODE", FOTMOB_RATE_MODE, FOTMOB_RATE_MODE_VALUES
            ),
            fotmob_initial_rps=initial_rps,
            fotmob_rps_step=max(0.0, _env_float("FOTMOB_RPS_STEP", FOTMOB_RPS_STEP)),
            fotmob_min_rps=min_rps,
            fotmob_max_rps=max_rps,
            fotmob_initial_workers=initial_workers,
            fotmob_max_workers=max_workers,
            fotmob_rate_window_requests=_env_int(
                "FOTMOB_RATE_WINDOW_REQUESTS", FOTMOB_RATE_WINDOW_REQUESTS
            ),
            fotmob_rate_cooldown_seconds=max(
                0.0,
                _env_float("FOTMOB_RATE_COOLDOWN_SECONDS", FOTMOB_RATE_COOLDOWN_SECONDS),
            ),
            fotmob_max_error_rate=max(
                0.0, _env_float("FOTMOB_MAX_ERROR_RATE", FOTMOB_MAX_ERROR_RATE)
            ),
            fotmob_max_5xx_rate=max(
                0.0, _env_float("FOTMOB_MAX_5XX_RATE", FOTMOB_MAX_5XX_RATE)
            ),
            fotmob_max_timeout_rate=max(
                0.0, _env_float("FOTMOB_MAX_TIMEOUT_RATE", FOTMOB_MAX_TIMEOUT_RATE)
            ),
            fotmob_max_connection_error_rate=max(
                0.0,
                _env_float(
                    "FOTMOB_MAX_CONNECTION_ERROR_RATE", FOTMOB_MAX_CONNECTION_ERROR_RATE
                ),
            ),
            fotmob_max_p95_latency_ms=max(
                0.0,
                _env_float("FOTMOB_MAX_P95_LATENCY_MS", FOTMOB_MAX_P95_LATENCY_MS),
            ),
            fotmob_connection_pool_size=max(
                1, _env_int("FOTMOB_CONNECTION_POOL_SIZE", FOTMOB_CONNECTION_POOL_SIZE)
            ),
            fotmob_performance_requests_per_level=_env_int(
                "FOTMOB_PERFORMANCE_REQUESTS_PER_LEVEL",
                FOTMOB_PERFORMANCE_REQUESTS_PER_LEVEL,
            ),
            fotmob_performance_worker_levels=worker_levels,
            fotmob_performance_stable_confirmations=_env_int(
                "FOTMOB_PERFORMANCE_STABLE_CONFIRMATIONS",
                FOTMOB_PERFORMANCE_STABLE_CONFIRMATIONS,
            ),
            fotmob_history_workers=min(
                max_workers,
                _env_int("FOTMOB_HISTORY_WORKERS", initial_workers),
            ),
            fotmob_history_requests_per_second=initial_rps,
            fotmob_history_timeout_seconds=_env_int(
                "FOTMOB_HISTORY_TIMEOUT_SECONDS", FOTMOB_HISTORY_TIMEOUT_SECONDS
            ),
            fotmob_history_max_retries=_env_nonnegative_int(
                "FOTMOB_HISTORY_MAX_RETRIES", FOTMOB_HISTORY_MAX_RETRIES
            ),
            fotmob_history_stale_minutes=_env_int(
                "FOTMOB_HISTORY_STALE_MINUTES", FOTMOB_HISTORY_STALE_MINUTES
            ),
            fotmob_history_max_retry_attempts=_env_int(
                "FOTMOB_HISTORY_MAX_RETRY_ATTEMPTS", FOTMOB_HISTORY_MAX_RETRY_ATTEMPTS
            ),
            fotmob_history_batch_size=_env_int(
                "FOTMOB_HISTORY_BATCH_SIZE", FOTMOB_HISTORY_BATCH_SIZE
            ),
            store_fotmob_historical_raw=_env_bool(
                "STORE_FOTMOB_HISTORICAL_RAW", STORE_FOTMOB_HISTORICAL_RAW
            ),
            fotmob_network_mode=_env_choice(
                "FOTMOB_NETWORK_MODE",
                FOTMOB_NETWORK_MODE,
                FOTMOB_NETWORK_MODE_VALUES,
            ),
            fotmob_archive_root=os.getenv("FOTMOB_ARCHIVE_ROOT", FOTMOB_ARCHIVE_ROOT),
            fotmob_history_league_id=os.getenv(
                "FOTMOB_HISTORY_LEAGUE_ID", FOTMOB_HISTORY_LEAGUE_ID
            ).strip()
            or FOTMOB_HISTORY_LEAGUE_ID,
            fotmob_ht_enrichment_enabled=_env_bool(
                "FOTMOB_HT_ENRICHMENT_ENABLED", FOTMOB_HT_ENRICHMENT_ENABLED
            ),
            fotmob_live_refresh_seconds=_env_int(
                "FOTMOB_LIVE_REFRESH_SECONDS", DEFAULT_FOTMOB_LIVE_REFRESH_SECONDS
            ),
            fotmob_live_cache_ttl_seconds=_env_int(
                "FOTMOB_LIVE_CACHE_TTL_SECONDS", FOTMOB_LIVE_CACHE_TTL_SECONDS
            ),
            fotmob_live_pending_minute=_env_nonnegative_int(
                "FOTMOB_LIVE_PENDING_MINUTE", FOTMOB_LIVE_PENDING_MINUTE
            ),
            fotmob_live_no_data_payload_threshold=_env_int(
                "FOTMOB_LIVE_NO_DATA_PAYLOAD_THRESHOLD",
                FOTMOB_LIVE_NO_DATA_PAYLOAD_THRESHOLD,
            ),
            smart_universe_enabled=_env_bool(
                "SMART_UNIVERSE_ENABLED", SMART_UNIVERSE_ENABLED
            ),
            smart_universe_cache_ttl_seconds=max(
                0.0,
                _env_float(
                    "SMART_UNIVERSE_CACHE_TTL_SECONDS",
                    SMART_UNIVERSE_CACHE_TTL_SECONDS,
                ),
            ),
            smart_universe_discovery_probe_seconds=max(
                0.0,
                _env_float(
                    "SMART_UNIVERSE_DISCOVERY_PROBE_SECONDS",
                    SMART_UNIVERSE_DISCOVERY_PROBE_SECONDS,
                ),
            ),
            fotmob_coverage_min_sample_size=max(
                1,
                _env_int(
                    "FOTMOB_COVERAGE_MIN_SAMPLE_SIZE",
                    FOTMOB_COVERAGE_MIN_SAMPLE_SIZE,
                ),
            ),
            fotmob_coverage_full_ratio=min(
                1.0,
                max(
                    0.0,
                    _env_float("FOTMOB_COVERAGE_FULL_RATIO", FOTMOB_COVERAGE_FULL_RATIO),
                ),
            ),
            fotmob_coverage_no_data_ratio=min(
                1.0,
                max(
                    0.0,
                    _env_float(
                        "FOTMOB_COVERAGE_NO_DATA_RATIO",
                        FOTMOB_COVERAGE_NO_DATA_RATIO,
                    ),
                ),
            ),
            tipico_market_capability_min_sample_size=max(
                1,
                _env_int(
                    "TIPICO_MARKET_CAPABILITY_MIN_SAMPLE_SIZE",
                    TIPICO_MARKET_CAPABILITY_MIN_SAMPLE_SIZE,
                ),
            ),
            tipico_market_capability_min_ratio=min(
                1.0,
                max(
                    0.0,
                    _env_float(
                        "TIPICO_MARKET_CAPABILITY_MIN_RATIO",
                        TIPICO_MARKET_CAPABILITY_MIN_RATIO,
                    ),
                ),
            ),
            feed_stale_reconciliation_min_observations=_env_int(
                "FEED_STALE_RECONCILIATION_MIN_OBSERVATIONS",
                FEED_STALE_RECONCILIATION_MIN_OBSERVATIONS,
            ),
            feed_stale_reconciliation_min_seconds=max(
                0.0,
                _env_float(
                    "FEED_STALE_RECONCILIATION_MIN_SECONDS",
                    FEED_STALE_RECONCILIATION_MIN_SECONDS,
                ),
            ),
            disk_warn_free_gb=max(
                0.0,
                _env_float("DISK_WARN_FREE_GB", DISK_WARN_FREE_GB),
            ),
            disk_critical_free_gb=max(
                0.0,
                _env_float("DISK_CRITICAL_FREE_GB", DISK_CRITICAL_FREE_GB),
            ),
        )


def configure_logging(settings: Settings) -> logging.Logger:
    """Configure the application logger once and return it."""

    settings.log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("tipico")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    marker = str(settings.log_path.resolve())
    if not any(getattr(handler, "_tipico_log_path", None) == marker for handler in logger.handlers):
        file_handler = logging.FileHandler(settings.log_path, encoding="utf-8")
        file_handler._tipico_log_path = marker  # type: ignore[attr-defined]
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        )
        logger.addHandler(file_handler)

    return logger
