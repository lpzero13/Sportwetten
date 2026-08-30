#!/usr/bin/env python3
"""Unified FotMob V0.5.2 historical CLI."""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fotmob.history_cli import main


if __name__ == "__main__":
    raise SystemExit(main())
