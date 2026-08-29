"""Deterministic Tipico-to-canonical market normalization.

Tipico's German captions are useful for display, but they are not a stable
semantic contract. The normalizer therefore decides from the provider type,
fixedParam and choiceParam first and keeps every raw field on the result.
"""

from __future__ import annotations

import re
from typing import Any

from models.market import EventDetails, Market, Outcome

from .models import CanonicalOutcome


NORMALIZER_VERSION = "v0.3.1"

BLOCKED_STATUSES = {"paused", "suspended", "stopped", "closed", "inactive"}

REMAINING_TOTAL_TYPES = {
    "points-more-less-rest",
    "points-more-less-rest-halftime",
}
MATCH_TOTAL_TYPES = {"points-more-less", "points-more-less-than"}
TEAM_REMAINING_TYPES = {
    "team-points-more-less",
    "team-points-more-less-halftime",
}
NEXT_GOAL_TYPES = {"next-point", "next-point-halftime"}


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _token(value: Any) -> str:
    return (
        _text(value)
        .casefold()
        .replace("ä", "ae")
        .replace("ö", "oe")
        .replace("ü", "ue")
        .replace("ß", "ss")
    )


def _fixed_parts(value: str) -> tuple[str | None, float | None]:
    """Return a possible selector and the final numeric line."""

    text = value.replace(",", ".").strip()
    selector: str | None = None
    if ":" in text:
        selector, text = (part.strip() for part in text.split(":", 1))
    numbers = re.findall(r"[-+]?\d+(?:\.\d+)?", text)
    line = float(numbers[-1]) if numbers else None
    return selector or None, line


def _choice(outcome: Outcome) -> str:
    return _token(outcome.choice_param or outcome.caption)


def _over_under(choice: str) -> str | None:
    if choice in {"+", "over", "more", "greater", "o", "ueber", "above"}:
        return "OVER"
    if choice in {"-", "under", "less", "lower", "u", "below", "unter"}:
        return "UNDER"
    return None


def _next_goal_side(choice: str) -> str | None:
    if choice in {"1", "home", "team1", "heim", "1st"}:
        return "HOME"
    if choice in {"2", "away", "team2", "gast", "2nd"}:
        return "AWAY"
    if choice in {
        "x",
        "0",
        "none",
        "no goal",
        "no-goal",
        "no next goal",
        "kein tor",
        "kein weiteres tor",
        "keines",
    }:
        return "NONE"
    return None


def _btts_side(choice: str) -> str | None:
    if choice in {"j", "ja", "yes", "y", "true", "1"}:
        return "YES"
    if choice in {"n", "nein", "no", "false", "0"}:
        return "NO"
    return None


def _rest_result_side(choice: str) -> str | None:
    if choice in {"1", "home", "heim"}:
        return "HOME"
    if choice in {"x", "draw", "unentschieden"}:
        return "DRAW"
    if choice in {"2", "away", "gast"}:
        return "AWAY"
    return None


def _event_is_half_time(details: EventDetails) -> bool:
    event = details.event
    return (
        _token(getattr(event, "period", "")) in {"half_time", "halftime", "ht"}
        or _token(getattr(event, "display_minute", "")) == "hz"
    )


def _event_is_second_half(details: EventDetails) -> bool:
    event = details.event
    if _event_is_half_time(details):
        return False
    try:
        if getattr(event, "section_number", None) is not None:
            return int(event.section_number) >= 2
    except (TypeError, ValueError):
        pass
    match = re.search(r"(\d+)", _text(getattr(event, "display_minute", "")))
    return bool(match and int(match.group(1)) >= 46)


def _remaining_period(details: EventDetails, market_type: str) -> str:
    if "halftime" in market_type:
        return "HALF_SPECIFIC"
    if _event_is_half_time(details):
        return "SECOND_HALF"
    if _event_is_second_half(details):
        return "REGULATION_REMAINING"
    return "REGULATION_REMAINING"


def _team_name(details: EventDetails, selector: str | None, caption: str) -> str | None:
    selector_token = _token(selector)
    if selector_token in {"1", "home", "heim"}:
        return getattr(details.event, "home_team", None)
    if selector_token in {"2", "away", "gast"}:
        return getattr(details.event, "away_team", None)
    caption_token = _token(caption)
    home = _text(getattr(details.event, "home_team", ""))
    away = _text(getattr(details.event, "away_team", ""))
    if home and _token(home) in caption_token:
        return home
    if away and _token(away) in caption_token:
        return away
    return selector or None


