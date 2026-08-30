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

# FotMob is an optional enrichment source.  It is deliberately disabled by
# default: Tipico collection, analysis and paper trading must remain fully
# operational when FotMob is unavailable or not permitted in the deployment.
FOTMOB_ENABLED = False
FOTMOB_BASE_URL = "https://www.fotmob.com"
FOTMOB_API_BASE_URL = "https://www.fotmob.com/api"
FOTMOB_MATCH_DETAILS_PATH = "/matchDetails?matchId={match_id}"
FOTMOB_POLL_SECONDS = 30
FOTMOB_TIMEOUT_SECONDS = 10
FOTMOB_MAX_RETRIES = 3
FOTMOB_MIN_REQUEST_INTERVAL_SECONDS = 1.0
FOTMOB_MATCHING_TOLERANCE_MINUTES = 15
FOTMOB_HT_STABLE_DELAY_SECONDS = 45
FOTMOB_SNAPSHOT_OUTBOX_EXPORT_INTERVAL_SECONDS = 300
FOTMOB_SNAPSHOT_OUTBOX_BATCH_SIZE = 100


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
    def halftime_reports_path(self) -> Path:
        return self.root_dir / "data" / "halftime_reports"

    @classmethod
    def from_env(cls, root_dir: Path | None = None) -> "Settings":
        """Build settings without requiring a dotenv file."""

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
