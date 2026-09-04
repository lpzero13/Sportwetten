#!/usr/bin/env python3
"""Write the install-time identity manifest for the current checkout."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# When the installer invokes this file by absolute path, Python puts
# ``deploy/`` on sys.path rather than the project root.  Add the root before
# importing application modules so the production installer follows the same
# path as ``python -m research.ml_v060``.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import Settings
from runtime_status import write_deployment_manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--installer-version", default="v0611")
    args = parser.parse_args()
    root = args.root.resolve()
    manifest = write_deployment_manifest(
        root,
        settings=Settings.from_env(root),
        installer_version=args.installer_version,
    )
    print(manifest["artifact_hash"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
