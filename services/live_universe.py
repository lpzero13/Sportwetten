"""Cheap, cache-backed eligibility decisions for the Tipico live universe.

The global Tipico feed remains the collector's radar.  This module only
decides which already parsed events are worth sending through expensive detail
and FotMob paths.  It never removes an event from the feed or from durable
history, and it has no network client of its own.
"""

from __future__ import annotations

import re
import time
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from config import Settings
from models.event import LiveEvent
from storage.database import Database


P0_SELECTED = "P0_SELECTED"
P1_STRATEGY_ELIGIBLE = "P1_STRATEGY_ELIGIBLE"
P2_DISCOVERY = "P2_DISCOVERY"
P3_MINIMAL = "P3_MINIMAL"
P4_IGNORE = "P4_IGNORE"

SENIOR_COMPETITIVE = "SENIOR_COMPETITIVE"
EXCLUDED_COMPETITION = "EXCLUDED"

FOTMOB_FULL = "FULL"
FOTMOB_DISCOVERY = "DISCOVERY"
FOTMOB_NO_DATA = "NO_DATA"
MARKET_SUITABLE = "SUITABLE"
MARKET_DISCOVERY = "DISCOVERY"
MARKET_NO_DATA = "NO_DATA"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_label(value: Any) -> str:
    """Normalize labels for stable cache lookups without changing stored text."""

    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(character for character in text if not unicodedata.combining(character))
    text = text.casefold().replace("ß", "ss")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


_EXCLUDED_COMPETITION_RE = re.compile(
    r"(?:^|[^a-z0-9])(?:u[\s-]?(?:17|18|19|20|21|23)|"
    r"youth|academy|junior(?:en)?|reserve(?:s)?|b[\s-]?team|ii[\s-]?team|"
    r"club friendlies?|vereins[\s-]?freundschaft|freundschaftsspiele?)"
    r"(?:$|[^a-z0-9])",
    re.IGNORECASE,
)


def competition_policy(event: LiveEvent) -> str:
    """Classify obvious youth/reserve/friendly competitions.

    Women/frauen terms are intentionally not in the exclusion list.  A
    women's competition is eligible when its provider and market capabilities
    support it.
    """

    labels = [event.competition_id, event.competition_name]
    raw = event.raw_data if isinstance(event.raw_data, dict) else {}
    for key in (
        "ageGroup",
        "age_group",
        "competitionType",
        "competition_type",
        "category",
        "categoryName",
        "groupName",
    ):
        value = raw.get(key)
        if isinstance(value, (str, int, float)):
            labels.append(value)
    searchable = " ".join(normalize_label(value) for value in labels if value is not None)
    if _EXCLUDED_COMPETITION_RE.search(searchable):
        return EXCLUDED_COMPETITION
    return SENIOR_COMPETITIVE


@dataclass(frozen=True, slots=True)
class FotMobCoverage:
    provider: str
    fotmob_league_id: str
    country: str | None
    league_name: str | None
    season_id: str
    season_label: str | None
    observed_matches: int
    detailed_matches: int
    coverage_ratio: float
    sample_size: int
    last_checked: str
    status: str

    @classmethod
    def from_row(cls, row: Any) -> "FotMobCoverage":
        return cls(
            provider=str(row["provider"] or "FOTMOB"),
            fotmob_league_id=str(row["fotmob_league_id"]),
            country=str(row["country"]) if row["country"] is not None else None,
            league_name=(str(row["league_name"]) if row["league_name"] is not None else None),
            season_id=str(row["season_id"] or ""),
            season_label=(str(row["season_label"]) if row["season_label"] is not None else None),
            observed_matches=int(row["observed_matches"] or 0),
            detailed_matches=int(row["detailed_matches"] or 0),
            coverage_ratio=float(row["coverage_ratio"] or 0.0),
            sample_size=int(row["sample_size"] or 0),
            last_checked=str(row["last_checked"] or ""),
            status=str(row["status"] or FOTMOB_DISCOVERY).upper(),
        )


@dataclass(frozen=True, slots=True)
class TipicoMarketCapability:
    competition_id: str
    competition_name: str
    competition_country: str | None
    observed_matches: int
    matches_with_strategy_markets: int
    coverage_ratio: float
    last_checked: str
    status: str

    @classmethod
    def from_row(cls, row: Any) -> "TipicoMarketCapability":
        return cls(
            competition_id=str(row["competition_id"]),
            competition_name=str(row["competition_name"] or row["competition_id"]),
            competition_country=(
                str(row["competition_country"])
                if row["competition_country"] is not None
                else None
            ),
            observed_matches=int(row["observed_matches"] or 0),
            matches_with_strategy_markets=int(row["matches_with_strategy_markets"] or 0),
            coverage_ratio=float(row["coverage_ratio"] or 0.0),
            last_checked=str(row["last_checked"] or ""),
            status=str(row["status"] or MARKET_DISCOVERY).upper(),
        )


