"""Small, UI-independent models used by the V0.3 analysis pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class CanonicalOutcome:
    """One Tipico outcome after deterministic semantic normalization.

    The raw identifiers and labels deliberately remain alongside the canonical
    fields.  A mapping can therefore be audited without losing the provider
    payload that caused it.
    """

    event_id: str
    market_id: str
    outcome_id: str
    canonical_type: str
    scope: str
    period: str
    side: str | None
    line: float | None
    team: str | None
    odds: float | None
    status: str
    available: bool
    observed_at: str
    raw_market_type: str
    raw_market_caption: str
    raw_fixed_param: str
    raw_choice_param: str | None
    raw_outcome_caption: str
    settlement_scope: str = "UNKNOWN"
    normalizer_version: str = "v0.3.1"

    @property
    def is_open(self) -> bool:
        return self.available and self.odds is not None and self.status not in {
            "paused",
            "suspended",
            "stopped",
            "closed",
            "inactive",
        }

    @property
    def source_label(self) -> str:
        """Human-readable provider source shown next to a selected quote."""

        market = self.raw_market_caption or self.raw_market_type or self.market_id
        outcome = self.raw_outcome_caption or self.outcome_id
        return f"Tipico · {market} · {outcome}"


@dataclass(slots=True)
class BestOddsResult:
    target: str
    status: str
    selected: CanonicalOutcome | None
    candidates: list[CanonicalOutcome] = field(default_factory=list)
    alternatives: list[CanonicalOutcome] = field(default_factory=list)
    stale_candidates: list[CanonicalOutcome] = field(default_factory=list)
    unavailable_candidates: list[CanonicalOutcome] = field(default_factory=list)

    @property
    def source(self) -> str | None:
        return self.selected.source_label if self.selected else None


@dataclass(slots=True)
class EquivalentMarket:
    """A settlement-compatible candidate set for one semantic outcome."""

    target: str
    label: str
    status: str
    candidates: list[CanonicalOutcome] = field(default_factory=list)
    best_odds: BestOddsResult | None = None
    explanation: str = ""


@dataclass(slots=True)
class OddsPair:
    """Two-way over/under quote pair used to remove Tipico's margin."""

    under: CanonicalOutcome
    over: CanonicalOutcome
    line: float
    source: str

    @property
    def q_under(self) -> float | None:
        return self.under.odds

    @property
    def q_over(self) -> float | None:
        return self.over.odds


@dataclass(slots=True)
class ProbabilityResult:
    status: str
    p0: float | None = None
    p1: float | None = None
    p2_plus: float | None = None
    p01: float | None = None
    zero_pair: OddsPair | None = None
    one_plus_pair: OddsPair | None = None
    source: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def distribution(self) -> dict[str, float | None]:
        return {"0": self.p0, "1": self.p1, "2+": self.p2_plus}


@dataclass(slots=True)
class StrategyResult:
    strategy_type: str
    strategy_version: str
    status: str
    label: str
    total_stake: float
    q_zero: float | None = None
    q_two_plus: float | None = None
    source_zero: str | None = None
    source_two_plus: str | None = None
    payout_before_rounding: float | None = None
    stake_zero: float | None = None
    stake_two_plus: float | None = None
    payout_zero: float | None = None
    payout_two_plus: float | None = None
    payout_difference: float | None = None
    covered_profit: float | None = None
    win_roi: float | None = None
    loss_exact_one: float | None = None
    p1_max: float | None = None
    p1_tipico: float | None = None
    p1_buffer: float | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def is_rankable(self) -> bool:
        return self.status == "OK" and self.p1_buffer is not None and self.win_roi is not None


@dataclass(slots=True)
class MarketAnalysis:
    event_id: str
    observed_at: str
    normalized_outcomes: list[CanonicalOutcome]
    zero_equivalence: EquivalentMarket
    two_plus_equivalence: EquivalentMarket
    probability: ProbabilityResult
    strategy: StrategyResult
    known_outcome_count: int
    unknown_outcome_count: int
    warnings: list[str] = field(default_factory=list)
    snapshot_id: int | None = None

    @property
    def normalization_coverage(self) -> float:
        total = self.known_outcome_count + self.unknown_outcome_count
        return self.known_outcome_count / total if total else 0.0


def canonical_to_row(outcome: CanonicalOutcome, *, snapshot_id: int | None = None) -> dict[str, Any]:
    """Convert a canonical object to stable DB column names."""

    return {
        "event_id": outcome.event_id,
        "market_id": outcome.market_id,
        "outcome_id": outcome.outcome_id,
        "snapshot_id": snapshot_id,
        "observed_at": outcome.observed_at,
        "canonical_type": outcome.canonical_type,
        "scope": outcome.scope,
        "period": outcome.period,
        "side": outcome.side,
        "line": outcome.line,
        "team": outcome.team,
        "odds": outcome.odds,
        "status": outcome.status,
        "available": int(outcome.available),
        "raw_market_type": outcome.raw_market_type,
        "raw_market_caption": outcome.raw_market_caption,
        "raw_fixed_param": outcome.raw_fixed_param,
        "raw_choice_param": outcome.raw_choice_param,
        "raw_outcome_caption": outcome.raw_outcome_caption,
        "settlement_scope": outcome.settlement_scope,
        "normalizer_version": outcome.normalizer_version,
    }
