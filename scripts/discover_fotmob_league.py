#!/usr/bin/env python3
"""Discover FotMob league metadata and real season IDs."""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fotmob.history_cli import main


if __name__ == "__main__":
    raise SystemExit(main(["seasons", *sys.argv[1:]]))