@dataclass(frozen=True, slots=True)
class LiveUniverseDecision:
    event_id: str
    competition_id: str | None
    competition_name: str
    competition_country: str | None
    priority: str
    competition_policy: str
    fotmob_league_id: str | None
    fotmob_season_id: str | None
    fotmob_status: str
    tipico_market_status: str
    eligible_for_strategy: bool
    detail_allowed: bool
    fotmob_probe_allowed: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "competition_id": self.competition_id,
            "competition_name": self.competition_name,
            "competition_country": self.competition_country,
            "priority": self.priority,
            "competition_policy": self.competition_policy,
            "fotmob_league_id": self.fotmob_league_id,
            "fotmob_season_id": self.fotmob_season_id,
            "fotmob_status": self.fotmob_status,
            "tipico_market_status": self.tipico_market_status,
            "eligible_for_strategy": self.eligible_for_strategy,
            "detail_allowed": self.detail_allowed,
            "fotmob_probe_allowed": self.fotmob_probe_allowed,
            "reason": self.reason,
        }


class LiveUniverse:
    """Build and cache capability decisions without reducing the live radar."""

    def __init__(
        self,
        database: Database,
        settings: Settings,
        *,
        logger: Any | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.logger = logger
        self._loaded_at: float | None = None
        self._coverage: list[FotMobCoverage] = []
        self._coverage_by_league: dict[str, list[FotMobCoverage]] = defaultdict(list)
        self._coverage_by_name: dict[tuple[str, str], list[FotMobCoverage]] = defaultdict(list)
        self._coverage_by_name_only: dict[str, list[FotMobCoverage]] = defaultdict(list)
        self._market: dict[str, TipicoMarketCapability] = {}
        self._market_by_name: dict[tuple[str, str], TipicoMarketCapability] = {}
        self._links_by_tipico_id: dict[str, str] = {}
        self._links_by_name: dict[tuple[str, str], str] = {}
        self._last_refresh_at: str | None = None
        self._discovery_probe_at: dict[str, float] = {}

    @property
    def last_refresh_at(self) -> str | None:
        return self._last_refresh_at

    def invalidate(self) -> None:
        self._loaded_at = None

    def discovery_probe_due(self, event: LiveEvent) -> bool:
        """Rate-limit P2 probes per competition without slowing the feed."""

        key = str(event.competition_id or normalize_label(event.competition_name) or event.event_id)
        now = time.monotonic()
        interval = max(
            0.0,
            float(getattr(self.settings, "smart_universe_discovery_probe_seconds", 900.0)),
        )
        previous = self._discovery_probe_at.get(key)
        if previous is not None and now - previous < interval:
            return False
        self._discovery_probe_at[key] = now
        return True

    def _status_for_coverage(self, sample_size: int, ratio: float) -> str:
        minimum = max(1, int(getattr(self.settings, "fotmob_coverage_min_sample_size", 5)))
        full_ratio = float(getattr(self.settings, "fotmob_coverage_full_ratio", 0.90))
        no_data_ratio = float(getattr(self.settings, "fotmob_coverage_no_data_ratio", 0.10))
        if sample_size >= minimum and ratio >= full_ratio:
            return FOTMOB_FULL
        if sample_size >= minimum and ratio <= no_data_ratio:
            return FOTMOB_NO_DATA
        return FOTMOB_DISCOVERY

    def _derive_fotmob_catalog(self) -> list[dict[str, Any]]:
        """Aggregate the existing historical index without touching archives."""

        aggregates: dict[tuple[str, str], dict[str, Any]] = {}
        with self.database._lock:
            index_rows = self.database.connection.execute(
                """
                SELECT league_id, season_id, season_label, league_name,
                       country, country_name, detail_status, last_checked_at
                FROM fotmob_match_index
                WHERE upper(provider) = 'FOTMOB'
                """
            ).fetchall()
            season_rows = self.database.connection.execute(
                """
                SELECT league_id, season_id, season_label, league_name,
                       country, last_checked_at
                FROM fotmob_seasons
                WHERE upper(provider) = 'FOTMOB'
                """
            ).fetchall()

        for row in season_rows:
            key = (str(row["league_id"]), str(row["season_id"] or ""))
            aggregates.setdefault(
                key,
                {
                    "provider": "FOTMOB",
                    "fotmob_league_id": key[0],
                    "season_id": key[1],
                    "season_label": row["season_label"],
                    "league_name": row["league_name"],
                    "country": row["country"],
                    "observed_matches": 0,
                    "detailed_matches": 0,
                    "last_checked": row["last_checked_at"],
                },
            )

        for row in index_rows:
            key = (str(row["league_id"]), str(row["season_id"] or ""))
            item = aggregates.setdefault(
                key,
                {
                    "provider": "FOTMOB",
                    "fotmob_league_id": key[0],
                    "season_id": key[1],
                    "season_label": row["season_label"],
                    "league_name": row["league_name"],
                    "country": row["country_name"] or row["country"],
                    "observed_matches": 0,
                    "detailed_matches": 0,
                    "last_checked": row["last_checked_at"],
                },
            )
            item["observed_matches"] += 1
            if str(row["detail_status"] or "").upper() in {"FETCHED", "PARTIAL"}:
                item["detailed_matches"] += 1
            if row["season_label"]:
                item["season_label"] = row["season_label"]
            if row["league_name"]:
                item["league_name"] = row["league_name"]
            if row["country_name"] or row["country"]:
                item["country"] = row["country_name"] or row["country"]
            checked = row["last_checked_at"]
            if checked and (not item.get("last_checked") or str(checked) > str(item["last_checked"])):
                item["last_checked"] = checked

        result: list[dict[str, Any]] = []
        checked_now = _now_iso()
        for item in aggregates.values():
            observed = int(item["observed_matches"] or 0)
            detailed = int(item["detailed_matches"] or 0)
            ratio = detailed / observed if observed else 0.0
            result.append(
                {
                    **item,
                    "coverage_ratio": ratio,
                    "sample_size": observed,
                    "last_checked": item.get("last_checked") or checked_now,
                    "status": self._status_for_coverage(observed, ratio),
                }
            )
        return result

    def _derive_market_capability(self) -> list[dict[str, Any]]:
        aggregates: dict[str, dict[str, Any]] = {}
        with self.database._lock:
            rows = self.database.connection.execute(
                """
                SELECT competition_id, competition_name, competition_country,
                       event_id, snapshot_quality, q_zero_best, q_two_plus_best
                FROM snapshots
                WHERE competition_id IS NOT NULL
                """
            ).fetchall()
        for row in rows:
            competition_id = str(row["competition_id"])
            item = aggregates.setdefault(
                competition_id,
                {
                    "competition_id": competition_id,
                    "competition_name": row["competition_name"] or competition_id,
                    "competition_country": row["competition_country"],
                    "events": set(),
                    "strategy_events": set(),
                    "last_checked": None,
                },
            )
            event_id = str(row["event_id"])
            item["events"].add(event_id)
            if (
                str(row["snapshot_quality"] or "").upper() != "FAILED"
                and row["q_zero_best"] is not None
                and row["q_two_plus_best"] is not None
            ):
                item["strategy_events"].add(event_id)
            if row["competition_name"]:
                item["competition_name"] = row["competition_name"]
            if row["competition_country"]:
                item["competition_country"] = row["competition_country"]

        minimum = max(
            1,
            int(getattr(self.settings, "tipico_market_capability_min_sample_size", 5)),
        )
        minimum_ratio = float(
            getattr(self.settings, "tipico_market_capability_min_ratio", 0.50)
        )
        checked_now = _now_iso()
        result: list[dict[str, Any]] = []
        for item in aggregates.values():
            observed = len(item["events"])
            strategy_events = len(item["strategy_events"])
            ratio = strategy_events / observed if observed else 0.0
            status = (
                MARKET_SUITABLE
                if observed >= minimum and ratio >= minimum_ratio
                else MARKET_NO_DATA
                if observed >= minimum
                else MARKET_DISCOVERY
            )
            result.append(
                {
                    "competition_id": item["competition_id"],
                    "competition_name": item["competition_name"],
                    "competition_country": item["competition_country"],
                    "observed_matches": observed,
                    "matches_with_strategy_markets": strategy_events,
                    "coverage_ratio": ratio,
                    "last_checked": checked_now,
                    "status": status,
                }
            )
        return result

    @staticmethod
    def _choose_coverage(rows: Iterable[FotMobCoverage]) -> FotMobCoverage | None:
        candidates = list(rows)
        if not candidates:
            return None
        rank = {FOTMOB_FULL: 0, FOTMOB_DISCOVERY: 1, FOTMOB_NO_DATA: 2}
        return sorted(
            candidates,
            key=lambda row: (rank.get(row.status, 3), -row.sample_size, row.season_id),
        )[0]

    def refresh(self, *, force: bool = False) -> bool:
        now = time.monotonic()
        ttl = max(0.0, float(getattr(self.settings, "smart_universe_cache_ttl_seconds", 300.0)))
        if not force and self._loaded_at is not None and now - self._loaded_at < ttl:
            return False

        derived_catalog = self._derive_fotmob_catalog()
        derived_market = self._derive_market_capability()
        self.database.upsert_fotmob_coverage_catalog_rows(derived_catalog)
        self.database.upsert_tipico_market_capability_rows(derived_market)

        coverage = [FotMobCoverage.from_row(row) for row in self.database.fotmob_coverage_catalog_rows()]
        market = [TipicoMarketCapability.from_row(row) for row in self.database.tipico_market_capability_rows()]
        links = self.database.competition_provider_link_rows("FOTMOB")

        self._coverage = coverage
        self._coverage_by_league = defaultdict(list)
        self._coverage_by_name = defaultdict(list)
        self._coverage_by_name_only = defaultdict(list)
        for row in coverage:
            self._coverage_by_league[row.fotmob_league_id].append(row)
            name = normalize_label(row.league_name)
            country = normalize_label(row.country)
            if name:
                self._coverage_by_name[(name, country)].append(row)
                self._coverage_by_name_only[name].append(row)

        self._market = {row.competition_id: row for row in market}
        self._market_by_name = {}
        for row in market:
            key = (normalize_label(row.competition_name), normalize_label(row.competition_country))
            if key[0]:
                self._market_by_name[key] = row

        self._links_by_tipico_id = {}
        self._links_by_name = {}
        for row in links:
            provider_id = str(row["provider_competition_id"] or "").strip()
            if not provider_id:
                continue
            self._links_by_tipico_id[str(row["internal_competition_id"])] = provider_id
            key = (
                normalize_label(row["tipico_competition_name"]),
                normalize_label(row["tipico_country"]),
            )
            if key[0]:
                self._links_by_name[key] = provider_id

        self._loaded_at = now
        self._last_refresh_at = _now_iso()
        return True

    def _ensure_loaded(self) -> None:
        try:
            self.refresh()
        except Exception:
            if self.logger is not None:
                self.logger.exception("Smart live-universe refresh failed")
            # A capability failure must never stop Tipico feed collection.
            if self._loaded_at is None:
                self._loaded_at = time.monotonic()

    @staticmethod
    def _event_season(event: LiveEvent) -> str | None:
        raw = event.raw_data if isinstance(event.raw_data, dict) else {}
        for key in ("season_id", "seasonId", "season", "seasonLabel"):
            value = raw.get(key)
            if value not in (None, ""):
                return str(value)
        return None

    def _coverage_for_event(self, event: LiveEvent) -> FotMobCoverage | None:
        competition_id = str(event.competition_id) if event.competition_id is not None else ""
        provider_id = self._links_by_tipico_id.get(competition_id, competition_id)
        season = self._event_season(event)
        if provider_id:
            rows = self._coverage_by_league.get(provider_id, [])
            if season:
                exact = [row for row in rows if row.season_id == season or row.season_label == season]
                selected = self._choose_coverage(exact)
                if selected is not None:
                    return selected
                # A known league with an unseen season must go through
                # discovery again; do not inherit an old season's NO_DATA or
                # FULL decision forever.
                if rows:
                    return None
            selected = self._choose_coverage(rows)
            if selected is not None:
                return selected

        name = normalize_label(event.competition_name)
        country = normalize_label(event.competition_country)
        linked = self._links_by_name.get((name, country))
        if linked:
            linked_rows = self._coverage_by_league.get(linked, [])
            if season:
                exact = [row for row in linked_rows if row.season_id == season or row.season_label == season]
                selected = self._choose_coverage(exact)
                if selected is not None:
                    return selected
                if linked_rows:
                    return None
            selected = self._choose_coverage(linked_rows)
            if selected is not None:
                return selected
        name_rows = self._coverage_by_name.get((name, country), [])
        if season:
            exact = [row for row in name_rows if row.season_id == season or row.season_label == season]
            selected = self._choose_coverage(exact)
            if selected is not None:
                return selected
            if name_rows:
                return None
        selected = self._choose_coverage(name_rows)
        if selected is not None:
            return selected
        name_only_rows = self._coverage_by_name_only.get(name, [])
        if season:
            exact = [row for row in name_only_rows if row.season_id == season or row.season_label == season]
            selected = self._choose_coverage(exact)
            if selected is not None:
                return selected
            if name_only_rows:
                return None
        return self._choose_coverage(name_only_rows)

    def _market_for_event(self, event: LiveEvent) -> TipicoMarketCapability | None:
        competition_id = str(event.competition_id) if event.competition_id is not None else ""
        selected = self._market.get(competition_id)
        if selected is not None:
            return selected
        return self._market_by_name.get(
            (normalize_label(event.competition_name), normalize_label(event.competition_country))
        )

    def decide(self, event: LiveEvent, *, selected_event_id: str | None = None) -> LiveUniverseDecision:
        self._ensure_loaded()
        policy = competition_policy(event)
        coverage = self._coverage_for_event(event)
        market = self._market_for_event(event)
        fotmob_status = coverage.status if coverage is not None else FOTMOB_DISCOVERY
        market_status = market.status if market is not None else MARKET_DISCOVERY
        selected = selected_event_id is not None and str(selected_event_id) == str(event.event_id)

        if selected:
            priority = P0_SELECTED
            reason = "dashboard-selected event overrides background policy"
        elif policy == EXCLUDED_COMPETITION:
            priority = P4_IGNORE
            reason = "youth/reserve/friendly competition policy"
        elif fotmob_status == FOTMOB_FULL and market_status == MARKET_SUITABLE:
            priority = P1_STRATEGY_ELIGIBLE
            reason = "FotMob coverage and Tipico strategy markets are suitable"
        elif fotmob_status == FOTMOB_DISCOVERY or market_status == MARKET_DISCOVERY:
            priority = P2_DISCOVERY
            reason = "unknown or new provider capability requires controlled discovery"
        else:
            priority = P3_MINIMAL
            reason = "capability is known but not suitable for routine strategy processing"

        eligible = priority == P1_STRATEGY_ELIGIBLE
        detail_allowed = priority in {P0_SELECTED, P1_STRATEGY_ELIGIBLE, P2_DISCOVERY}
        probe_allowed = (
            priority in {P0_SELECTED, P1_STRATEGY_ELIGIBLE, P2_DISCOVERY}
            and (priority == P0_SELECTED or fotmob_status != FOTMOB_NO_DATA)
        )
        return LiveUniverseDecision(
            event_id=str(event.event_id),
            competition_id=(str(event.competition_id) if event.competition_id is not None else None),
            competition_name=event.competition_name,
            competition_country=event.competition_country,
            priority=priority,
            competition_policy=policy,
            fotmob_league_id=coverage.fotmob_league_id if coverage is not None else None,
            fotmob_season_id=coverage.season_id if coverage is not None else None,
            fotmob_status=fotmob_status,
            tipico_market_status=market_status,
            eligible_for_strategy=eligible,
            detail_allowed=detail_allowed,
            fotmob_probe_allowed=probe_allowed,
            reason=reason,
        )

    def summary(
        self,
        events: Iterable[LiveEvent],
        *,
        selected_event_id: str | None = None,
    ) -> dict[str, Any]:
        self._ensure_loaded()
        decisions = [self.decide(event, selected_event_id=selected_event_id) for event in events]
        counts = Counter(decision.priority for decision in decisions)
        return {
            "enabled": True,
            "total_live_events": len(decisions),
            "priorities": {
                P0_SELECTED: counts.get(P0_SELECTED, 0),
                P1_STRATEGY_ELIGIBLE: counts.get(P1_STRATEGY_ELIGIBLE, 0),
                P2_DISCOVERY: counts.get(P2_DISCOVERY, 0),
                P3_MINIMAL: counts.get(P3_MINIMAL, 0),
                P4_IGNORE: counts.get(P4_IGNORE, 0),
            },
            "p0_selected": counts.get(P0_SELECTED, 0),
            "p1_strategy_eligible": counts.get(P1_STRATEGY_ELIGIBLE, 0),
            "p2_discovery": counts.get(P2_DISCOVERY, 0),
            "p3_minimal": counts.get(P3_MINIMAL, 0),
            "p4_ignored": counts.get(P4_IGNORE, 0),
            "coverage_catalog_rows": len(self._coverage),
            "market_capability_rows": len(self._market),
            "last_refresh_at": self._last_refresh_at,
        }
