"""Tipico-implied remaining-goal probabilities."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Iterable

from models.event import LiveEvent

from .models import CanonicalOutcome, OddsPair, ProbabilityResult
from .odds import select_best_odds


def normalize_two_way(q_under: float, q_over: float) -> tuple[float, float]:
    """Remove the two-way overround using reciprocal odds."""

    if q_under <= 1 or q_over <= 1:
        raise ValueError("Decimal odds must be greater than 1.")
    inverse_under = 1.0 / float(q_under)
    inverse_over = 1.0 / float(q_over)
    denominator = inverse_under + inverse_over
    if denominator <= 0:
        raise ValueError("Reciprocal odds sum must be positive.")
    return inverse_under / denominator, inverse_over / denominator


def _line_equal(left: float | None, right: float | None) -> bool:
    return left is not None and right is not None and abs(left - right) < 1e-9


def _pair_for_line(
    outcomes: list[CanonicalOutcome],
    *,
    under_type: str,
    over_type: str,
    line: float,
    now: datetime | None,
    max_age_seconds: int,
) -> OddsPair | None:
    grouped: dict[str, list[CanonicalOutcome]] = defaultdict(list)
    for outcome in outcomes:
        if _line_equal(outcome.line, line) and outcome.canonical_type in {
            under_type,
            over_type,
        }:
            grouped[outcome.market_id].append(outcome)

    pairs: list[OddsPair] = []
    for market_id, market_outcomes in grouped.items():
        under = select_best_odds(
            f"{under_type}:{line:g}",
            [item for item in market_outcomes if item.canonical_type == under_type],
            now=now,
            max_age_seconds=max_age_seconds,
        )
        over = select_best_odds(
            f"{over_type}:{line:g}",
            [item for item in market_outcomes if item.canonical_type == over_type],
            now=now,
            max_age_seconds=max_age_seconds,
        )
        if under.selected is None or over.selected is None:
            continue
        pairs.append(
            OddsPair(
                under=under.selected,
                over=over.selected,
                line=line,
                source=f"Tipico-Paar · Markt {market_id}",
            )
        )
    if not pairs:
        return None
    return max(
        pairs,
        key=lambda pair: (
            float(pair.under.odds or 0) + float(pair.over.odds or 0),
            pair.under.market_id,
        ),
    )


def _has_stale_or_unavailable(
    outcomes: list[CanonicalOutcome],
    *,
    under_type: str,
    over_type: str,
    line: float,
) -> bool:
    return any(
        _line_equal(outcome.line, line)
        and outcome.canonical_type in {under_type, over_type}
        for outcome in outcomes
    )


class ProbabilityEngine:
    """Calculate the three buckets 0 / exactly 1 / 2+ without clamping."""

    def calculate(
        self,
        event: LiveEvent,
        outcomes: Iterable[CanonicalOutcome],
        *,
        now: datetime | None = None,
        max_age_seconds: int = 10,
        allow_match_fallback: bool = True,
    ) -> ProbabilityResult:
        all_outcomes = [
            item
            for item in outcomes
            if item.settlement_scope == "REGULATION_NO_EXTRA_TIME"
        ]
        if event.extra_time is not False or event.penalties is not False:
            return ProbabilityResult(
                status="UNVERIFIED_SETTLEMENT_SCOPE",
                warnings=["Extra Time/Penalties sind nicht explizit ausgeschlossen."],
            )
        if event.score_home is None or event.score_away is None:
            return ProbabilityResult(
                status="MISSING_SCORE",
                warnings=["Der aktuelle Spielstand fehlt für dynamische Match-Total-Linien."],
            )

        total_goals = int(event.score_home) + int(event.score_away)
        zero_pair = _pair_for_line(
            all_outcomes,
            under_type="REMAINING_TOTAL_UNDER",
            over_type="REMAINING_TOTAL_OVER",
            line=0.5,
            now=now,
            max_age_seconds=max_age_seconds,
        )
        one_plus_pair = _pair_for_line(
            all_outcomes,
            under_type="REMAINING_TOTAL_UNDER",
            over_type="REMAINING_TOTAL_OVER",
            line=1.5,
            now=now,
            max_age_seconds=max_age_seconds,
        )
        source_parts: list[str] = []

        if zero_pair is not None:
            source_parts.append("Rest U/O 0,5")
        elif allow_match_fallback:
            zero_pair = _pair_for_line(
                all_outcomes,
                under_type="MATCH_TOTAL_UNDER",
                over_type="MATCH_TOTAL_OVER",
                line=total_goals + 0.5,
                now=now,
                max_age_seconds=max_age_seconds,
            )
            if zero_pair is not None:
                source_parts.append(f"Match U/O {total_goals + 0.5:g} (Fallback)")

        if one_plus_pair is not None:
            source_parts.append("Rest U/O 1,5")
        elif allow_match_fallback:
            one_plus_pair = _pair_for_line(
                all_outcomes,
                under_type="MATCH_TOTAL_UNDER",
                over_type="MATCH_TOTAL_OVER",
                line=total_goals + 1.5,
                now=now,
                max_age_seconds=max_age_seconds,
            )
            if one_plus_pair is not None:
                source_parts.append(f"Match U/O {total_goals + 1.5:g} (Fallback)")

        if zero_pair is None or one_plus_pair is None:
            possible_stale = (
                _has_stale_or_unavailable(
                    all_outcomes,
                    under_type="REMAINING_TOTAL_UNDER",
                    over_type="REMAINING_TOTAL_OVER",
                    line=0.5,
                )
                or _has_stale_or_unavailable(
                    all_outcomes,
                    under_type="REMAINING_TOTAL_UNDER",
                    over_type="REMAINING_TOTAL_OVER",
                    line=1.5,
                )
            )
            return ProbabilityResult(
                status="STALE_OR_MISSING_MARKETS" if possible_stale else "MISSING_MARKETS",
                zero_pair=zero_pair,
                one_plus_pair=one_plus_pair,
                source="; ".join(source_parts) or None,
                warnings=[
                    "Für P0 und P(0/1) werden jeweils vollständige, frische U/O-Paare benötigt."
                ],
            )

        try:
            p0, _ = normalize_two_way(
                float(zero_pair.under.odds),
                float(zero_pair.over.odds),
            )
            p01, _ = normalize_two_way(
                float(one_plus_pair.under.odds),
                float(one_plus_pair.over.odds),
            )
        except (TypeError, ValueError):
            return ProbabilityResult(
                status="INVALID_ODDS",
                zero_pair=zero_pair,
                one_plus_pair=one_plus_pair,
                source="; ".join(source_parts) or None,
            )

        p1 = p01 - p0
        p2_plus = 1.0 - p01
        values = (p0, p1, p2_plus)
        if p01 < p0 or any(value < 0 or value > 1 for value in values):
            return ProbabilityResult(
                status="INCONSISTENT_MARKETS",
                p0=p0,
                p1=p1,
                p2_plus=p2_plus,
                p01=p01,
                zero_pair=zero_pair,
                one_plus_pair=one_plus_pair,
                source="; ".join(source_parts) or None,
                warnings=[
                    "P(0/1) liegt unter P(0). Es wurde nichts geklammert oder korrigiert."
                ],
            )
        if abs(sum(values) - 1.0) > 1e-6:
            return ProbabilityResult(
                status="INVALID_DISTRIBUTION",
                p0=p0,
                p1=p1,
                p2_plus=p2_plus,
                p01=p01,
                zero_pair=zero_pair,
                one_plus_pair=one_plus_pair,
                source="; ".join(source_parts) or None,
            )
        return ProbabilityResult(
            status="OK",
            p0=p0,
            p1=p1,
            p2_plus=p2_plus,
            p01=p01,
            zero_pair=zero_pair,
            one_plus_pair=one_plus_pair,
            source="; ".join(source_parts) or None,
        )


def calculate_probability_distribution(
    event: LiveEvent,
    outcomes: Iterable[CanonicalOutcome],
    *,
    now: datetime | None = None,
    max_age_seconds: int = 10,
    allow_match_fallback: bool = True,
) -> ProbabilityResult:
    return ProbabilityEngine().calculate(
        event,
        outcomes,
        now=now,
        max_age_seconds=max_age_seconds,
        allow_match_fallback=allow_match_fallback,
    )
