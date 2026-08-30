"""League-to-season-to-match historical pipeline for FotMob.

The pipeline intentionally separates index discovery from detail fetching.  It
is safe to use with local fixtures in tests, while the CLI applies the
V0.5.1 provider-policy gate before any external request.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import Settings

from .client import FotMobClient
from .history_discovery import (
    extract_league_metadata,
    extract_match_index,
    extract_seasons,
    season_matches_selector,
    select_reproducible_sample,
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


def historical_automation_allowed(settings: Settings) -> bool:
    """Return whether an external historical request is explicitly allowed."""

    return bool(
        settings.fotmob_enabled
        and settings.fotmob_history_enabled
        and settings.fotmob_provider_decision == "PRODUCTION_READY"
        and settings.fotmob_automated_usage == "ACCEPTABLE_FOR_PROJECT"
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _payload_hash(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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
        country=row["country"],
        first_seen_at=row["first_seen_at"],
        provider=str(row["provider"]),
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
            min_request_interval_seconds=1.0 / max(0.01, settings.fotmob_history_requests_per_second),
            logger=self.logger,
        )
        self.archive = FotMobHistoricalArchive(settings.archive_path, settings.parquet_compression)
        self._archive_lock = threading.RLock()

    def _network_error(self) -> str:
        return (
            "FotMob Historical-Netzwerkzugriff ist gesperrt. Setze nur nach "
            "ausdrücklicher Providerfreigabe FOTMOB_HISTORY_ENABLED=true, "
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
    ) -> LeagueDiscoveryResult:
        if payload is None:
            if not historical_automation_allowed(self.settings):
                return LeagueDiscoveryResult(False, str(league_id), error=self._network_error())
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
    ) -> MatchIndexResult:
        if payload is None:
            if not historical_automation_allowed(self.settings):
                return MatchIndexResult(
                    False, str(league_id), season.season_id, season.season_label, error=self._network_error()
                )
            payload, error = self._fetch_json(
                self._endpoint(
                    self.settings.fotmob_season_path,
                    league_id=league_id,
                    season_id=season.season_id,
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
        )
        normalized["payload_hash"] = payload_hash
        return normalized

    def fetch_details(
        self,
        league_id: str,
        season_id: str,
        *,
        workers: int = 1,
        retry_failed: bool = False,
        only_sample: bool = False,
        limit: int | None = None,
        batch_size: int | None = None,
    ) -> dict[str, Any]:
        if not historical_automation_allowed(self.settings):
            return {
                "status": "BLOCKED_BY_POLICY",
                "league_id": str(league_id),
                "season_id": str(season_id),
                "error": self._network_error(),
            }
        workers = max(1, min(8, int(workers)))
        batch_size = max(1, int(batch_size or self.settings.fotmob_history_batch_size))
        worker_id = f"history-{uuid.uuid4().hex[:12]}"
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
                )
                if status == "FETCHED":
                    fetched_count += 1
                else:
                    partial_count += 1
            buffer = []

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
        status = self.store.status(league_id, season_id)
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
            "archive_files": sorted(set(archive_files)),
            "access": getattr(self.client, "metrics_snapshot", lambda: {})(),
        }

    def status(self, league_id: str, season_id: str) -> dict[str, Any]:
        return self.store.status(league_id, season_id)
