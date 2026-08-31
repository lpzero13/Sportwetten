"""Optional FotMob orchestration, isolated from Tipico collection."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from config import Settings

from .canonical import FotMobCanonicalArchive, ht_snapshot_row
from .client import FotMobClient
from .history_models import FotMobMatchIndexRecord
from .history_pipeline import FotMobHistoryPipeline
from .matching import MatchIdentity, MatchMatchResult, MatchMatcher, normalize_name
from .models import FOTMOB_SNAPSHOT_TYPES, FotMobFetchResult, FotMobMatch, FotMobSnapshot
from .storage import FotMobParquetArchive, FotMobStore


@dataclass(slots=True)
class FotMobRefreshResult:
    success: bool
    internal_match_id: str | None = None
    match: FotMobMatch | None = None
    match_result: MatchMatchResult | None = None
    snapshot_id: int | None = None
    snapshot_created: bool = False
    result_consistency: str | None = None
    ht_consistency: str | None = None
    error: str | None = None


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _score_pair(value_home: Any, value_away: Any) -> tuple[int | None, int | None]:
    def integer(value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    return integer(value_home), integer(value_away)


def _field(value: Any, name: str) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        return value.get(name)
    keys = getattr(value, "keys", None)
    if callable(keys) and name in keys():
        return value[name]
    return getattr(value, name, None)


def compare_results(tipico_result: Any, fotmob_match: FotMobMatch) -> str:
    """Compare only explicit final scores; missing values stay explicit."""

    tipico_home = tipico_away = None
    if tipico_result is not None:
        tipico_home, tipico_away = _score_pair(
            _field(tipico_result, "ft_home"),
            _field(tipico_result, "ft_away"),
        )
    fotmob_home, fotmob_away = _score_pair(fotmob_match.score_home, fotmob_match.score_away)
    if tipico_home is None or tipico_away is None:
        return "TIPICO_RESULT_MISSING"
    if fotmob_home is None or fotmob_away is None:
        return "FOTMOB_RESULT_MISSING"
    return "RESULT_MATCH" if (tipico_home, tipico_away) == (fotmob_home, fotmob_away) else "RESULT_CONFLICT"


def compare_halftime(tipico_result: Any, fotmob_match: FotMobMatch) -> str:
    tipico_home = tipico_away = None
    if tipico_result is not None:
        tipico_home, tipico_away = _score_pair(
            _field(tipico_result, "ht_home")
            if _field(tipico_result, "ht_home") is not None
            else _field(tipico_result, "ht_score_home"),
            _field(tipico_result, "ht_away")
            if _field(tipico_result, "ht_away") is not None
            else _field(tipico_result, "ht_score_away"),
        )
    fotmob_home, fotmob_away = _score_pair(
        fotmob_match.ht_score_home, fotmob_match.ht_score_away
    )
    if tipico_home is None or tipico_away is None or fotmob_home is None or fotmob_away is None:
        return "HT_MISSING"
    return "HT_MATCH" if (tipico_home, tipico_away) == (fotmob_home, fotmob_away) else "HT_CONFLICT"


class FotMobService:
    """A feature-flagged boundary for matching, enrichment and archiving."""

    def __init__(
        self,
        settings: Settings,
        database: Any,
        *,
        client: FotMobClient | Any | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.settings = settings
        self.enabled = bool(settings.fotmob_enabled)
        self.network_mode = str(getattr(settings, "fotmob_network_mode", "off")).casefold()
        self.provider_decision = str(settings.fotmob_provider_decision).upper()
        self.automated_usage = str(settings.fotmob_automated_usage).upper()
        self.manual_use_allowed = (
            self.enabled
            and self.provider_decision != "NOT_SUITABLE"
            and self.automated_usage != "NOT_ACCEPTABLE"
        )
        self.automated_worker_allowed = (
            self.enabled
            and self.network_mode == "worker"
            and self.provider_decision == "PRODUCTION_READY"
            and self.automated_usage == "ACCEPTABLE_FOR_PROJECT"
        )
        self.logger = logger or logging.getLogger("tipico.fotmob")
        self.store = FotMobStore(database, settings.archive_path)
        self.client = client or FotMobClient(
            base_url=settings.fotmob_base_url,
            api_base_url=settings.fotmob_api_base_url,
            match_details_path=settings.fotmob_match_details_path,
            timeout_seconds=settings.fotmob_timeout_seconds,
            max_retries=settings.fotmob_max_retries,
            min_request_interval_seconds=settings.fotmob_min_request_interval_seconds,
            logger=self.logger,
        )
        self.archive = FotMobParquetArchive(settings.archive_path, settings.parquet_compression)
        self.canonical_archive = FotMobCanonicalArchive(
            getattr(settings, "fotmob_archive_path", settings.archive_path / "fotmob"),
            settings.parquet_compression,
        )
        # The UI uses this same rate-limited client for explicit date-range
        # loads.  No network call is made during construction.
        self.history_pipeline = FotMobHistoryPipeline(
            settings,
            database,
            client=self.client,
            logger=self.logger,
        )
        # Competition mappings are persisted once and then reused by the
        # halftime resolver.  The import is local to keep the service/model
        # dependency graph acyclic.
        from .enrichment import FotMobTipicoResolver

        self.resolver = FotMobTipicoResolver(self)
        self.resolver.seed_default_competition_links()
        self.last_error: str | None = None
        self.last_result: FotMobRefreshResult | None = None

    def _matcher(self) -> MatchMatcher:
        known_provider_ids = {
            str(row["provider_match_id"]): str(row["tipico_event_id"])
            for row in self.store.links(limit=1000)
            if row["provider_match_id"] and row["tipico_event_id"]
        }
        return MatchMatcher(
            tolerance_minutes=self.settings.fotmob_matching_tolerance_minutes,
            team_aliases=self.store.team_alias_map(None),
            competition_aliases=self.store.competition_alias_map(None),
            known_provider_ids=known_provider_ids,
        )

    def _tipico_result(self, internal_match_id: str) -> Any:
        row = self.store.match_row(internal_match_id)
        if row is None or not row["tipico_event_id"]:
            return None
        method = getattr(self.store.database, "match_result_for_event", None)
        result = method(str(row["tipico_event_id"])) if method else None
        # Before FINAL, the shared matches row is still enough to compare the
        # currently known half-time score.  It intentionally has no FT score,
        # so compare_results continues to report TIPICO_RESULT_MISSING.
        return result or row

    def _quality_flags(
        self,
        match: FotMobMatch,
        result_consistency: str,
        ht_consistency: str,
    ) -> list[str]:
        flags: list[str] = []
        if not match.ht_stats_available:
            flags.append("FOTMOB_HT_STATS_UNAVAILABLE")
        if not match.stats.has_any_value():
            flags.append("FOTMOB_STATS_UNAVAILABLE")
        elif any(value is None for key, value in match.stats.to_dict().items() if key != "extra_stats"):
            flags.append("FOTMOB_STATS_PARTIAL")
        if result_consistency != "RESULT_MATCH":
            flags.append(result_consistency)
        if ht_consistency not in {"HT_MATCH"}:
            flags.append(ht_consistency)
        return flags

    def _persist_match(
        self,
        internal_match_id: str,
        match: FotMobMatch,
        *,
        observed_at: str,
        snapshot_type: str | None = None,
        raw_payload_path: str | None = None,
        source_context: str | None = None,
        captured_live: bool = False,
        stats_period: str | None = None,
        tipico_event_id: str | None = None,
    ) -> FotMobRefreshResult:
        self.store.upsert_fotmob_match(internal_match_id, match, observed_at=observed_at)
        tipico_result = self._tipico_result(internal_match_id)
        result_consistency = compare_results(tipico_result, match)
        ht_consistency = compare_halftime(tipico_result, match)
        flags = self._quality_flags(match, result_consistency, ht_consistency)
        quality = "COMPLETE" if not flags else "PARTIAL"
        self.store.upsert_current_state(
            internal_match_id=internal_match_id,
            match=match,
            observed_at=observed_at,
            result_consistency=result_consistency,
            ht_consistency=ht_consistency,
            quality=quality,
            raw_payload_path=raw_payload_path,
            provider="FOTMOB",
            stats_period=stats_period or (
                "FIRST_HALF" if snapshot_type in {"HALFTIME", "HT_STABLE"} else "FULL_MATCH"
            ),
            source_context=source_context,
            captured_live=captured_live,
            tipico_event_id=tipico_event_id,
        )
        self.store.upsert_quality(
            internal_match_id=internal_match_id,
            fotmob_matched=True,
            fotmob_ht_available=match.ht_score_home is not None and match.ht_score_away is not None,
            fotmob_ht_stats_available=match.ht_stats_available,
            tipico_ht_available=(
                _field(tipico_result, "ht_home") is not None
                and _field(tipico_result, "ht_away") is not None
                if tipico_result is not None and _field(tipico_result, "ht_home") is not None
                else tipico_result is not None
                and _field(tipico_result, "ht_score_home") is not None
                and _field(tipico_result, "ht_score_away") is not None
            ),
            result_consistency=result_consistency,
            ht_consistency=ht_consistency,
            fotmob_result_status=result_consistency,
            quality_flags=flags,
        )
        snapshot_id: int | None = None
        created = False
        snapshot_types = [snapshot_type] if snapshot_type not in {None, "AUTO"} else []
        if snapshot_type == "AUTO":
            snapshot_types = self._auto_snapshot_types(internal_match_id, match, observed_at)
        for current_type in snapshot_types:
            if current_type not in FOTMOB_SNAPSHOT_TYPES:
                raise ValueError(f"Unsupported FotMob snapshot type: {current_type}")
            snapshot = FotMobSnapshot(
                internal_match_id=internal_match_id,
                match=match,
                snapshot_type=current_type,
                captured_at=observed_at,
                quality=quality,
                result_consistency=result_consistency,
                ht_consistency=ht_consistency,
                raw_payload_path=raw_payload_path,
                provider="FOTMOB",
                stats_period=stats_period or (
                    "FIRST_HALF" if current_type in {"HALFTIME", "HT_STABLE"} else "FULL_MATCH"
                ),
                source_context=source_context,
                captured_live=captured_live,
                tipico_event_id=tipico_event_id,
            )
            current_id, current_created = self.store.save_snapshot(snapshot)
            snapshot_id = current_id
            created = created or current_created
            if current_type == "HALFTIME" and captured_live:
                # Live HT enrichment has its own canonical dataset.  It is
                # intentionally not written into the historical core as a
                # replacement for a completed fresh match.
                index = FotMobMatchIndexRecord(
                    provider_match_id=match.provider_match_id,
                    league_id=str(match.competition_id or "unknown"),
                    season_id=str(match.season or "unknown"),
                    season_label=str(match.season or "unknown"),
                    kickoff_at=match.kickoff_at,
                    home_team_id=match.home_team_id,
                    home_team_name=match.home_team,
                    away_team_id=match.away_team_id,
                    away_team_name=match.away_team,
                    match_status=match.status,
                    league_name=match.competition_name,
                    country=match.competition_country,
                )
                self.canonical_archive.write_ht_snapshot(
                    ht_snapshot_row(
                        index,
                        match,
                        captured_at=observed_at,
                        internal_match_id=internal_match_id,
                        tipico_event_id=tipico_event_id,
                        matching_status="CONFIRMED",
                        matching_confidence=1.0,
                    )
                )
        result = FotMobRefreshResult(
            success=True,
            internal_match_id=internal_match_id,
            match=match,
            snapshot_id=snapshot_id,
            snapshot_created=created,
            result_consistency=result_consistency,
            ht_consistency=ht_consistency,
        )
        self.last_result = result
        self.last_error = None
        return result

    def _auto_snapshot_types(
        self,
        internal_match_id: str,
        match: FotMobMatch,
        observed_at: str,
    ) -> list[str]:
        """Return only the seven allowed slots crossed by this observation."""

        types: list[str] = []
        status = (match.status or "").casefold()
        period = (match.period or "").casefold().replace("_", "")
        if status in {"scheduled", "upcoming", "not started", "notstarted", "pre match"}:
            types.append("PRE_KICKOFF")
        if period in {"ht", "halftime", "half time", "1h"}:
            types.append("HALFTIME")
        if period in {"2h", "second half", "secondhalf"} and match.minute is not None:
            halftime = next(
                (
                    row for row in self.store.snapshots_for_match(internal_match_id)
                    if row["snapshot_type"] == "HALFTIME"
                ),
                None,
            )
            if halftime is not None:
                captured = _parse_time(str(halftime["captured_at"]))
                now = _parse_time(observed_at)
                if captured is not None and now is not None and (
                    now - captured
                ).total_seconds() >= self.settings.fotmob_ht_stable_delay_seconds:
                    types.append("HT_STABLE")
        if match.minute is not None:
            if match.minute >= 60:
                types.append("MINUTE_60")
            if match.minute >= 70:
                types.append("MINUTE_70")
            if match.minute >= 80:
                types.append("MINUTE_80")
        if match.is_finished:
            types.append("FINAL")
        return list(dict.fromkeys(types))

    def ensure_tipico_event(self, event: Any, *, observed_at: str | None = None) -> str:
        return self.store.upsert_tipico_event(event, observed_at=observed_at)

    def has_current_state_for_tipico_event(self, event_id: str) -> bool:
        """Return whether a persisted FotMob state is available for a Tipico event."""

        row = self.store.match_row_for_tipico_event(str(event_id))
        return bool(
            row is not None
            and self.store.current_state(str(row["internal_match_id"])) is not None
        )

    def match_tipico_event(
        self,
        event: Any,
        candidates: Iterable[FotMobMatch],
    ) -> MatchMatchResult:
        internal_match_id = self.ensure_tipico_event(event)
        candidate_list = list(candidates)
        tipico_identity = MatchIdentity.from_tipico_event(event)
        result = self._matcher().match(tipico_identity, candidate_list)
        candidate_map = {item.provider_match_id: item for item in candidate_list}
        if result.auto_linkable and result.provider_match_id:
            selected = candidate_map.get(result.provider_match_id)
            self.store.upsert_link(
                internal_match_id=internal_match_id,
                provider="FOTMOB",
                provider_match_id=result.provider_match_id,
                confidence=result.confidence,
                status=result.status,
                reason=";".join(result.reasons),
            )
            if selected is not None:
                self._persist_verified_aliases(event, selected)
                self._persist_match(
                    internal_match_id,
                    selected,
                    observed_at=_iso_now(),
                )
        return result

    def _persist_verified_aliases(self, event: Any, match: FotMobMatch) -> None:
        def stable_id(provider: str, provider_id: str | None, name: str) -> str:
            value = provider_id or normalize_name(name)
            return f"{provider.lower()}_team_{hashlib.sha256(value.encode()).hexdigest()[:16]}"

        for provider, event_name, event_id, fotmob_name, fotmob_id in (
            ("TIPICO", event.home_team, getattr(event, "home_team_id", None), match.home_team, match.home_team_id),
            ("TIPICO", event.away_team, getattr(event, "away_team_id", None), match.away_team, match.away_team_id),
        ):
            team_id = stable_id("tipico", event_id, event_name)
            self.store.upsert_team(team_id=team_id, canonical_name=event_name)
            self.store.upsert_team_alias(
                team_id=team_id,
                provider=provider,
                provider_name=event_name,
                normalized_name=normalize_name(event_name),
                provider_team_id=event_id,
                verified=True,
            )
            self.store.upsert_team_alias(
                team_id=team_id,
                provider="FOTMOB",
                provider_name=fotmob_name,
                normalized_name=normalize_name(fotmob_name),
                provider_team_id=fotmob_id,
                verified=True,
            )
        if event.competition_name and match.competition_name:
            competition_id = str(event.competition_id or normalize_name(event.competition_name))
            self.store.upsert_competition_alias(
                competition_id=competition_id,
                provider="TIPICO",
                provider_name=event.competition_name,
                normalized_name=normalize_name(event.competition_name),
                provider_competition_id=event.competition_id,
                country=getattr(event, "competition_country", None),
                verified=True,
            )
            self.store.upsert_competition_alias(
                competition_id=competition_id,
                provider="FOTMOB",
                provider_name=match.competition_name,
                normalized_name=normalize_name(match.competition_name),
                provider_competition_id=match.competition_id,
                country=match.competition_country,
                verified=True,
            )

    def confirm_manual(self, event: Any, match: FotMobMatch, *, reason: str = "manual_confirmation") -> str:
        internal_match_id = self.ensure_tipico_event(event)
        self.store.upsert_link(
            internal_match_id=internal_match_id,
            provider="FOTMOB",
            provider_match_id=match.provider_match_id,
            confidence=1.0,
            status="MANUALLY_CONFIRMED",
            reason=reason,
            verified_at=_iso_now(),
        )
        self._persist_verified_aliases(event, match)
        self._persist_match(internal_match_id, match, observed_at=_iso_now())
        return internal_match_id

    def reject_match(self, event: Any, provider_match_id: str, *, reason: str = "manual_rejection") -> str:
        internal_match_id = self.ensure_tipico_event(event)
        self.store.upsert_link(
            internal_match_id=internal_match_id,
            provider="FOTMOB",
            provider_match_id=provider_match_id,
            confidence=0.0,
            status="REJECTED",
            reason=reason,
            verified_at=_iso_now(),
        )
        return internal_match_id

    def discover_and_match(
        self,
        event: Any,
        provider_match_id: str,
        *,
        snapshot_type: str | None = None,
    ) -> FotMobRefreshResult:
        internal_match_id = self.ensure_tipico_event(event)
        if not self.enabled:
            return FotMobRefreshResult(
                False,
                internal_match_id=internal_match_id,
                error="FotMob ist deaktiviert (FOTMOB_ENABLED=false).",
            )
        if not self.manual_use_allowed:
            return FotMobRefreshResult(
                False,
                internal_match_id=internal_match_id,
                error=(
                    "FotMob-Einzelspielnutzung ist durch die Provider-Policy "
                    f"deaktiviert (decision={self.provider_decision}, "
                    f"automated_usage={self.automated_usage})."
                ),
            )
        fetched = self.client.fetch_match_details(str(provider_match_id))
        if not isinstance(fetched, FotMobFetchResult):
            fetched = FotMobFetchResult(success=True, match=fetched)
        if not fetched.success or fetched.match is None:
            error = fetched.error or "FotMob-Match konnte nicht gelesen werden."
            self.last_error = error
            result = FotMobRefreshResult(False, internal_match_id=internal_match_id, error=error)
            self.last_result = result
            return result
        match_result = self.match_tipico_event(event, [fetched.match])
        if not match_result.auto_linkable:
            return FotMobRefreshResult(
                False,
                internal_match_id=internal_match_id,
                match=fetched.match,
                match_result=match_result,
                error=f"Matching nicht automatisch bestätigt: {match_result.status}",
            )
        result = self._persist_match(
            internal_match_id,
            fetched.match,
            observed_at=_iso_now(),
            snapshot_type=snapshot_type,
        )
        result.match_result = match_result
        return result

    def refresh_link(
        self,
        internal_match_id: str,
        *,
        snapshot_type: str | None = None,
        source_context: str | None = "MANUAL_REFRESH",
        captured_live: bool = False,
        stats_period: str | None = None,
        tipico_event_id: str | None = None,
    ) -> FotMobRefreshResult:
        if not self.enabled:
            return FotMobRefreshResult(False, internal_match_id=internal_match_id, error="FotMob ist deaktiviert.")
        if not self.manual_use_allowed:
            return FotMobRefreshResult(
                False,
                internal_match_id=internal_match_id,
                error=(
                    "FotMob-Refresh ist durch die Provider-Policy deaktiviert "
                    f"(decision={self.provider_decision}, automated_usage={self.automated_usage})."
                ),
            )
        link = self.store.link_for_internal(internal_match_id)
        if link is None or link["match_status"] in {"REJECTED", "UNMATCHED", "AMBIGUOUS"}:
            return FotMobRefreshResult(False, internal_match_id=internal_match_id, error="Kein bestätigter FotMob-Link vorhanden.")
        fetched = self.client.fetch_match_details(str(link["provider_match_id"]))
        if not isinstance(fetched, FotMobFetchResult):
            fetched = FotMobFetchResult(success=True, match=fetched)
        if not fetched.success or fetched.match is None:
            error = fetched.error or "FotMob-Abruf fehlgeschlagen."
            self.last_error = error
            result = FotMobRefreshResult(False, internal_match_id=internal_match_id, error=error)
            self.last_result = result
            return result
        return self._persist_match(
            internal_match_id,
            fetched.match,
            observed_at=_iso_now(),
            snapshot_type=snapshot_type,
            source_context=source_context,
            captured_live=captured_live,
            stats_period=stats_period,
            tipico_event_id=tipico_event_id,
        )

    def refresh_for_tipico_event(
        self,
        event: Any,
        *,
        snapshot_type: str | None = None,
    ) -> FotMobRefreshResult:
        internal_match_id = self.ensure_tipico_event(event)
        if snapshot_type == "HALFTIME" and not getattr(
            self.settings, "fotmob_ht_enrichment_enabled", True
        ):
            return FotMobRefreshResult(
                False,
                internal_match_id=internal_match_id,
                error="FotMob-HT-Enrichment ist deaktiviert (FOTMOB_HT_ENRICHMENT_ENABLED=false).",
            )
        if not self.automated_worker_allowed:
            return FotMobRefreshResult(
                False,
                internal_match_id=internal_match_id,
                error=(
                    "Automatisches FotMob-Refresh ist durch die Provider-Policy "
                    f"deaktiviert (decision={self.provider_decision}, "
                    f"automated_usage={self.automated_usage})."
                ),
            )
        if snapshot_type == "HALFTIME":
            current = self.store.current_state(internal_match_id)
            observed = _parse_time(str(current["observed_at"])) if current is not None else None
            if observed is not None:
                age = (datetime.now(timezone.utc) - observed).total_seconds()
                has_first_half = bool(current["ht_stats_json"])
                is_live_ht = bool(current["captured_live"]) and current["source_context"] == "LIVE_HT"
                if 0 <= age < self.settings.fotmob_poll_seconds and is_live_ht and has_first_half:
                    return FotMobRefreshResult(
                        True,
                        internal_match_id=internal_match_id,
                        result_consistency=current["result_consistency"],
                        ht_consistency=current["ht_consistency"],
                    )
        return self.refresh_link(
            internal_match_id,
            snapshot_type=snapshot_type,
            source_context="LIVE_HT" if snapshot_type == "HALFTIME" else "LIVE_REFRESH",
            captured_live=snapshot_type == "HALFTIME",
            stats_period="FIRST_HALF" if snapshot_type == "HALFTIME" else None,
            tipico_event_id=str(event.event_id),
        )

    def current_for_tipico_event(self, event: Any) -> Any:
        row = self.store.match_row_for_tipico_event(str(event.event_id))
        return self.store.current_state(row["internal_match_id"]) if row else None

    def export_pending(self) -> dict[str, Any]:
        return self.archive.export_pending(
            self.store,
            batch_size=self.settings.fotmob_snapshot_outbox_batch_size,
        )

    def metrics(self) -> dict[str, Any]:
        metrics = self.store.metrics_for_date()
        client_metrics = getattr(self.client, "metrics_snapshot", lambda: {})()
        metrics["enabled"] = self.enabled
        metrics["network_mode"] = self.network_mode
        metrics["provider_decision"] = self.provider_decision
        metrics["automated_usage"] = self.automated_usage
        metrics["manual_use_allowed"] = self.manual_use_allowed
        metrics["automated_worker_allowed"] = self.automated_worker_allowed
        metrics["access"] = client_metrics
        # The date-range UI now consumes the complete daily feed.  Keep this
        # aggregate unscoped so the debug panel does not silently report only
        # the legacy Bundesliga queue.
        metrics["daily"] = self.history_pipeline.store.daily_status()
        metrics["canonical_archive"] = str(self.canonical_archive.root)
        metrics["canonical_archive_bytes"] = self.canonical_archive.total_size_bytes
        return metrics
