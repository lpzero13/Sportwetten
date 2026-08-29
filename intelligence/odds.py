"""Freshness-aware best-odds selection."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from .models import BestOddsResult, CanonicalOutcome


BLOCKED_STATUSES = {"paused", "suspended", "stopped", "closed", "inactive"}


def parse_observed_at(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        moment = value
    else:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def age_seconds(
    observed_at: str | datetime,
    *,
    now: datetime | None = None,
) -> float:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return max(0.0, (current - parse_observed_at(observed_at)).total_seconds())


def is_fresh(
    outcome: CanonicalOutcome,
    *,
    now: datetime | None = None,
    max_age_seconds: int = 10,
) -> bool:
    return age_seconds(outcome.observed_at, now=now) <= max(1, int(max_age_seconds))


def _is_open_quote(outcome: CanonicalOutcome) -> bool:
    return (
        outcome.available
        and outcome.odds is not None
        and outcome.odds > 1
        and outcome.status.casefold() not in BLOCKED_STATUSES
    )


def select_best_odds(
    target: str,
    candidates: Iterable[CanonicalOutcome],
    *,
    now: datetime | None = None,
    max_age_seconds: int = 10,
) -> BestOddsResult:
    all_candidates = list(candidates)
    fresh = [
        item
        for item in all_candidates
        if is_fresh(item, now=now, max_age_seconds=max_age_seconds)
    ]
    stale = [item for item in all_candidates if item not in fresh]
    fresh_open = [item for item in fresh if _is_open_quote(item)]
    stale_open = [item for item in stale if _is_open_quote(item)]
    unavailable = [
        item
        for item in all_candidates
        if item not in fresh_open and item not in stale_open
    ]
    ordered = sorted(
        fresh_open,
        key=lambda item: (float(item.odds or 0), item.market_id, item.outcome_id),
        reverse=True,
    )
    selected = ordered[0] if ordered else None
    if selected is not None:
        status = "OK"
    elif stale_open:
        status = "STALE_QUOTES"
    elif all_candidates:
        status = "UNAVAILABLE_QUOTES"
    else:
        status = "MISSING_EQUIVALENT_MARKET"
    return BestOddsResult(
        target=target,
        status=status,
        selected=selected,
        candidates=all_candidates,
        alternatives=ordered[1:],
        stale_candidates=stale_open,
        unavailable_candidates=unavailable,
    )
