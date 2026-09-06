"""Tipico-only historical research and replay utilities.

The package is deliberately independent from the live collector.  It opens a
SQLite source read-only, builds a versioned observation dataset, and runs
deterministic experiments without mutating the production database.
"""

from .runner import run_study
from .source import TipicoSource

__all__ = ["TipicoSource", "run_study"]
