"""One coherent, provider-ID-bound market observation for every paper decision."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from typing import Any
from .models import decimal_value


def encode(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str, allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(encode(value).encode()).hexdigest()


def timestamp(value: Any) -> datetime | None:
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except (ValueError, TypeError):
        return None


def is_halftime(event: dict[str, Any]) -> bool:
    return (
        str(event.get("period", "")).upper() in {"HALF_TIME", "HALFTIME", "HT"}
        or str(event.get("display_minute", event.get("display_time", ""))).upper() == "HZ"
    ) and str(event.get("status", "")).upper() not in {
        "FINISHED", "FINAL", "ENDED", "NO_LONGER_LIVE", "CANCELLED", "ABORTED",
    }


def power_under(q_under: float, q_over: float) -> float:
    """Two-way power de-vig; the exponent is solved rather than assumed."""
    inverse = (1 / q_under, 1 / q_over)
    low, high = 0.0, 100.0
    for _ in range(80):
        mid = (low + high) / 2
        if sum(p ** mid for p in inverse) > 1:
            low = mid
        else:
            high = mid
    return inverse[0] ** ((low + high) / 2)


def capture_market(event: Any, analysis: Any) -> dict[str, Any]:
    def selected(market: Any) -> dict[str, Any] | None:
        best = market.best_odds
        return asdict(best.selected) if best and best.selected else None

    probability = analysis.probability
    pairs = [pair for pair in (probability.zero_pair, probability.one_plus_pair) if pair]
    references = [asdict(outcome) for pair in pairs for outcome in (pair.under, pair.over)]
    zero = selected(analysis.zero_equivalence)
    two = selected(analysis.two_plus_equivalence)
    entry_ids = {(q["market_id"], q["outcome_id"]) for q in (zero, two) if q}
    reference_ids = {(q["market_id"], q["outcome_id"]) for q in references}
    power_p1 = None
    if probability.status == "OK" and len(pairs) == 2:
        p0 = power_under(pairs[0].q_under, pairs[0].q_over)
        p01 = power_under(pairs[1].q_under, pairs[1].q_over)
        if 0 <= p0 <= p01 <= 1:
            power_p1 = p01 - p0
    return {
        "schema_version": "paper_market_v2",
        "event_id": str(event.event_id),
        "observed_at": analysis.observed_at,
        "event": {key: getattr(event, key, None) for key in (
            "event_id", "competition_id", "competition_name", "competition_country",
            "home_team", "away_team", "kickoff_time", "sport", "status", "period",
            "display_minute", "score_home", "score_away", "ht_score_home", "ht_score_away",
            "extra_time", "penalties",
        )},
        "normalizer_version": analysis.strategy and (
            analysis.normalized_outcomes[0].normalizer_version if analysis.normalized_outcomes else "unknown"
        ),
        "strategy_version": analysis.strategy.strategy_version,
        "structure_status": analysis.strategy.status,
        "zero": zero,
        "two_plus": two,
        "probability": {
            "status": probability.status, "method": "NORMALIZATION",
            "p0": probability.p0, "p1": probability.p1, "p2_plus": probability.p2_plus,
            "p1_power": power_p1, "references": references,
            "execution_reference_overlap": bool(entry_ids & reference_ids),
            "warnings": probability.warnings,
        },
    }


def validate_entry(context: dict[str, Any], now: datetime, max_age: int) -> tuple[str, float | None]:
    """Validate the entire observation, not odds numerically close to an old quote."""
    event = context["event"]
    if not is_halftime(event):
        return "NOT_HALFTIME", None
    if event.get("extra_time") is not False or event.get("penalties") is not False:
        return "SETTLEMENT_SCOPE_UNKNOWN", None
    scores = [event.get(key) for key in ("score_home", "score_away", "ht_score_home", "ht_score_away")]
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in scores):
        return "HALFTIME_SCORE_MISSING", None
    if scores[:2] != scores[2:]:
        return "HALFTIME_SCORE_CONFLICT", None
    observed = timestamp(context.get("observed_at"))
    if observed is None:
        return "QUOTE_AGE_UNKNOWN", None
    age = (now - observed).total_seconds()
    if age < 0:
        return "OBSERVATION_FROM_FUTURE", age
    if age > max_age:
        return "QUOTE_TOO_OLD", age
    for key, under, line in (("zero", True, 0.5), ("two_plus", False, 1.5)):
        quote = context.get(key)
        if not quote:
            return "MISSING_QUOTES", age
        if quote.get("event_id") != context["event_id"] or quote.get("observed_at") != context["observed_at"]:
            return "QUOTE_SNAPSHOT_MISMATCH", age
        if not quote.get("market_id") or not quote.get("outcome_id"):
            return "QUOTE_ID_MISSING", age
        odds = decimal_value(quote.get("odds"))
        if odds is None or odds <= 1:
            return "INVALID_ODDS", age
        if quote.get("settlement_scope") != "REGULATION_NO_EXTRA_TIME":
            return "SETTLEMENT_SCOPE_UNKNOWN", age
        if not quote.get("available") or str(quote.get("status")).lower() in {
            "paused", "suspended", "stopped", "closed", "inactive",
        }:
            return "MARKET_NOT_OPEN", age
        kind = quote.get("canonical_type")
        direction = "UNDER" if under else "OVER"
        valid = (
            kind == f"REMAINING_TOTAL_{direction}" and quote.get("line") == line
            or kind == f"MATCH_TOTAL_{direction}" and quote.get("line") == sum(scores[:2]) + line
            or under and kind == "NEXT_GOAL_NONE"
        )
        if not valid:
            return "MARKET_SEMANTICS_MISMATCH", age
    return "OK", age
