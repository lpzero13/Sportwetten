"""Executable wrapper for ``python scripts/tipico_backtest.py ...``."""

from pathlib import Path
import sys

# Python puts ``scripts/`` on sys.path when this file is executed directly.
# Add the repository root so the package also works from any working folder.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tipico_research.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
