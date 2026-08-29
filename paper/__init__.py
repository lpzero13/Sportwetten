"""Paper-trading domain services for Tipico strategy validation."""

from .engine import evaluate_signal, calculate_stake, settle_scores
from .models import PaperPortfolio, SettlementResult, SignalDecision
from .service import PaperTradingService

__all__ = [
    "PaperPortfolio",
    "PaperTradingService",
    "SettlementResult",
    "SignalDecision",
    "calculate_stake",
    "evaluate_signal",
    "settle_scores",
]
