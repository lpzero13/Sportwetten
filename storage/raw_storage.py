"""Content-addressed storage for changed raw Tipico payloads."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class RawStorageResult:
    changed: bool
    path: Path | None
    content_hash: str


class RawStorage:
    """Keep raw JSON for replay/debugging without storing identical payloads."""

    def __init__(self, root: Path | str, *, enabled: bool = True) -> None:
        self.root = Path(root)
        self.enabled = enabled

    @staticmethod
    def _canonical(payload: Any) -> str:
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )

    @staticmethod
    def _safe(value: str) -> str:
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "unknown"

    def store(
        self,
        kind: str,
        identity: str,
        payload: Any,
        *,
        observed_at: str | None = None,
        halftime: bool = False,
    ) -> RawStorageResult:
        canonical = self._canonical(payload)
        content_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if not self.enabled:
            return RawStorageResult(False, None, content_hash)

        observed = datetime.fromisoformat(
            (observed_at or datetime.now().astimezone().isoformat()).replace("Z", "+00:00")
        )
        date_dir = observed.astimezone().strftime("%Y-%m-%d")
        safe_kind = self._safe(kind)
        safe_identity = self._safe(identity)
        directory = self.root / date_dir / safe_kind / safe_identity
        if kind == "live":
            directory = self.root / date_dir / "live"
        if halftime:
            directory = directory / "halftime"
        directory.mkdir(parents=True, exist_ok=True)

        latest_hash_path = directory / ".latest_hash"
        if latest_hash_path.exists():
            try:
                if latest_hash_path.read_text(encoding="utf-8").strip() == content_hash:
                    return RawStorageResult(False, None, content_hash)
            except OSError:
                pass

        timestamp = observed.astimezone().strftime("%H%M%S_%f")
        filename = f"{safe_kind}_{timestamp}_{content_hash[:12]}.json"
        path = directory / filename
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str)
            + "\n",
            encoding="utf-8",
        )
        latest_hash_path.write_text(content_hash + "\n", encoding="utf-8")
        return RawStorageResult(True, path, content_hash)

    def path_for_hash(
        self,
        kind: str,
        identity: str,
        content_hash: str,
        *,
        observed_at: str | None = None,
        halftime: bool = False,
    ) -> Path | None:
        """Resolve an already archived payload without writing a duplicate."""

        if not self.enabled:
            return None
        observed = datetime.fromisoformat(
            (observed_at or datetime.now().astimezone().isoformat()).replace("Z", "+00:00")
        )
        date_dir = observed.astimezone().strftime("%Y-%m-%d")
        safe_kind = self._safe(kind)
        safe_identity = self._safe(identity)
        directory = self.root / date_dir / safe_kind / safe_identity
        if kind == "live":
            directory = self.root / date_dir / "live"
        if halftime:
            directory = directory / "halftime"
        matches = sorted(directory.glob(f"{safe_kind}_*_{content_hash[:12]}.json"))
        return matches[-1] if matches else None
