"""Orchestration for the deterministic V0.3 analysis pipeline."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import Settings
from models.market import EventDetails
from storage.database import Database
from tipico.parser import parse_event_details

from .equivalence import resolve_equivalences
from .models import MarketAnalysis
from .normalizer import MarketNormalizer, normalize_event_details
from .probability import ProbabilityEngine
from .strategy import STRATEGY_VERSION, calculate_zero_or_2plus


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _event_flags_are_safe(event: Any) -> bool:
    return event.extra_time is False and event.penalties is False


class MarketIntelligenceService:
    """Combine normalization, equivalence, odds, probability and strategy."""

    def __init__(
        self,
        database: Database | None = None,
        settings: Settings | None = None,
        *,
        logger: logging.Logger | None = None,
        normalizer: MarketNormalizer | None = None,
    ) -> None:
        self.database = database
        self.settings = settings or Settings()
        self.logger = logger or logging.getLogger("tipico")
        self.normalizer = normalizer or MarketNormalizer()
        self.probability_engine = ProbabilityEngine()

    def analyze(
        self,
        details: EventDetails,
        *,
        observed_at: str | None = None,
        snapshot_id: int | None = None,
        total_stake: float | None = None,
        now: datetime | None = None,
        persist: bool = True,
    ) -> MarketAnalysis:
        current = now or datetime.now(timezone.utc)
        observation = observed_at or current.isoformat()
        stake = (
            float(total_stake)
            if total_stake is not None
            else float(self.settings.default_total_stake_eur)
        )
        normalized = normalize_event_details(
            details,
            observed_at=observation,
            normalizer=self.normalizer,
        )
        zero_equivalence, two_equivalence = resolve_equivalences(
            details.event,
            normalized,
            max_age_seconds=self.settings.max_live_odds_age_seconds,
            now=current,
        )
        zero_selected = (
            zero_equivalence.best_odds.selected
            if zero_equivalence.best_odds is not None
            else None
        )
        two_selected = (
            two_equivalence.best_odds.selected
            if two_equivalence.best_odds is not None
            else None
        )
        probability = self.probability_engine.calculate(
            details.event,
            normalized,
            now=current,
            max_age_seconds=self.settings.max_live_odds_age_seconds,
        )
        strategy = calculate_zero_or_2plus(
            zero_selected.odds if zero_selected is not None else None,
            two_selected.odds if two_selected is not None else None,
            total_stake=stake,
            p1_tipico=probability.p1 if probability.status == "OK" else None,
            source_zero=zero_selected.source_label if zero_selected is not None else None,
            source_two_plus=two_selected.source_label if two_selected is not None else None,
        )
        known = sum(item.canonical_type != "UNKNOWN" for item in normalized)
        unknown = len(normalized) - known
        warnings: list[str] = []
        if unknown:
            warnings.append(f"{unknown} Outcomes bleiben UNKNOWN und werden nicht semantisch verwendet.")
        if not _event_flags_are_safe(details.event):
            warnings.append("Extra Time/Penalties sind nicht sicher ausgeschlossen.")
        if probability.warnings:
            warnings.extend(probability.warnings)
        analysis = MarketAnalysis(
            event_id=str(details.event.event_id),
            observed_at=observation,
            normalized_outcomes=normalized,
            zero_equivalence=zero_equivalence,
            two_plus_equivalence=two_equivalence,
            probability=probability,
            strategy=strategy,
            known_outcome_count=known,
            unknown_outcome_count=unknown,
            warnings=warnings,
            snapshot_id=snapshot_id,
        )
        if persist and self.database is not None:
            self._persist(analysis)
        return analysis

    def _persist(self, analysis: MarketAnalysis) -> None:
        try:
            strategy = analysis.strategy
            status = (
                strategy.status
                if analysis.probability.status == "OK"
                else f"BLOCKED_{analysis.probability.status}"
            )
            self.database.replace_current_canonical_outcomes(  # type: ignore[union-attr]
                analysis.normalized_outcomes,
                event_id=analysis.event_id,
            )
            self.database.upsert_current_strategy_state(  # type: ignore[union-attr]
                {
                    "event_id": analysis.event_id,
                    "observed_at": analysis.observed_at,
                    "strategy_type": strategy.strategy_type,
                    "strategy_version": strategy.strategy_version or STRATEGY_VERSION,
                    "normalizer_version": self.normalizer.version,
                    "status": status,
                    "is_eligible": status == "OK",
                    "total_stake": strategy.total_stake,
                    "q_zero": strategy.q_zero,
                    "q_two_plus": strategy.q_two_plus,
                    "source_zero": strategy.source_zero,
                    "source_two_plus": strategy.source_two_plus,
                    "stake_zero": strategy.stake_zero,
                    "stake_two_plus": strategy.stake_two_plus,
                    "payout_zero": strategy.payout_zero,
                    "payout_two_plus": strategy.payout_two_plus,
                    "payout_difference": strategy.payout_difference,
                    "covered_profit": strategy.covered_profit,
                    "win_roi": strategy.win_roi,
                    "p1_max": strategy.p1_max,
                    "p1_tipico": strategy.p1_tipico,
                    "p1_buffer": strategy.p1_buffer,
                    "p_zero": analysis.probability.p0,
                    "p_one": analysis.probability.p1,
                    "p_two_plus": analysis.probability.p2_plus,
                }
            )
        except Exception as exc:  # persistence must never interrupt live polling
            self.logger.warning(
                "Could not persist V0.3 market intelligence for event %s: %s",
                analysis.event_id,
                exc,
            )

    @staticmethod
    def load_details_from_raw(path: str | Path) -> EventDetails:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Raw Tipico payload is not a JSON object.")
        return parse_event_details(payload)

    def analyze_snapshot_row(
        self,
        row: Any,
        *,
        total_stake: float | None = None,
        now: datetime | None = None,
    ) -> MarketAnalysis | None:
        path = row["raw_payload_path"] if row is not None else None
        if not path:
            return None
        details = self.load_details_from_raw(path)
        return self.analyze(
            details,
            observed_at=str(row["observed_at"]),
            snapshot_id=int(row["snapshot_id"]),
            total_stake=total_stake,
            now=now,
            persist=False,
        )
