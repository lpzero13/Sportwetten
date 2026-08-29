"""Normalized data models used by the observer."""
from .competition import CompetitionMetadata
from .event import LiveEvent
from .event_state import EventState
from .market import EventDetails, Market, Outcome
from .snapshot import SNAPSHOT_TYPES, Snapshot

__all__ = [
    "CompetitionMetadata",
    "EventDetails",
    "EventState",
    "LiveEvent",
    "Market",
    "Outcome",
    "SNAPSHOT_TYPES",
    "Snapshot",
]
