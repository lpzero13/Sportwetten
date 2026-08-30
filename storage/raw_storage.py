"""Content-addressed storage for changed raw Tipico payloads."""

from __future__ import annotations

import hashlib
import gzip
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import zstandard as zstd
except ImportError:  # pragma: no cover - optional local fallback
    zstd = None


@dataclass(frozen=True, slots=True)
class RawStorageResult:
    changed: bool
    path: Path | None
    content_hash: str


class RawStorage:
    """Keep raw JSON for replay/debugging without storing identical payloads."""

    def __init__(
        self,
        root: Path | str,
        *,
        enabled: bool = True,
        compression: str = "json",
    ) -> None:
        self.root = Path(root)
        self.enabled = enabled
        requested = str(compression or "json").strip().lower()
        if requested in {"zstd", "zst"} and zstd is not None:
            self.compression = "zstd"
        elif requested in {"zstd", "zst", "gzip", "gz"}:
            self.compression = "gzip"
        else:
            self.compression = "json"

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

    def _suffix(self) -> str:
        return {"zstd": ".json.zst", "gzip": ".json.gz", "json": ".json"}[self.compression]

    def _write_payload(self, path: Path, canonical: str) -> None:
        if self.compression == "zstd" and zstd is not None:
            compressor = zstd.ZstdCompressor(level=3)
            with path.open("wb") as handle:
                with compressor.stream_writer(handle, closefd=False) as writer:
                    writer.write(canonical.encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
            return
        if self.compression == "gzip":
            with gzip.open(path, "wb", compresslevel=6) as handle:
                handle.write(canonical.encode("utf-8"))
            with path.open("r+b") as handle:
                os.fsync(handle.fileno())
            return
        path.write_text(
            json.dumps(json.loads(canonical), ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _read_payload(path: Path) -> Any:
        if path.suffix == ".zst" and zstd is not None:
            with path.open("rb") as handle:
                with zstd.ZstdDecompressor().stream_reader(handle) as reader:
                    data = reader.read()
            return json.loads(data.decode("utf-8"))
        if path.suffix == ".gz":
            with gzip.open(path, "rb") as handle:
                return json.loads(handle.read().decode("utf-8"))
        return json.loads(path.read_text(encoding="utf-8"))

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
        filename = f"{safe_kind}_{timestamp}_{content_hash[:12]}{self._suffix()}"
        path = directory / filename
        self._write_payload(path, canonical)
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
        matches = []
        for suffix in (".json.zst", ".json.gz", ".json"):
            matches.extend(directory.glob(f"{safe_kind}_*_{content_hash[:12]}{suffix}"))
        return matches[-1] if matches else None

    @classmethod
    def read(cls, path: Path | str) -> Any:
        """Read JSON, gzip JSON or zstd JSON produced by this storage."""

        return cls._read_payload(Path(path))

    def cleanup_older_than(
        self,
        *,
        days: int,
        preserve_kinds: tuple[str, ...] = ("paper_entries",),
        now: datetime | None = None,
    ) -> int:
        """Delete only old debug raw files; permanent paper-entry raw is kept."""

        if days < 0:
            return 0
        cutoff = (now or datetime.now().astimezone()).timestamp() - days * 86400
        removed = 0
        if not self.root.exists():
            return 0
        for path in self.root.rglob("*"):
            if not path.is_file() or path.name == ".latest_hash":
                continue
            if path.suffix not in {".json", ".gz", ".zst"}:
                continue
            try:
                relative_parts = path.relative_to(self.root).parts
                if any(kind in relative_parts for kind in preserve_kinds):
                    continue
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError:
                continue
        # A stale sidecar must not suppress a future debug payload after the
        # only file carrying its hash has expired.
        for sidecar in self.root.rglob(".latest_hash"):
            try:
                has_payload = any(
                    candidate.is_file()
                    and candidate.suffix in {".json", ".gz", ".zst"}
                    for candidate in sidecar.parent.iterdir()
                )
                if not has_payload:
                    sidecar.unlink()
            except OSError:
                continue
        return removed