def _base_fields(
    details: EventDetails,
    market: Market,
    outcome: Outcome,
    observed_at: str,
) -> dict[str, Any]:
    market_status = _token(market.status)
    outcome_status = _token(outcome.status)
    status = outcome_status or market_status or ("open" if outcome.is_available else "unavailable")
    available = bool(outcome.is_available) and market_status not in BLOCKED_STATUSES
    odds = outcome.odds if available and outcome.odds is not None and outcome.odds > 1 else None
    if odds is None and available:
        available = False
        status = status or "unavailable"
    return {
        "event_id": str(details.event.event_id),
        "market_id": str(market.market_id),
        "outcome_id": str(outcome.outcome_id),
        "canonical_type": "UNKNOWN",
        "scope": "UNKNOWN",
        "period": "UNKNOWN",
        "side": None,
        "line": None,
        "team": None,
        "odds": odds,
        "status": status,
        "available": available,
        "observed_at": observed_at,
        "raw_market_type": _text(market.type),
        "raw_market_caption": _text(market.caption),
        "raw_fixed_param": _text(market.fixed_param),
        "raw_choice_param": outcome.choice_param,
        "raw_outcome_caption": _text(outcome.caption),
        "settlement_scope": "UNKNOWN",
        "normalizer_version": NORMALIZER_VERSION,
    }


class MarketNormalizer:
    """Normalize one event's complete current market graph."""

    version = NORMALIZER_VERSION

    def normalize(
        self,
        details: EventDetails,
        *,
        observed_at: str,
    ) -> list[CanonicalOutcome]:
        normalized: list[CanonicalOutcome] = []
        for market in details.markets:
            for outcome in market.outcomes:
                normalized.append(self.normalize_outcome(details, market, outcome, observed_at))
        return normalized

    def normalize_outcome(
        self,
        details: EventDetails,
        market: Market,
        outcome: Outcome,
        observed_at: str,
    ) -> CanonicalOutcome:
        fields = _base_fields(details, market, outcome, observed_at)
        market_type = _token(market.type)
        selector, line = _fixed_parts(market.fixed_param)
        choice = _choice(outcome)

        if market_type in REMAINING_TOTAL_TYPES:
            side = _over_under(choice)
            if side:
                fields.update(
                    canonical_type=f"REMAINING_TOTAL_{side}",
                    scope="REMAINING",
                    period=_remaining_period(details, market_type),
                    side=side,
                    line=line,
                    settlement_scope=(
                        "FIRST_HALF_ONLY" if "halftime" in market_type else "REGULATION_NO_EXTRA_TIME"
                    ),
                )
        elif market_type in MATCH_TOTAL_TYPES:
            side = _over_under(choice)
            if side:
                fields.update(
                    canonical_type=f"MATCH_TOTAL_{side}",
                    scope="MATCH",
                    period="REGULATION",
                    side=side,
                    line=line,
                    settlement_scope="REGULATION_NO_EXTRA_TIME",
                )
        elif market_type in TEAM_REMAINING_TYPES:
            side = _over_under(choice)
            if side:
                fields.update(
                    canonical_type=f"TEAM_REMAINING_{side}",
                    scope="TEAM_REMAINING",
                    period=("HALF_SPECIFIC" if "halftime" in market_type else "REGULATION_REMAINING"),
                    side=side,
                    line=line,
                    team=_team_name(details, selector, market.caption),
                    settlement_scope=(
                        "FIRST_HALF_ONLY" if "halftime" in market_type else "REGULATION_NO_EXTRA_TIME"
                    ),
                )
        elif market_type in NEXT_GOAL_TYPES:
            side = _next_goal_side(choice)
            if side:
                fields.update(
                    canonical_type=f"NEXT_GOAL_{side}",
                    scope="REMAINING",
                    period=("HALF_SPECIFIC" if "halftime" in market_type else "REGULATION_REMAINING"),
                    side=side,
                    settlement_scope=(
                        "FIRST_HALF_ONLY" if "halftime" in market_type else "REGULATION_NO_EXTRA_TIME"
                    ),
                )
        elif market_type == "section-points-more-less":
            # A section total is only useful for this milestone when the
            # provider explicitly identifies the second half. First-half
            # totals remain UNKNOWN instead of being treated as full-match or
            # rest-of-match markets.
            if _token(selector) in {"2", "second", "second half", "2nd"}:
                side = _over_under(choice)
                if side:
                    fields.update(
                        canonical_type=f"REMAINING_TOTAL_{side}",
                        scope="REMAINING",
                        period="SECOND_HALF",
                        side=side,
                        line=line,
                        settlement_scope="REGULATION_NO_EXTRA_TIME",
                    )
        elif market_type == "score-both":
            side = _btts_side(choice)
            if side:
                fields.update(
                    canonical_type=f"BTTS_{side}",
                    scope="MATCH",
                    period="REGULATION",
                    side=side,
                    settlement_scope="REGULATION_NO_EXTRA_TIME",
                )
        elif market_type == "standard-rest":
            side = _rest_result_side(choice)
            if side:
                fields.update(
                    canonical_type=f"REST_1X2_{side}",
                    scope="REMAINING",
                    period="REGULATION_REMAINING",
                    side=side,
                    settlement_scope="REGULATION_NO_EXTRA_TIME",
                )

        return CanonicalOutcome(**fields)


def normalize_event_details(
    details: EventDetails,
    *,
    observed_at: str,
    normalizer: MarketNormalizer | None = None,
) -> list[CanonicalOutcome]:
    """Functional entry point used by services, tests and integrations."""

    return (normalizer or MarketNormalizer()).normalize(details, observed_at=observed_at)
