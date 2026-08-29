"""Competition metadata collected without applying any sport filters."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class CompetitionMetadata:
    competition_id: str
    competition_name: str
    country_or_region: str | None
    first_seen_at: str
    last_seen_at: str
    events_observed: int = 0
