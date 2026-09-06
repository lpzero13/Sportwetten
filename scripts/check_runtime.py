"""Dependency preflight usable even in a freshly created virtual environment."""
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def main() -> int:
    try:
        from packaging.requirements import Requirement
    except ImportError:
        return 1
    missing = []
    for line in (Path(__file__).resolve().parents[1] / "requirements.txt").read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        requirement = Requirement(line)
        if requirement.marker and not requirement.marker.evaluate():
            continue
        try:
            installed = version(requirement.name)
            if installed not in requirement.specifier:
                missing.append(f"{requirement.name}: {installed} statt {requirement.specifier}")
        except PackageNotFoundError:
            missing.append(requirement.name)
    if missing:
        print("Abhängigkeiten werden benötigt: " + ", ".join(missing))
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
