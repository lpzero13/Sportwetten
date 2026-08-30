"""Optional FotMob discovery, matching and live-enrichment boundary.

The package is intentionally independent from the Tipico collector.  Nothing
in :mod:`tipico` or the Tipico collector imports this module; callers opt in
through :class:`fotmob.service.FotMobService` and the ``FOTMOB_ENABLED`` flag.
"""

from .models import (
    FOTMOB_SNAPSHOT_TYPES,
    FotMobEvent,
    FotMobFetchResult,
    FotMobMatch,
    FotMobSnapshot,
    FotMobStats,
)
from .parser import parse_fotmob_payload

__all__ = [
    "FOTMOB_SNAPSHOT_TYPES",
    "FotMobEvent",
    "FotMobFetchResult",
    "FotMobMatch",
    "FotMobSnapshot",
    "FotMobStats",
    "parse_fotmob_payload",
]
