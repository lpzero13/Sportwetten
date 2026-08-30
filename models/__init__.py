"""Normalized data models used by the observer."""
from .competition import CompetitionMetadata
from .event import LiveEvent
from .event_state import EventState
from .market import EventDetails, Market, Outcome
from .snapshot import STANDARD_SNAPSHOT_TYPES, SNAPSHOT_TYPES, Snapshot

__all__ = [
    "CompetitionMetadata",
    "EventDetails",
    "EventState",
    "LiveEvent",
    "Market",
    "Outcome",
    "SNAPSHOT_TYPES",
    "STANDARD_SNAPSHOT_TYPES",
    "Snapshot",
]
