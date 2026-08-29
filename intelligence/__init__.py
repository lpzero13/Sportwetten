"""Deterministic Tipico market-intelligence primitives for V0.3."""

from .models import (
    BestOddsResult,
    CanonicalOutcome,
    EquivalentMarket,
    MarketAnalysis,
    OddsPair,
    ProbabilityResult,
    StrategyResult,
)
from .normalizer import NORMALIZER_VERSION, MarketNormalizer, normalize_event_details
from .service import MarketIntelligenceService

__all__ = [
    "BestOddsResult",
    "CanonicalOutcome",
    "EquivalentMarket",
    "MarketAnalysis",
    "MarketIntelligenceService",
    "NORMALIZER_VERSION",
    "OddsPair",
    "ProbabilityResult",
    "StrategyResult",
    "MarketNormalizer",
    "normalize_event_details",
]
