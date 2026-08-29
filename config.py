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
