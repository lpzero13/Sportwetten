"""League-to-season-to-match historical pipeline for FotMob.

The pipeline intentionally separates index discovery from detail fetching.  It
is safe to use with local fixtures in tests. Explicit CLI jobs use the
``manual`` network mode; a permanent worker remains behind the stricter
V0.5.1 provider-policy gate.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import threading
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from config import Settings

from .client import FotMobClient
from .canonical import (
    CANONICAL_MATCH_CORE_SCHEMA_VERSION,
    CANONICAL_PARSER_VERSION,
    FotMobCanonicalArchive,
)
from .history_discovery import (
    extract_catalog_names,
    extract_daily_match_index,
    extract_league_metadata,
    extract_match_index,
    extract_seasons,
    season_matches_selector,
    select_reproducible_sample,
    summarize_daily_feed,
)
from .history_models import (
    FotMobMatchIndexRecord,
    FotMobSeasonRef,
    historical_row_from_match,
)
from .history_storage import FotMobHistoricalArchive, FotMobHistoryStore
from .models import FotMobFetchResult


@dataclass(frozen=True, slots=True)
class LeagueDiscoveryResult:
    success: bool
    league_id: str
    league_name: str | None = None
    country: str | None = None
    seasons: tuple[FotMobSeasonRef, ...] = ()
    error: str | None = None


@dataclass(frozen=True, slots=True)
class MatchIndexResult:
    success: bool
    league_id: str
    season_id: str
    season_label: str
    records: tuple[FotMobMatchIndexRecord, ...] = ()
    counts: dict[str, int] | None = None
    error: str | None = None


def _network_mode(settings: Settings) -> str:
    mode = str(getattr(settings, "fotmob_network_mode", "off")).strip().casefold()
    return mode if mode in {"off", "manual", "worker"} else "off"


def manual_history_allowed(settings: Settings) -> bool:
    """Return whether an explicitly started CLI history job may use the network."""

    return bool(
        settings.fotmob_enabled
        and settings.fotmob_history_enabled
        and _network_mode(settings) == "manual"
    )


def worker_history_allowed(settings: Settings) -> bool:
    """Return whether a permanent/background historical worker may use the network."""

    return bool(
        settings.fotmob_enabled
        and settings.fotmob_history_enabled
        and _network_mode(settings) == "worker"
        and settings.fotmob_provider_decision == "PRODUCTION_READY"
        and settings.fotmob_automated_usage == "ACCEPTABLE_FOR_PROJECT"
    )


def historical_automation_allowed(settings: Settings) -> bool:
    """Backward-compatible name for the deliberately stricter worker gate."""

    return worker_history_allowed(settings)


def _history_network_allowed(settings: Settings, execution_mode: str) -> bool:
    mode = str(execution_mode).strip().casefold()
    if mode == "manual":
        return manual_history_allowed(settings)
    if mode == "worker":
        return worker_history_allowed(settings)
    return False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _payload_hash(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def has_halftime_data(match: Any) -> bool:
    """Return true only when FotMob supplied usable FirstHalf metrics."""

    stats = getattr(match, "ht_stats", None)
    return bool(stats is not None and stats.has_any_value())


def is_no_data_result(fetched: FotMobFetchResult | None) -> bool:
    """Identify FotMob's explicit empty-detail response."""

    payload = getattr(fetched, "payload", None)
    if not isinstance(payload, dict) or not payload.get("error"):
        return False
    message = str(payload.get("message") or "").strip().casefold()
    return message in {"data not found", "match data not found"}


def _row_to_index(row: Any) -> FotMobMatchIndexRecord:
    return FotMobMatchIndexRecord(
        provider_match_id=str(row["fotmob_match_id"]),
        league_id=str(row["league_id"]),
        season_id=str(row["season_id"]),
        season_label=str(row["season_label"]),
        kickoff_at=row["kickoff_at"],
        home_team_id=row["home_team_id"],
        home_team_name=str(row["home_team_name"]),
        away_team_id=row["away_team_id"],
        away_team_name=str(row["away_team_name"]),
        round_name=row["round"],
        match_status=row["match_status"],
        league_name=row["league_name"],
        country=row["country_name"] or row["country"],
        country_code=row["country_code"] if "country_code" in row.keys() else None,
        country_name=row["country_name"] if "country_name" in row.keys() else None,
        first_seen_at=row["first_seen_at"],
        provider=str(row["provider"]),
        source_type=str(row["source_type"] or "FRESH_INDEX"),
        source_context=row["source_context"],
        stats_period=row["stats_period"],
        captured_live=bool(row["captured_live"]),
        is_next_day=bool(row["is_next_day"]) if "is_next_day" in row.keys() else False,
        field_provenance=(
            json.loads(str(row["field_provenance_json"]))
            if row["field_provenance_json"]
            else {}
        ),
    )


