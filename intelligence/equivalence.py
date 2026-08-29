"""Conservative equivalence resolution for the V0.3 target outcomes."""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

from models.event import LiveEvent

from .models import CanonicalOutcome, EquivalentMarket
from .odds import select_best_odds


TARGET_ZERO = "ZERO_REMAINING_GOALS"
TARGET_TWO_PLUS = "TWO_OR_MORE_REMAINING_GOALS"


def _goal_count(event: LiveEvent) -> int | None:
    if event.score_home is None or event.score_away is None:
        return None
    return int(event.score_home) + int(event.score_away)


def _line_equal(left: float | None, right: float | None) -> bool:
    return left is not None and right is not None and abs(left - right) < 1e-9


def _scope_verified(event: LiveEvent, outcome: CanonicalOutcome) -> bool:
    """Only equate markets when regular-time settlement is explicit."""

    if outcome.settlement_scope != "REGULATION_NO_EXTRA_TIME":
        return False
    # Tipico's event payload normally provides both flags. Unknown flags are
    # intentionally not treated as a safe equivalent settlement scope.
    if event.extra_time is not False or event.penalties is not False:
        return False
    return True


def _candidate_reason(target: str, outcome: CanonicalOutcome, total_goals: int | None) -> str:
    if outcome.canonical_type == "NEXT_GOAL_NONE":
        return "Next Goal = None"
    if outcome.canonical_type == "REMAINING_TOTAL_UNDER":
        return f"Resttore Unter {outcome.line:g}" if outcome.line is not None else "Resttore Unter"
    if outcome.canonical_type == "REMAINING_TOTAL_OVER":
        return f"Resttore Über {outcome.line:g}" if outcome.line is not None else "Resttore Über"
    if outcome.canonical_type == "MATCH_TOTAL_UNDER":
        return f"Match Total Unter {outcome.line:g}" if outcome.line is not None else "Match Total Unter"
    if outcome.canonical_type == "MATCH_TOTAL_OVER":
        return f"Match Total Über {outcome.line:g}" if outcome.line is not None else "Match Total Über"
    del target, total_goals
    return outcome.source_label


def resolve_equivalences(
    event: LiveEvent,
    outcomes: Iterable[CanonicalOutcome],
    *,
    max_age_seconds: int = 10,
    now: datetime | None = None,
) -> tuple[EquivalentMarket, EquivalentMarket]:
    """Resolve ZERO and TWO+ using score-aware dynamic match-total lines."""

    all_outcomes = list(outcomes)
    total_goals = _goal_count(event)
    zero: list[CanonicalOutcome] = []
    two_plus: list[CanonicalOutcome] = []

    for outcome in all_outcomes:
        if not _scope_verified(event, outcome):
            continue
        if outcome.canonical_type == "REMAINING_TOTAL_UNDER" and _line_equal(outcome.line, 0.5):
            zero.append(outcome)
        elif outcome.canonical_type == "NEXT_GOAL_NONE":
            zero.append(outcome)
        elif (
            outcome.canonical_type == "MATCH_TOTAL_UNDER"
            and total_goals is not None
            and _line_equal(outcome.line, total_goals + 0.5)
        ):
            zero.append(outcome)

        if outcome.canonical_type == "REMAINING_TOTAL_OVER" and _line_equal(outcome.line, 1.5):
            two_plus.append(outcome)
        elif (
            outcome.canonical_type == "MATCH_TOTAL_OVER"
            and total_goals is not None
            and _line_equal(outcome.line, total_goals + 1.5)
        ):
            two_plus.append(outcome)

    def build(
        target: str,
        label: str,
        candidates: list[CanonicalOutcome],
    ) -> EquivalentMarket:
        if not candidates:
            status = "MISSING_EQUIVALENT_MARKET"
            explanation = "Keine settlement-kompatible Zielquote im aktuellen Snapshot."
        else:
            selected_scope_candidates = [
                outcome
                for outcome in all_outcomes
                if (
                    (
                        target == TARGET_ZERO
                        and (
                            outcome.canonical_type in {
                                "REMAINING_TOTAL_UNDER",
                                "NEXT_GOAL_NONE",
                                "MATCH_TOTAL_UNDER",
                            }
                        )
                    )
                    or (
                        target == TARGET_TWO_PLUS
                        and outcome.canonical_type
                        in {"REMAINING_TOTAL_OVER", "MATCH_TOTAL_OVER"}
                    )
                )
            ]
            if selected_scope_candidates and not candidates:
                status = "EQUIVALENCE_UNVERIFIED"
                explanation = "Markt erkannt, aber Settlement-Scope ist nicht sicher kompatibel."
            else:
                status = "EQUIVALENT"
                explanation = "; ".join(
                    _candidate_reason(target, item, total_goals) for item in candidates
                )
        best = (
            select_best_odds(
                target,
                candidates,
                now=now,
                max_age_seconds=max_age_seconds,
            )
            if status == "EQUIVALENT"
            else None
        )
        if best is not None and best.status != "OK":
            explanation = f"{explanation} · {best.status}"
        return EquivalentMarket(
            target=target,
            label=label,
            status=status,
            candidates=candidates,
            best_odds=best,
            explanation=explanation,
        )

    # The branch for unverified scopes needs to inspect target-like outcomes
    # independently so the UI can explain why a candidate was withheld.
    zero_like = [
        item
        for item in all_outcomes
        if (
            item.canonical_type == "REMAINING_TOTAL_UNDER"
            and _line_equal(item.line, 0.5)
        )
        or item.canonical_type == "NEXT_GOAL_NONE"
        or (
            item.canonical_type == "MATCH_TOTAL_UNDER"
            and total_goals is not None
            and _line_equal(item.line, total_goals + 0.5)
        )
    ]
    two_like = [
        item
        for item in all_outcomes
        if (
            item.canonical_type == "REMAINING_TOTAL_OVER"
            and _line_equal(item.line, 1.5)
        )
        or (
            item.canonical_type == "MATCH_TOTAL_OVER"
            and total_goals is not None
            and _line_equal(item.line, total_goals + 1.5)
        )
    ]

    zero_market = build(TARGET_ZERO, "0 verbleibende Tore", zero)
    two_market = build(TARGET_TWO_PLUS, "2+ verbleibende Tore", two_plus)
    if not zero and zero_like:
        zero_market.status = "EQUIVALENCE_UNVERIFIED"
        zero_market.candidates = zero_like
        zero_market.explanation = "Zielmarkt erkannt, Settlement-Scope aber nicht verifiziert."
    if not two_plus and two_like:
        two_market.status = "EQUIVALENCE_UNVERIFIED"
        two_market.candidates = two_like
        two_market.explanation = "Zielmarkt erkannt, Settlement-Scope aber nicht verifiziert."
    return zero_market, two_market