class FotMobHistoryPipeline:
    """Orchestrate discovery, indexing, deterministic sampling and detail scans."""

    def __init__(
        self,
        settings: Settings,
        database: Any,
        *,
        client: FotMobClient | Any | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.settings = settings
        self.logger = logger or logging.getLogger("tipico.fotmob.history")
        self.store = FotMobHistoryStore(
            database,
            settings.archive_path,
            settings.raw_storage_path if settings.store_fotmob_historical_raw else None,
        )
        self.client = client or FotMobClient(
            base_url=settings.fotmob_base_url,
            api_base_url=settings.fotmob_api_base_url,
            match_details_path=settings.fotmob_match_details_path,
            timeout_seconds=settings.fotmob_history_timeout_seconds,
            max_retries=settings.fotmob_history_max_retries,
            min_request_interval_seconds=None,
            rate_mode=getattr(settings, "fotmob_rate_mode", "ADAPTIVE"),
            initial_rps=getattr(
                settings,
                "fotmob_initial_rps",
                getattr(settings, "fotmob_history_requests_per_second", 5.0),
            ),
            rps_step=getattr(settings, "fotmob_rps_step", 5.0),
            min_rps=getattr(settings, "fotmob_min_rps", 0.5),
            max_rps=getattr(settings, "fotmob_max_rps", 30.0),
            rate_window_requests=getattr(settings, "fotmob_rate_window_requests", 20),
            rate_cooldown_seconds=getattr(settings, "fotmob_rate_cooldown_seconds", 5.0),
            max_error_rate=getattr(settings, "fotmob_max_error_rate", 0.10),
            max_5xx_rate=getattr(settings, "fotmob_max_5xx_rate", 0.05),
            max_timeout_rate=getattr(settings, "fotmob_max_timeout_rate", 0.05),
            max_connection_error_rate=getattr(
                settings, "fotmob_max_connection_error_rate", 0.05
            ),
            max_p95_latency_ms=getattr(settings, "fotmob_max_p95_latency_ms", 3000.0),
            connection_pool_size=getattr(settings, "fotmob_connection_pool_size", 40),
            logger=self.logger,
        )
        self.archive = FotMobHistoricalArchive(settings.archive_path, settings.parquet_compression)
        self.canonical_archive = FotMobCanonicalArchive(
            getattr(settings, "fotmob_archive_path", settings.archive_path / "fotmob"),
            settings.parquet_compression,
        )
        self._archive_lock = threading.RLock()
        known_stable_rps = self.store.known_stable_max_rps(
            confirmations=int(
                getattr(self.settings, "fotmob_performance_stable_confirmations", 2)
            )
        )
        self.logger.info(
            "FotMob historical collector configuration: mode=%s initial_rps=%.2f "
            "rps_step=%.2f max_rps=%.2f workers=%d max_workers=%d pool=%d "
            "known_stable_rps=%s",
            getattr(self.settings, "fotmob_rate_mode", "ADAPTIVE"),
            float(getattr(self.settings, "fotmob_initial_rps", 5.0)),
            float(getattr(self.settings, "fotmob_rps_step", 5.0)),
            float(getattr(self.settings, "fotmob_max_rps", 30.0)),
            int(getattr(self.settings, "fotmob_initial_workers", 10)),
            int(getattr(self.settings, "fotmob_max_workers", 40)),
            int(getattr(self.settings, "fotmob_connection_pool_size", 40)),
            known_stable_rps if known_stable_rps is not None else "unknown",
        )

    def _configured_workers(self, workers: int | None) -> int:
        default_workers = int(
            getattr(
                self.settings,
                "fotmob_initial_workers",
                getattr(self.settings, "fotmob_history_workers", 10),
            )
        )
        max_workers = int(
            getattr(
                self.settings,
                "fotmob_max_workers",
                max(default_workers, getattr(self.settings, "fotmob_history_workers", 10)),
            )
        )
        requested = default_workers if workers is None else int(workers)
        return max(1, min(max_workers, requested))

    def _network_error(self, execution_mode: str) -> str:
        return (
            "FotMob Historical-Netzwerkzugriff ist gesperrt. Für einen bewusst "
            "manuell gestarteten CLI-Job setze FOTMOB_ENABLED=true, "
            "FOTMOB_HISTORY_ENABLED=true und FOTMOB_NETWORK_MODE=manual. "
            f"Der Modus {execution_mode!r} verlangt zusätzlich die Worker-Gates "
            "FOTMOB_PROVIDER_DECISION=PRODUCTION_READY und "
            "FOTMOB_AUTOMATED_USAGE=ACCEPTABLE_FOR_PROJECT."
        )

    def _fetch_json(self, endpoint: str) -> tuple[dict[str, Any] | None, str | None]:
        fetched = self.client.fetch_json(endpoint)
        if not isinstance(fetched, FotMobFetchResult):
            if isinstance(fetched, dict):
                return fetched, None
            return None, "FotMob endpoint returned no mapping payload"
        if not fetched.success or not isinstance(fetched.payload, dict):
            return None, fetched.error or "FotMob endpoint request failed"
        return fetched.payload, None

    def _endpoint(self, template: str, **values: Any) -> str:
        return template.format(**{key: str(value) for key, value in values.items()})

    def discover_league(
        self,
        league_id: str,
        *,
        payload: dict[str, Any] | None = None,
        execution_mode: str = "worker",
    ) -> LeagueDiscoveryResult:
        if payload is None:
            if not _history_network_allowed(self.settings, execution_mode):
                return LeagueDiscoveryResult(
                    False,
                    str(league_id),
                    error=self._network_error(execution_mode),
                )
            payload, error = self._fetch_json(
                self._endpoint(self.settings.fotmob_league_path, league_id=league_id)
            )
            if payload is None:
                return LeagueDiscoveryResult(False, str(league_id), error=error)
        metadata = extract_league_metadata(payload, str(league_id))
        seasons = tuple(extract_seasons(payload, league_id=str(league_id), discovered_at=_now()))
        if not seasons:
            return LeagueDiscoveryResult(
                False,
                str(metadata["league_id"] or league_id),
                metadata["league_name"],
                metadata["country"],
                error="FotMob league payload enthält keine verwertbaren Season-Einträge",
            )
        self.store.upsert_seasons(seasons)
        return LeagueDiscoveryResult(
            True,
            str(metadata["league_id"] or league_id),
            metadata["league_name"],
            metadata["country"],
            seasons,
        )

    def resolve_season(self, league_id: str, selector: str) -> FotMobSeasonRef | None:
        for row in self.store.seasons(league_id):
            if season_matches_selector(row, selector):
                return FotMobSeasonRef(
                    provider=str(row["provider"]),
                    league_id=str(row["league_id"]),
                    season_id=str(row["season_id"]),
                    season_label=str(row["season_label"]),
                    league_name=row["league_name"],
                    country=row["country"],
                    discovered_at=row["discovered_at"],
                )
        return None

    def index_season(
        self,
        league_id: str,
        season: FotMobSeasonRef,
        *,
        payload: dict[str, Any] | None = None,
        execution_mode: str = "worker",
    ) -> MatchIndexResult:
        if payload is None:
            if not _history_network_allowed(self.settings, execution_mode):
                return MatchIndexResult(
                    False,
                    str(league_id),
                    season.season_id,
                    season.season_label,
                    error=self._network_error(execution_mode),
                )
            payload, error = self._fetch_json(
                self._endpoint(
                    self.settings.fotmob_season_path,
                    league_id=league_id,
                    season_id=season.season_id,
                    season_label=season.season_label,
                )
            )
            if payload is None:
                return MatchIndexResult(
                    False, str(league_id), season.season_id, season.season_label, error=error
                )
        records = tuple(
            extract_match_index(
                payload,
                league_id=str(league_id),
                season=season,
                first_seen_at=_now(),
            )
        )
        counts = self.store.upsert_match_index(records)
        self.store.record_fixture_index_run(
            str(league_id),
            season.season_id,
            fixture_count=len(records),
            payload_hash=_payload_hash(payload),
            source_context="DAILY_INDEX",
        )
        return MatchIndexResult(
            True,
            str(league_id),
            season.season_id,
            season.season_label,
            records,
            counts,
        )

    def sample_season(self, league_id: str, season_id: str, count: int = 5) -> list[sqlite3.Row]:
        rows = self.store.match_index(league_id, season_id)
        selected = select_reproducible_sample((_row_to_index(row) for row in rows), count=count)
        self.store.set_sample(league_id, season_id, (item.provider_match_id for item in selected))
        return self.store.sample(league_id, season_id)

    def _fetch_one(
        self,
        league_id: str,
        season_id: str,
        *,
        retry_failed: bool,
        worker_id: str,
        only_sample: bool,
    ) -> tuple[Any, FotMobFetchResult | None, str | None] | None:
        row = self.store.claim_next(
            league_id,
            season_id,
            worker_id=worker_id,
            retry_failed=retry_failed,
            max_attempts=self.settings.fotmob_history_max_retry_attempts,
            stale_minutes=self.settings.fotmob_history_stale_minutes,
            only_sample=only_sample,
        )
        if row is None:
            return None
        try:
            fetched = self.client.fetch_match_details(str(row["fotmob_match_id"]))
            if not isinstance(fetched, FotMobFetchResult):
                fetched = FotMobFetchResult(success=True, match=fetched)
            if not fetched.success or fetched.match is None:
                return row, fetched, fetched.error or "FotMob detail request failed"
            return row, fetched, None
        except Exception as exc:  # pragma: no cover - defensive worker boundary
            return row, None, str(exc)

    def _normalized_row(self, row: Any, fetched: FotMobFetchResult) -> dict[str, Any]:
        index = _row_to_index(row)
        raw_path: str | None = None
        payload_hash = _payload_hash(fetched.payload)
        if (
            self.settings.store_fotmob_historical_raw
            and isinstance(fetched.payload, dict)
        ):
            raw_path, payload_hash = self.store.save_raw_payload(
                fetched.payload,
                league_id=index.league_id,
                season_label=index.season_label,
                provider_match_id=index.provider_match_id,
            )
        normalized = historical_row_from_match(
            index,
            fetched.match,
            fetched_at=_now(),
            raw_payload_path=raw_path,
            source_type="FRESH_FETCH",
            source_context="HISTORY_DETAIL",
            stats_period="FULL_MATCH",
            captured_live=False,
        )
        normalized["payload_hash"] = payload_hash
        return normalized

    def _write_canonical(
        self,
        row: Any,
        fetched: FotMobFetchResult,
        *,
        fetched_at: str | None = None,
        source_context: str = "DAILY_DETAIL",
    ) -> dict[str, Any]:
        if fetched.match is None:
            raise ValueError("FotMob detail has no normalized match")
        result = self.canonical_archive.write_match(
            _row_to_index(row),
            fetched.match,
            fetched.payload,
            fetched_at=fetched_at or _now(),
            source_type="FRESH_FETCH",
            source_context=source_context,
            stats_period="FULL_MATCH",
            captured_live=False,
        )
        # The canonical writer returns one deterministic path per dataset.  Do
        # not use a glob here: the SQLite archive index must point to the file
        # written by this fetch, even when a deployment contains old runs.
        core_path = next(
            (
                path for path in result.get("paths", [])
                if "match_core" in Path(str(path)).parts
            ),
            None,
        )
        if core_path:
            self.store.mark_archive_written(
                str(row["fotmob_match_id"]),
                str(core_path),
                payload_hash=result.get("payload_hash"),
                schema_version=CANONICAL_MATCH_CORE_SCHEMA_VERSION,
                parser_version=CANONICAL_PARSER_VERSION,
                provider=str(row["provider"]),
                source_type="FRESH_FETCH",
                source_context=source_context,
                stats_period="FULL_MATCH",
                captured_live=False,
            )
        return result

    def _fetch_specific(
        self,
        provider_match_id: str,
        *,
        worker_id: str,
        retry_failed: bool,
        refresh_existing: bool,
    ) -> tuple[Any, FotMobFetchResult | None, str | None] | None:
        row = self.store.claim_match(
            str(provider_match_id),
            worker_id=worker_id,
            retry_failed=retry_failed,
            max_attempts=self.settings.fotmob_history_max_retry_attempts,
            stale_minutes=self.settings.fotmob_history_stale_minutes,
            refresh_existing=refresh_existing,
        )
        if row is None:
            return None
        try:
            fetched = self.client.fetch_match_details(str(provider_match_id))
            if not isinstance(fetched, FotMobFetchResult):
                fetched = FotMobFetchResult(success=True, match=fetched)
            if not fetched.success or fetched.match is None:
                return row, fetched, fetched.error or "FotMob detail request failed"
            return row, fetched, None
        except Exception as exc:  # pragma: no cover - defensive worker boundary
            return row, None, str(exc)

    def fetch_details_for_ids(
        self,
        provider_match_ids: list[str] | tuple[str, ...],
        *,
        workers: int | None = None,
        retry_failed: bool = False,
        refresh_existing: bool = False,
        require_halftime_stats: bool = False,
        execution_mode: str = "manual",
    ) -> dict[str, Any]:
        """Fetch only the fixtures selected by a date-range job.

        This keeps a date job resumable and prevents a small UI-selected range
        from accidentally draining an entire season queue.
        """

        if not _history_network_allowed(self.settings, execution_mode):
            return {
                "status": "BLOCKED_BY_POLICY",
                "requested": len(set(str(item) for item in provider_match_ids)),
                "fetched": 0,
                "partial": 0,
                "failed": 0,
                "errors": 0,
            "skipped": 0,
            "skipped_no_halftime": 0,
            "skipped_no_data": 0,
            "canonical_files": [],
                "historical_files": [],
                "period_stats_rows": 0,
                "shot_rows": 0,
                "event_rows": 0,
                "archive_bytes": 0,
            "canonical_bytes": 0,
            "workers": self._configured_workers(workers),
            "error": self._network_error(execution_mode),
            }
        ids = list(dict.fromkeys(str(item) for item in provider_match_ids if str(item).strip()))
        workers = self._configured_workers(workers)
        worker_id = f"daily-{uuid.uuid4().hex[:12]}"
        fetched_count = partial_count = failed_count = errors = skipped = 0
        skipped_no_halftime = skipped_no_data = 0
        canonical_files: list[str] = []
        historical_files: list[str] = []
        period_rows = shot_rows = event_rows = archive_bytes = canonical_bytes = 0
        successful_ids: set[str] = set()
        buffer: list[dict[str, Any]] = []

        def flush() -> None:
            nonlocal buffer, archive_bytes
            if not buffer:
                return
            with self._archive_lock:
                result = self.archive.write_batch(self.store, buffer)
            historical_files.extend(str(path) for path in result.get("paths", []))
            archive_bytes += sum(
                Path(path).stat().st_size
                for path in result.get("paths", [])
                if Path(path).exists()
            )
            for normalized in buffer:
                self.store.mark_success(
                    str(normalized["fotmob_match_id"]),
                    data_quality=str(normalized["data_quality"]),
                    ml_eligible=bool(normalized["ml_eligible"]),
                    parser_version=str(normalized["parser_version"]),
                    schema_version=str(normalized["schema_version"]),
                    raw_payload_path=normalized.get("raw_payload_path"),
                    payload_hash=normalized.get("payload_hash"),
                    second_half_goals=normalized.get("second_half_goals"),
                    second_half_goal_class=normalized.get("second_half_goal_class"),
                    worker_id=worker_id,
                    source_type=str(normalized.get("source_type", "FRESH_FETCH")),
                    source_context=normalized.get("source_context"),
                    stats_period=normalized.get("stats_period"),
                    captured_live=bool(normalized.get("captured_live")),
                    field_provenance=normalized.get("field_provenance_json"),
                )
            buffer = []

        def handle(result: tuple[Any, FotMobFetchResult | None, str | None] | None) -> None:
            nonlocal fetched_count, partial_count, failed_count, errors, skipped
            nonlocal skipped_no_halftime, skipped_no_data
            nonlocal period_rows, shot_rows, event_rows, canonical_bytes
            if result is None:
                skipped += 1
                return
            row, fetched, error = result
            if error or fetched is None or fetched.match is None:
                if is_no_data_result(fetched):
                    skipped_no_data += 1
                    self.store.mark_skipped_no_data(
                        str(row["fotmob_match_id"]),
                        reason=str((fetched.payload or {}).get("message") or error or "FotMob Detaildaten nicht vorhanden"),
                        worker_id=worker_id,
                    )
                    return
                errors += 1
                failure_status = self.store.mark_failure(
                    str(row["fotmob_match_id"]),
                    error or "FotMob detail request failed",
                    max_attempts=self.settings.fotmob_history_max_retry_attempts,
                    worker_id=worker_id,
                )
                if failure_status == "FAILED":
                    failed_count += 1
                return
            if require_halftime_stats and not has_halftime_data(fetched.match):
                skipped_no_halftime += 1
                self.store.mark_skipped_no_halftime(
                    str(row["fotmob_match_id"]),
                    worker_id=worker_id,
                )
                return
            try:
                canonical = self._write_canonical(
                    row,
                    fetched,
                    source_context="DAILY_DETAIL",
                )
                canonical_files.extend(str(path) for path in canonical.get("paths", []))
                period_rows += int(canonical.get("period_stats_rows", 0))
                shot_rows += int(canonical.get("shot_rows", 0))
                event_rows += int(canonical.get("event_rows", 0))
                canonical_bytes += int(canonical.get("bytes", 0))
                normalized = self._normalized_row(row, fetched)
                buffer.append(normalized)
                if str(normalized.get("data_quality")) == "COMPLETE":
                    fetched_count += 1
                else:
                    partial_count += 1
                successful_ids.add(str(row["fotmob_match_id"]))
            except Exception as exc:
                errors += 1
                failure_status = self.store.mark_failure(
                    str(row["fotmob_match_id"]),
                    f"normalization/archive preparation failed: {exc}",
                    max_attempts=self.settings.fotmob_history_max_retry_attempts,
                    worker_id=worker_id,
                )
                if failure_status == "FAILED":
                    failed_count += 1
            if len(buffer) >= max(1, int(self.settings.fotmob_history_batch_size)):
                flush()

        try:
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="fotmob-daily") as executor:
                # Keep the executor queue bounded.  Submitting every detail
                # request at once is cheap for a small sample, but a
                # two-year all-leagues backfill can create hundreds of
                # thousands of Future objects before the first one finishes.
                pending: set[Any] = set()
                id_iter = iter(ids)

                def submit_available() -> None:
                    while len(pending) < max(workers * 4, workers):
                        try:
                            provider_match_id = next(id_iter)
                        except StopIteration:
                            return
                        pending.add(
                            executor.submit(
                                self._fetch_specific,
                                provider_match_id,
                                worker_id=worker_id,
                                retry_failed=retry_failed,
                                refresh_existing=refresh_existing,
                            )
                        )

                submit_available()
                while pending:
                    done, pending = wait(pending, return_when=FIRST_COMPLETED)
                    for future in done:
                        handle(future.result())
                    submit_available()
            flush()
        finally:
            self.store.release_worker(worker_id)
        return {
            "status": "PASS" if failed_count == 0 and errors == 0 else "PARTIAL",
            "requested": len(ids),
            "fetched": fetched_count,
            "partial": partial_count,
            "failed": failed_count,
            "errors": errors,
            "skipped": skipped,
            "skipped_no_halftime": skipped_no_halftime,
            "skipped_no_data": skipped_no_data,
            "canonical_files": sorted(set(canonical_files)),
            "historical_files": sorted(set(historical_files)),
            "period_stats_rows": period_rows,
            "shot_rows": shot_rows,
            "event_rows": event_rows,
            "archive_bytes": archive_bytes,
            "canonical_bytes": canonical_bytes,
            "workers": workers,
            "successful_ids": sorted(successful_ids),
            "access": getattr(self.client, "metrics_snapshot", lambda: {})(),
        }

    def fetch_details(
        self,
        league_id: str,
        season_id: str,
        *,
        workers: int | None = None,
        retry_failed: bool = False,
        only_sample: bool = False,
        limit: int | None = None,
        batch_size: int | None = None,
        execution_mode: str = "worker",
    ) -> dict[str, Any]:
        if not _history_network_allowed(self.settings, execution_mode):
            return {
                "status": "BLOCKED_BY_POLICY",
                "league_id": str(league_id),
                "season_id": str(season_id),
                "error": self._network_error(execution_mode),
            }
        workers = self._configured_workers(workers)
        batch_size = max(1, int(batch_size or self.settings.fotmob_history_batch_size))
        worker_id = f"history-{uuid.uuid4().hex[:12]}"
        target_rows = self.store.match_index(
            str(league_id),
            str(season_id),
            only_sample=only_sample,
        )
        target_total = len(target_rows)
        if limit is not None:
            target_total = min(target_total, max(0, int(limit)))
        started_monotonic = time.monotonic()
        processed = fetched_count = partial_count = failed_count = error_count = 0
        archive_files: list[str] = []
        buffer: list[dict[str, Any]] = []
        exhausted = False

        def flush() -> None:
            nonlocal buffer, fetched_count, partial_count
            if not buffer:
                return
            with self._archive_lock:
                archive_result = self.archive.write_batch(self.store, buffer)
            archive_files.extend(str(path) for path in archive_result.get("paths", []))
            for normalized in buffer:
                status = self.store.mark_success(
                    str(normalized["fotmob_match_id"]),
                    data_quality=str(normalized["data_quality"]),
                    ml_eligible=bool(normalized["ml_eligible"]),
                    parser_version=str(normalized["parser_version"]),
                    schema_version=str(normalized["schema_version"]),
                    raw_payload_path=normalized.get("raw_payload_path"),
                    payload_hash=normalized.get("payload_hash"),
                    second_half_goals=normalized.get("second_half_goals"),
                    second_half_goal_class=normalized.get("second_half_goal_class"),
                    worker_id=worker_id,
                    source_type=str(normalized.get("source_type", "FRESH_FETCH")),
                    source_context=normalized.get("source_context"),
                    stats_period=normalized.get("stats_period"),
                    captured_live=bool(normalized.get("captured_live")),
                    field_provenance=normalized.get("field_provenance_json"),
                )
                if status == "FETCHED":
                    fetched_count += 1
                else:
                    partial_count += 1
            buffer = []

        def progress_snapshot(status: dict[str, Any] | None = None) -> dict[str, Any]:
            current_status = status or self.store.status(
                league_id,
                season_id,
                only_sample=only_sample,
            )
            elapsed_seconds = max(0.0, time.monotonic() - started_monotonic)
            remaining = int(current_status.get("remaining", 0))
            completed = max(0, int(current_status.get("total", target_total)) - remaining)
            rate = processed / elapsed_seconds if elapsed_seconds > 0 else 0.0
            eta_seconds = (remaining / rate) if rate > 0 and remaining > 0 else None
            fraction = (completed / target_total) if target_total > 0 else 1.0
            return {
                "processed": processed,
                "completed": min(target_total, completed),
                "target": target_total,
                "remaining": remaining,
                "fraction": round(min(1.0, max(0.0, fraction)), 4),
                "elapsed_seconds": round(elapsed_seconds, 3),
                "rate_per_second": round(rate, 4),
                "eta_seconds": round(eta_seconds, 3) if eta_seconds is not None else None,
            }

        def submit(executor: ThreadPoolExecutor, pending: set[Any]) -> None:
            if limit is not None and processed + len(pending) >= limit:
                return
            future = executor.submit(
                self._fetch_one,
                str(league_id),
                str(season_id),
                retry_failed=retry_failed,
                worker_id=worker_id,
                only_sample=only_sample,
            )
            pending.add(future)

        try:
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="fotmob-history") as executor:
                pending: set[Any] = set()
                for _ in range(workers):
                    submit(executor, pending)
                while pending:
                    done, pending = wait(pending, return_when=FIRST_COMPLETED)
                    for future in done:
                        result = future.result()
                        if result is None:
                            exhausted = True
                            continue
                        row, fetched, error = result
                        processed += 1
                        if error or fetched is None or fetched.match is None:
                            error_count += 1
                            failure_status = self.store.mark_failure(
                                str(row["fotmob_match_id"]),
                                error or "FotMob detail request failed",
                                max_attempts=self.settings.fotmob_history_max_retry_attempts,
                                worker_id=worker_id,
                            )
                            if failure_status == "FAILED":
                                failed_count += 1
                        else:
                            try:
                                self._write_canonical(row, fetched)
                                buffer.append(self._normalized_row(row, fetched))
                            except Exception as exc:
                                error_count += 1
                                failure_status = self.store.mark_failure(
                                    str(row["fotmob_match_id"]),
                                    f"normalization/archive preparation failed: {exc}",
                                    max_attempts=self.settings.fotmob_history_max_retry_attempts,
                                    worker_id=worker_id,
                                )
                                if failure_status == "FAILED":
                                    failed_count += 1
                        if len(buffer) >= batch_size:
                            flush()
                            progress = progress_snapshot()
                            self.logger.info(
                                "FotMob history progress %s/%s (%.1f%%), ETA=%s s",
                                progress["completed"],
                                progress["target"],
                                progress["fraction"] * 100,
                                progress["eta_seconds"] if progress["eta_seconds"] is not None else "unknown",
                            )
                    while not exhausted and len(pending) < workers:
                        before = len(pending)
                        submit(executor, pending)
                        if len(pending) == before:
                            exhausted = True
                            break
            flush()
        except KeyboardInterrupt:
            self.store.release_worker(worker_id)
            raise
        finally:
            self.store.release_worker(worker_id)
        status = self.store.status(league_id, season_id, only_sample=only_sample)
        progress = progress_snapshot(status)
        return {
            "status": "PASS" if failed_count == 0 else "PARTIAL",
            "league_id": str(league_id),
            "season_id": str(season_id),
            "workers": workers,
            "processed": processed,
            "fetched": fetched_count,
            "partial": partial_count,
            "failed": failed_count,
            "errors": error_count,
            "remaining": status["remaining"],
            "failed_queue": status.get("failed", 0),
            "elapsed_seconds": progress["elapsed_seconds"],
            "eta_seconds": progress["eta_seconds"],
            "progress": progress,
            "archive_files": sorted(set(archive_files)),
            "access": getattr(self.client, "metrics_snapshot", lambda: {})(),
        }

    @staticmethod
    def _coerce_date(value: date | str) -> date:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        try:
            return date.fromisoformat(str(value).strip())
        except ValueError as exc:
            raise ValueError(f"Ungültiges Datum: {value!r}; erwartet YYYY-MM-DD") from exc

    @staticmethod
    def _season_window(season_label: str) -> tuple[date, date] | None:
        match = re.search(r"(\d{4})\s*[/\-]\s*(\d{2,4})", str(season_label or ""))
        if not match:
            return None
        first = int(match.group(1))
        second_text = match.group(2)
        second = int(second_text) if len(second_text) == 4 else (first // 100) * 100 + int(second_text)
        if second < first:
            second = first + 1
        # Football seasons are treated as July--June for selecting which
        # provider season page to request.  The fixture itself is still
        # filtered by its exact UTC kickoff, so unusual cup/season dates are
        # never silently included.
        return date(first, 7, 1), date(second, 6, 30)

    def _daily_feed_endpoint(self, observation_date: date) -> str:
        return self._endpoint(
            getattr(
                self.settings,
                "fotmob_daily_matches_path",
                "/data/matches?date={date}&timezone={timezone}&ccode3={ccode3}"
                "&includeNextDayLateNight=true",
            ),
            date=observation_date.strftime("%Y%m%d"),
            timezone=quote(
                str(getattr(self.settings, "fotmob_daily_timezone", "Europe/Berlin")),
                safe="",
            ),
            ccode3=quote(
                str(getattr(self.settings, "fotmob_daily_ccode3", "DEU")),
                safe="",
            ),
        )

    def _all_leagues_date_range(
        self,
        start: date,
        end: date,
        *,
        fetch_details: bool,
        workers: int | None,
        execution_mode: str,
    ) -> dict[str, Any]:
        """Load every match returned by FotMob's selected-day feed."""

        day_count = (end - start).days + 1
        scope_league = "ALL"
        scope_season = "DAILY_FEED"
        # Keep only provider IDs between the feed and detail phases.  A
        # two-year all-leagues run can contain hundreds of thousands of
        # records; retaining every dataclass object here made the old path
        # grow into multiple gigabytes before the first detail request.
        daily_records: dict[str, list[str]] = {}
        daily_feed_summaries: dict[str, dict[str, int]] = {}
        unique_match_ids: set[str] = set()
        observed_league_ids: set[str] = set()
        observed_country_codes: set[str] = set()
        observed_seasons: set[str] = set()
        index_errors: list[str] = []
        failed_days: set[str] = set()
        warnings: list[str] = []
        country_names: dict[str, str] = {}
        league_names: dict[str, str] = {}

        catalog_endpoint = self._endpoint(
            getattr(
                self.settings,
                "fotmob_all_leagues_path",
                "/data/allLeagues?locale={locale}&country={country}",
            ),
            locale=quote(str(getattr(self.settings, "fotmob_daily_locale", "de")), safe=""),
            country=quote(str(getattr(self.settings, "fotmob_daily_ccode3", "DEU")), safe=""),
        )
        catalog_payload, catalog_error = self._fetch_json(catalog_endpoint)
        if catalog_payload is not None:
            catalog_names = extract_catalog_names(catalog_payload)
            country_names = catalog_names["countries"]
            league_names = catalog_names["leagues"]
        elif catalog_error:
            warnings.append(f"Länder-/Liga-Katalog nicht geladen: {catalog_error}")

        for offset in range(day_count):
            observation_date = start + timedelta(days=offset)
            day = observation_date.isoformat()
            endpoint = self._daily_feed_endpoint(observation_date)
            payload, error = self._fetch_json(endpoint)
            if payload is None:
                message = f"{day}: {error or 'FotMob-Tagesfeed konnte nicht geladen werden'}"
                index_errors.append(message)
                failed_days.add(day)
                self.store.record_daily_load_run(
                    day,
                    scope_league,
                    season_id=scope_season,
                    status="ERROR",
                    source_endpoint=endpoint,
                    error=error,
                )
                continue

            fetched_at = _now()
            feed_summary = summarize_daily_feed(payload)
            daily_feed_summaries[day] = feed_summary
            records = extract_daily_match_index(
                payload,
                observation_date=observation_date,
                first_seen_at=fetched_at,
                country_names=country_names,
                league_names=league_names,
            )
            daily_records[day] = [record.provider_match_id for record in records]
            unique_match_ids.update(record.provider_match_id for record in records)
            observed_league_ids.update(str(record.league_id) for record in records if record.league_id)
            observed_country_codes.update(
                str(record.country_code or record.country or "")
                for record in records
                if record.country_code or record.country
            )
            observed_seasons.update(str(record.season_label) for record in records if record.season_label)
            self.store.upsert_match_index(records)
            self.store.upsert_seasons(
                FotMobSeasonRef(
                    provider=record.provider,
                    league_id=record.league_id,
                    season_id=record.season_id,
                    season_label=record.season_label,
                    league_name=record.league_name,
                    country=record.country_name or record.country,
                    discovered_at=fetched_at,
                )
                for record in records
            )
            self.store.upsert_daily_index(
                records,
                observation_date=day,
                source_endpoint=endpoint,
                payload_hash=_payload_hash(payload),
                fetched_at=fetched_at,
            )
            self.store.record_daily_load_run(
                day,
                scope_league,
                season_id=scope_season,
                status="COMPLETE",
                fixture_count=len(records),
                selected_count=len(records),
                feed_group_count=feed_summary["feed_group_count"],
                feed_entry_count=feed_summary["feed_entry_count"],
                feed_unique_count=feed_summary["feed_unique_count"],
                next_day_count=feed_summary["next_day_count"],
                duplicates_removed_count=feed_summary["duplicates_removed_count"],
                payload_hash=_payload_hash(payload),
                source_endpoint=endpoint,
                fetched_at=fetched_at,
            )

        detail_result: dict[str, Any] = {
            "status": "SKIPPED",
            "requested": 0,
            "fetched": 0,
            "partial": 0,
            "failed": 0,
            "errors": 0,
            "skipped": 0,
            "skipped_no_halftime": 0,
        }
        if fetch_details and unique_match_ids:
            detail_result = self.fetch_details_for_ids(
                sorted(unique_match_ids),
                workers=workers or getattr(self.settings, "fotmob_history_workers", 1),
                refresh_existing=True,
                require_halftime_stats=True,
                execution_mode=execution_mode,
            )

        status_by_id: dict[str, str] = {}
        if daily_records:
            catalog_rows = self.store.daily_index(
                start_date=start.isoformat(),
                end_date=end.isoformat(),
                limit=max(500, len(unique_match_ids) * 2),
            )
            status_by_id = {
                str(row["fotmob_match_id"]): str(row["detail_status"] or "NOT_FETCHED")
                for row in catalog_rows
            }
        successful_ids = set(str(item) for item in detail_result.get("successful_ids", []))
        skipped_ids = {
            match_id
            for match_id, status in status_by_id.items()
            if status == "SKIPPED_NO_HALFTIME"
        }
        for day, record_ids in daily_records.items():
            day_ids = set(record_ids)
            detail_count = len(day_ids.intersection(successful_ids))
            skipped_count = len(day_ids.intersection(skipped_ids))
            if not fetch_details:
                # An index-only refresh must not erase the detail counters
                # from a completed canary.  Reconstruct them from the
                # terminal match index instead of treating all details as
                # newly skipped.
                detail_count = sum(
                    status_by_id.get(match_id) in {"FETCHED", "PARTIAL"}
                    for match_id in day_ids
                )
            detail_status = str(detail_result.get("status") or "SKIPPED")
            run_status = (
                "BLOCKED_BY_POLICY"
                if detail_status == "BLOCKED_BY_POLICY"
                else "PARTIAL"
                if detail_status in {"PARTIAL", "ERROR"} or day in failed_days
                else "COMPLETE"
            )
            self.store.record_daily_load_run(
                day,
                scope_league,
                season_id=scope_season,
                status=run_status,
                fixture_count=len(record_ids),
                selected_count=len(record_ids),
                detail_count=detail_count,
                skipped_no_halftime_count=skipped_count,
                feed_group_count=daily_feed_summaries[day]["feed_group_count"],
                feed_entry_count=daily_feed_summaries[day]["feed_entry_count"],
                feed_unique_count=daily_feed_summaries[day]["feed_unique_count"],
                next_day_count=daily_feed_summaries[day]["next_day_count"],
                duplicates_removed_count=daily_feed_summaries[day]["duplicates_removed_count"],
            )

        detail_status = str(detail_result.get("status") or "SKIPPED")
        if index_errors or detail_status in {"PARTIAL", "ERROR"}:
            status = "PARTIAL"
        elif detail_status == "BLOCKED_BY_POLICY":
            status = "BLOCKED_BY_POLICY"
        else:
            status = "PASS"
        return {
            "status": status,
            "scope": "ALL_LEAGUES",
            "from_date": start.isoformat(),
            "to_date": end.isoformat(),
            "league_id": None,
            "league_name": None,
            "country": None,
            "country_code": None,
            "country_name": None,
            "days": day_count,
            "leagues": len(observed_league_ids),
            "countries": len(observed_country_codes),
            "seasons": sorted(observed_seasons),
            "fixtures": sum(len(records) for records in daily_records.values()),
            "unique_fixtures": len(unique_match_ids),
            "daily_index_rows": sum(len(records) for records in daily_records.values()),
            "feed": {
                key: sum(item[key] for item in daily_feed_summaries.values())
                for key in (
                    "feed_group_count",
                    "feed_entry_count",
                    "feed_unique_count",
                    "next_day_count",
                    "duplicates_removed_count",
                    "invalid_entry_count",
                )
            },
            "daily_feed": daily_feed_summaries,
            "details": detail_result,
            "errors": index_errors,
            "warnings": warnings,
            "country_catalog": {
                "status": "PASS" if catalog_payload is not None else "PARTIAL",
                "endpoint": catalog_endpoint,
                "countries": len(country_names),
                "leagues": len(league_names),
                "error": catalog_error,
            },
            "access": getattr(self.client, "metrics_snapshot", lambda: {})(),
        }

    def load_date_range(
        self,
        start_date: date | str,
        end_date: date | str,
        *,
        league_id: str | None = None,
        fetch_details: bool = True,
        workers: int | None = None,
        execution_mode: str = "manual",
    ) -> dict[str, Any]:
        """Load an inclusive date range from FotMob's daily feed.

        With no explicit ``league_id`` this is an all-league load: every fixture
        returned for each selected FotMob day is indexed, independent of country,
        league or kickoff time.  Detail rows without usable FirstHalf metrics
        are deliberately skipped.  Passing a league id keeps the older
        league/season-page path available for legacy CLI jobs.
        """

        start = self._coerce_date(start_date)
        end = self._coerce_date(end_date)
        if end < start:
            raise ValueError("Das Enddatum darf nicht vor dem Startdatum liegen.")
        day_count = (end - start).days + 1
        if day_count > 3660:
            raise ValueError("Der Datumsbereich ist auf 10 Jahre begrenzt.")
        if league_id is None:
            if not _history_network_allowed(self.settings, execution_mode):
                return {
                    "status": "BLOCKED_BY_POLICY",
                    "scope": "ALL_LEAGUES",
                    "from_date": start.isoformat(),
                    "to_date": end.isoformat(),
                    "league_id": None,
                    "days": day_count,
                    "fixtures": 0,
                    "unique_fixtures": 0,
                    "details": {},
                    "error": self._network_error(execution_mode),
                }
            return self._all_leagues_date_range(
                start,
                end,
                fetch_details=fetch_details,
                workers=workers,
                execution_mode=execution_mode,
            )
        target_league = str(league_id or getattr(self.settings, "fotmob_history_league_id", "54"))
        if not _history_network_allowed(self.settings, execution_mode):
            return {
                "status": "BLOCKED_BY_POLICY",
                "from_date": start.isoformat(),
                "to_date": end.isoformat(),
                "league_id": target_league,
                "days": day_count,
                "fixtures": 0,
                "details": {},
                "error": self._network_error(execution_mode),
            }

        discovery = self.discover_league(target_league, execution_mode=execution_mode)
        if not discovery.success:
            return {
                "status": "ERROR",
                "from_date": start.isoformat(),
                "to_date": end.isoformat(),
                "league_id": target_league,
                "days": day_count,
                "fixtures": 0,
                "details": {},
                "error": discovery.error,
            }

        relevant_seasons = [
            season
            for season in discovery.seasons
            if self._season_window(season.season_label) is None
            or (
                self._season_window(season.season_label)[1] >= start
                and self._season_window(season.season_label)[0] <= end
            )
        ]
        day_records: dict[str, dict[str, FotMobMatchIndexRecord]] = {}
        season_day_records: dict[tuple[str, str], dict[str, dict[str, FotMobMatchIndexRecord]]] = {}
        season_errors: list[str] = []
        indexed_seasons: list[str] = []
        for season in relevant_seasons:
            endpoint = self._endpoint(
                self.settings.fotmob_season_path,
                league_id=target_league,
                season_id=season.season_id,
                season_label=season.season_label,
            )
            payload, error = self._fetch_json(endpoint)
            if payload is None:
                message = f"{season.season_label}: {error or 'FotMob Season konnte nicht geladen werden'}"
                season_errors.append(message)
                for offset in range(day_count):
                    day = (start + timedelta(days=offset)).isoformat()
                    self.store.record_daily_load_run(
                        day,
                        target_league,
                        season_id=season.season_id,
                        status="ERROR",
                        source_endpoint=endpoint,
                        error=error,
                    )
                continue
            fetched_at = _now()
            records = tuple(
                extract_match_index(
                    payload,
                    league_id=target_league,
                    season=season,
                    first_seen_at=fetched_at,
                )
            )
            self.store.upsert_match_index(records)
            self.store.record_fixture_index_run(
                target_league,
                season.season_id,
                fixture_count=len(records),
                payload_hash=_payload_hash(payload),
                run_date=end.isoformat(),
                fetched_at=fetched_at,
                source_context="DAILY_INDEX",
            )
            indexed_seasons.append(season.season_id)
            selected_by_day: dict[str, dict[str, FotMobMatchIndexRecord]] = {}
            for record in records:
                kickoff_day = str(record.kickoff_at or "")[:10]
                if not kickoff_day:
                    continue
                try:
                    kickoff_date = date.fromisoformat(kickoff_day)
                except ValueError:
                    continue
                if not (start <= kickoff_date <= end):
                    continue
                selected_by_day.setdefault(kickoff_day, {})[record.provider_match_id] = record
                day_records.setdefault(kickoff_day, {})[record.provider_match_id] = record
            season_day_records[(season.season_id, endpoint)] = selected_by_day
            for offset in range(day_count):
                day = (start + timedelta(days=offset)).isoformat()
                selected = list(selected_by_day.get(day, {}).values())
                self.store.upsert_daily_index(
                    selected,
                    observation_date=day,
                    source_endpoint=endpoint,
                    payload_hash=_payload_hash(payload),
                    fetched_at=fetched_at,
                )
                self.store.record_daily_load_run(
                    day,
                    target_league,
                    season_id=season.season_id,
                    status="COMPLETE",
                    fixture_count=len(selected),
                    selected_count=len(selected),
                    payload_hash=_payload_hash(payload),
                    source_endpoint=endpoint,
                    fetched_at=fetched_at,
                )

        if not relevant_seasons:
            for offset in range(day_count):
                day = (start + timedelta(days=offset)).isoformat()
                self.store.record_daily_load_run(
                    day,
                    target_league,
                    season_id="",
                    status="NO_SEASON",
                    error="Kein bekannter FotMob-Season-Zeitraum deckt dieses Datum ab.",
                )

        selected_records = [record for records in day_records.values() for record in records.values()]
        detail_result: dict[str, Any] = {
            "status": "SKIPPED",
            "requested": 0,
            "fetched": 0,
            "partial": 0,
            "failed": 0,
            "errors": 0,
            "skipped": 0,
        }
        if fetch_details and selected_records:
            detail_result = self.fetch_details_for_ids(
                [record.provider_match_id for record in selected_records],
                workers=workers or getattr(self.settings, "fotmob_history_workers", 1),
                refresh_existing=True,
                require_halftime_stats=True,
                execution_mode=execution_mode,
            )
            successful_ids = set(detail_result.get("successful_ids", []))
            for (season_id, _endpoint), records_by_day in season_day_records.items():
                for day, records in records_by_day.items():
                    detail_count = len(successful_ids.intersection(records))
                    run_status = (
                        "BLOCKED_BY_POLICY"
                        if detail_result.get("status") == "BLOCKED_BY_POLICY"
                        else "COMPLETE"
                        if detail_result.get("status") in {"PASS", "SKIPPED"}
                        else "PARTIAL"
                    )
                    self.store.record_daily_load_run(
                        day,
                        target_league,
                        season_id=season_id,
                        status=run_status,
                        fixture_count=len(records),
                        selected_count=len(records),
                        detail_count=detail_count,
                        payload_hash=None,
                    )
        detail_status = str(detail_result.get("status") or "SKIPPED")
        if season_errors or detail_status in {"PARTIAL", "ERROR", "BLOCKED_BY_POLICY"}:
            status = "PARTIAL" if detail_status != "BLOCKED_BY_POLICY" else "BLOCKED_BY_POLICY"
        else:
            status = "PASS"
        country_code = str(discovery.country or "").strip().upper() or None
        country_names = {
            "GER": "Deutschland",
            "DEU": "Deutschland",
            "AUT": "Österreich",
            "ENG": "England",
            "ESP": "Spanien",
            "FRA": "Frankreich",
            "ITA": "Italien",
            "NED": "Niederlande",
        }
        return {
            "status": status,
            "from_date": start.isoformat(),
            "to_date": end.isoformat(),
            "league_id": target_league,
            "league_name": discovery.league_name,
            "country": discovery.country,
            "country_code": country_code,
            "country_name": country_names.get(country_code or "", discovery.country),
            "days": day_count,
            "seasons": indexed_seasons,
            "fixtures": len(selected_records),
            "daily_index_rows": len(selected_records),
            "details": detail_result,
            "errors": season_errors,
            "error": detail_result.get("error") if detail_status == "BLOCKED_BY_POLICY" else None,
            "access": getattr(self.client, "metrics_snapshot", lambda: {})(),
        }

    def status(self, league_id: str, season_id: str) -> dict[str, Any]:
        return self.store.status(league_id, season_id)

    def run_performance_probe(
        self,
        start_date: date | str,
        end_date: date | str,
        *,
        requests_per_level: int | None = None,
        worker_levels: tuple[int, ...] | list[int] | None = None,
        execution_mode: str = "manual",
    ) -> dict[str, Any]:
        """Run the finite V0.5.6 throughput probe with this pipeline's client."""

        from .performance import FotMobPerformanceProbe

        return FotMobPerformanceProbe(self, logger=self.logger).run(
            start_date,
            end_date,
            requests_per_level=requests_per_level,
            worker_levels=worker_levels,
            execution_mode=execution_mode,
        )

    def run_max_throughput_probe(
        self,
        start_date: date | str,
        end_date: date | str,
        *,
        requests_per_level: int = 100,
        critical_requests: int = 250,
        max_target_rps: float = 100.0,
        worker_levels: tuple[int, ...] | list[int] | None = None,
        include_worker_50: bool = False,
        execution_mode: str = "manual",
    ) -> dict[str, Any]:
        """Run the finite V0.5.6.1 max-throughput/bottleneck probe."""

        from .max_throughput import FotMobMaxThroughputProbe

        return FotMobMaxThroughputProbe(self, logger=self.logger).run(
            start_date,
            end_date,
            requests_per_level=requests_per_level,
            critical_requests=critical_requests,
            max_target_rps=max_target_rps,
            worker_levels=worker_levels,
            include_worker_50=include_worker_50,
            execution_mode=execution_mode,
        )
