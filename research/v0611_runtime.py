"""Runtime safety primitives introduced for V0.6.1.1.

This module deliberately contains no training code.  It owns the two pieces
of state that must be safe before a research process can do expensive work:
the single-process lock and the immutable deployment identity manifest.
Both APIs are usable from tests and from the CLI without importing the
collector or starting a network request.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import platform
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


LOCK_SCHEMA_VERSION = "v0611"
DEPLOYMENT_SCHEMA_VERSION = "v0611"
RESEARCH_PROCESS_MARKERS = ("research.ml_v060", "research/ml_v060", "research\\ml_v060")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dump(path: Path, value: Mapping[str, Any], *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
    mode = 0o640
    descriptor = os.open(str(path), flags, mode)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if descriptor != -1:
            os.close(descriptor)


def _atomic_json_replace(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        _json_dump(temporary, value)
        os.replace(temporary, path)
        try:
            directory_fd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    finally:
        try:
            temporary.unlink()
        except OSError:
            pass


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
    return dict(value) if isinstance(value, Mapping) else None


def _process_info(pid: int) -> dict[str, Any]:
    """Return conservative process facts; unavailable facts stay ``None``."""

    result: dict[str, Any] = {
        "pid": int(pid),
        "alive": False,
        "process_start_time": None,
        "cmdline": [],
        "name": None,
    }
    try:
        import psutil

        process = psutil.Process(int(pid))
        result["alive"] = bool(process.is_running() and process.status() != psutil.STATUS_ZOMBIE)
        if not result["alive"]:
            return result
        try:
            result["process_start_time"] = float(process.create_time())
        except (OSError, psutil.Error, TypeError, ValueError):
            pass
        try:
            result["cmdline"] = [str(item) for item in (process.cmdline() or [])]
        except (OSError, psutil.Error):
            result["cmdline"] = []
        try:
            result["name"] = process.name()
        except (OSError, psutil.Error):
            pass
        return result
    except ImportError:
        if int(pid) == os.getpid():
            result["alive"] = True
            result["cmdline"] = list(sys.argv)
        return result
    except Exception:
        return result


def _command_is_research_app(command: Any) -> bool:
    values = command if isinstance(command, (list, tuple)) else [command]
    for item in values:
        if item is None:
            continue
        token = str(item).casefold().replace("\\", "/")
        if token == "research.ml_v060":
            return True
        if any(marker.casefold().replace("\\", "/") in token for marker in RESEARCH_PROCESS_MARKERS[1:]):
            return True
    return False


class ResearchLockError(RuntimeError):
    """Base class for fail-closed ML lock decisions."""

    def __init__(self, message: str, *, inspection: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.inspection = dict(inspection or {})


class AlreadyRunningError(ResearchLockError):
    """A second research process was detected with a matching live owner."""


class StaleLockError(ResearchLockError):
    """A lock exists but its owner cannot be trusted; recovery is explicit."""


@dataclass(slots=True)
class LockInspection:
    status: str
    path: str
    locked: bool
    owner_alive: bool | None
    owner_identity_match: bool | None
    metadata: dict[str, Any] | None = None
    owner: dict[str, Any] | None = None
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        metadata = self.metadata or {}
        owner = self.owner or {}
        return {
            "status": self.status,
            "path": self.path,
            "locked": self.locked,
            "owner_alive": self.owner_alive,
            "owner_identity_match": self.owner_identity_match,
            "metadata": self.metadata,
            "owner": self.owner,
            "reason": self.reason,
            # Flatten the diagnostic contract as well as retaining the raw
            # metadata/owner objects.  This makes ``lock-status`` useful to a
            # shell/operator without requiring knowledge of the JSON nesting.
            "run_id": metadata.get("run_id"),
            "owner_pid": metadata.get("owner_pid", metadata.get("pid")),
            "owner_ppid": metadata.get("owner_ppid", metadata.get("ppid")),
            "owner_hostname": metadata.get("owner_hostname", metadata.get("hostname")),
            "process_start_time": metadata.get(
                "process_start_time", metadata.get("owner_process_start_time")
            ),
            "lock_created_at": metadata.get("lock_created_at", metadata.get("lock_time")),
            "owner_started_at": metadata.get("lock_created_at", metadata.get("lock_time")),
            "phase": metadata.get("phase"),
            "mode": metadata.get("mode"),
            "requested_experiments": metadata.get("requested_experiments"),
            "dataset_hash": metadata.get("dataset_hash"),
            "code_commit": metadata.get("code_commit"),
            "owner_command": metadata.get("command_line"),
            "owner_process_alive": owner.get("alive", self.owner_alive),
            "owner_process_start_time": owner.get("process_start_time"),
        }


class ResearchRunLock:
    """Crash-safe, diagnostic single-owner lock for explicit ML commands."""

    def __init__(
        self,
        root: Path | str,
        *,
        path: Path | str | None = None,
        run_id: str | None = None,
        mode: str = "unknown",
        requested_experiments: int | list[str] | None = None,
        dataset_hash: str | None = None,
        code_commit: str | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.path = Path(path or self.root / "research" / "runtime" / "ml_run.lock.json").resolve()
        self.run_id = str(run_id or f"run_{datetime.now(timezone.utc):%Y%m%dT%H%M%S}_{uuid.uuid4().hex[:8]}")
        self.mode = str(mode)
        self.requested_experiments = requested_experiments
        self.dataset_hash = dataset_hash
        self.code_commit = code_commit
        self._acquired = False

    @staticmethod
    def process_start_time(pid: int | None = None) -> float | None:
        value = _process_info(int(pid or os.getpid())).get("process_start_time")
        return float(value) if value is not None else None

    def _metadata(self) -> dict[str, Any]:
        process_start = self.process_start_time()
        lock_time = _now()
        return {
            "schema_version": LOCK_SCHEMA_VERSION,
            "lock_schema_version": LOCK_SCHEMA_VERSION,
            "run_id": self.run_id,
            "pid": os.getpid(),
            "ppid": os.getppid(),
            "hostname": socket.gethostname(),
            "owner_pid": os.getpid(),
            "owner_ppid": os.getppid(),
            "owner_hostname": socket.gethostname(),
            "process_start_time": process_start,
            "owner_process_start_time": process_start,
            "lock_time": lock_time,
            "lock_created_at": lock_time,
            "command_line": list(sys.argv),
            "mode": self.mode,
            "requested_experiments": self.requested_experiments,
            "phase": "LOCK_ACQUIRED",
            "dataset_hash": self.dataset_hash,
            "code_commit": self.code_commit,
        }

    def inspect(self) -> LockInspection:
        if not self.path.exists():
            return LockInspection("UNLOCKED", str(self.path), False, False, False, reason="lock file absent")
        metadata = _read_json(self.path)
        if metadata is None:
            return LockInspection(
                "STALE_LOCK_DETECTED",
                str(self.path),
                True,
                None,
                False,
                reason="lock file is not valid JSON; recovery is fail-closed",
            )
        pid_value = metadata.get("owner_pid", metadata.get("pid"))
        try:
            pid = int(pid_value)
        except (TypeError, ValueError):
            return LockInspection(
                "STALE_LOCK_DETECTED", str(self.path), True, False, False, metadata,
                reason="lock owner PID is invalid",
            )
        owner = _process_info(pid)
        alive = bool(owner.get("alive"))
        stored_start = metadata.get(
            "owner_process_start_time", metadata.get("process_start_time")
        )
        current_start = owner.get("process_start_time")
        start_matches = True
        if stored_start is not None and current_start is not None:
            try:
                start_matches = abs(float(stored_start) - float(current_start)) <= 2.0
            except (TypeError, ValueError):
                start_matches = False
        elif stored_start is not None:
            start_matches = False
        command_matches = _command_is_research_app(owner.get("cmdline"))
        # A lock acquired by this Python process is unambiguous even when the
        # test runner/embedded caller does not contain the CLI marker.  For a
        # different PID the expected research command is mandatory.
        identity_match = bool(alive and start_matches and (pid == os.getpid() or command_matches))
        if identity_match:
            status = "LOCKED"
            reason = "live research owner matches PID and process identity"
        else:
            status = "STALE_LOCK_DETECTED"
            reason = "owner is dead or PID/process-start/command identity does not match"
        owner["command_matches_research_app"] = command_matches
        owner["process_start_matches"] = start_matches
        return LockInspection(status, str(self.path), True, alive, identity_match, metadata, owner, reason)

    def acquire(self) -> dict[str, Any]:
        if self._acquired:
            return self.inspect().as_dict()
        metadata = self._metadata()
        try:
            _json_dump(self.path, metadata, exclusive=True)
        except FileExistsError:
            inspection = self.inspect()
            if inspection.status == "LOCKED":
                raise AlreadyRunningError(
                    "another ML research process is already running; inspect the lock owner before retrying",
                    inspection=inspection.as_dict(),
                )
            raise StaleLockError(
                "a stale or unverifiable ML lock exists; run clear-stale-lock only after validating the owner",
                inspection=inspection.as_dict(),
            )
        self._acquired = True
        return self.inspect().as_dict()

    def update_phase(self, phase: str, **fields: Any) -> dict[str, Any]:
        inspection = self.inspect()
        metadata = dict(inspection.metadata or {})
        owner_pid = metadata.get("owner_pid", metadata.get("pid"))
        if not self._acquired or metadata.get("run_id") != self.run_id or int(owner_pid or -1) != os.getpid():
            raise ResearchLockError("only the lock owner may update lock metadata", inspection=inspection.as_dict())
        metadata.update(fields)
        metadata["phase"] = str(phase).upper()
        metadata["updated_at"] = _now()
        _atomic_json_replace(self.path, metadata)
        return metadata

    def rebind_run_id(self, run_id: str) -> dict[str, Any]:
        """Attach an already-acquired command lock to an existing plan run.

        ``run`` and ``resume`` may discover a pre-existing planned run after
        acquiring the command lock.  Rebinding is allowed only by the same
        owner before execution starts; it keeps the lock metadata and the
        registry identity aligned without releasing the exclusion window.
        """

        inspection = self.inspect()
        metadata = dict(inspection.metadata or {})
        owner_pid = metadata.get("owner_pid", metadata.get("pid"))
        if (
            not self._acquired
            or metadata.get("run_id") != self.run_id
            or int(owner_pid or -1) != os.getpid()
        ):
            raise ResearchLockError(
                "only the lock owner may rebind run metadata",
                inspection=inspection.as_dict(),
            )
        self.run_id = str(run_id)
        metadata["run_id"] = self.run_id
        metadata["updated_at"] = _now()
        _atomic_json_replace(self.path, metadata)
        return metadata

    def release(self, *, final_phase: str = "FINISHED") -> bool:
        if not self._acquired:
            return False
        try:
            inspection = self.inspect()
            metadata = inspection.metadata or {}
            owner_pid = metadata.get("owner_pid", metadata.get("pid"))
            if metadata.get("run_id") == self.run_id and int(owner_pid or -1) == os.getpid():
                metadata = dict(metadata)
                metadata["phase"] = str(final_phase).upper()
                metadata["finished_at"] = _now()
                _atomic_json_replace(self.path, metadata)
                self.path.unlink(missing_ok=True)
                self._acquired = False
                return True
        except (OSError, TypeError, ValueError):
            return False
        return False

    def __enter__(self) -> "ResearchRunLock":
        self.acquire()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.release(final_phase="FINISHED" if exc_type is None else "FAILED")

    @staticmethod
    def _active_research_processes(exclude_pid: int | None = None) -> list[dict[str, Any]]:
        try:
            import psutil
        except ImportError:
            return []
        result: list[dict[str, Any]] = []
        try:
            processes = psutil.process_iter(["pid", "cmdline", "name"])
            for process in processes:
                if exclude_pid is not None and int(process.info.get("pid") or -1) == int(exclude_pid):
                    continue
                if _command_is_research_app(process.info.get("cmdline") or process.info.get("name")):
                    result.append({
                        "pid": int(process.info.get("pid")),
                        "cmdline": list(process.info.get("cmdline") or []),
                        "name": process.info.get("name"),
                    })
        except Exception:
            return []
        return result

    @classmethod
    def clear_stale_lock(cls, root: Path | str, *, path: Path | str | None = None) -> dict[str, Any]:
        probe = cls(root, path=path)
        inspection = probe.inspect()
        if inspection.status != "STALE_LOCK_DETECTED":
            return {
                "status": "REFUSED",
                "removed": False,
                "reason": "lock is not stale; a live matching owner must never be cleared",
                "inspection": inspection.as_dict(),
            }
        active = cls._active_research_processes(
            int(
                (inspection.metadata or {}).get(
                    "owner_pid", (inspection.metadata or {}).get("pid")
                )
                or -1
            )
        )
        if active:
            return {
                "status": "REFUSED",
                "removed": False,
                "reason": "another active research process was found",
                "active_research_processes": active,
                "inspection": inspection.as_dict(),
            }
        # A corrupt/unreadable lock is deliberately not removable: proof of
        # owner death is impossible in that case.  A parsed lock is removable
        # only when the owner is dead or its PID identity is mismatched.
        if inspection.metadata is None:
            return {
                "status": "REFUSED",
                "removed": False,
                "reason": "owner identity cannot be proven for a corrupt lock file",
                "inspection": inspection.as_dict(),
            }
        try:
            probe.path.unlink()
        except FileNotFoundError:
            return {"status": "PASS", "removed": False, "reason": "lock disappeared before clear"}
        except OSError as exc:
            return {"status": "FAIL", "removed": False, "reason": str(exc)}
        return {
            "status": "PASS",
            "removed": True,
            "reason": "dead or identity-mismatched owner and no active research process",
            "inspection": inspection.as_dict(),
        }


def deployment_manifest_path(root: Path | str) -> Path:
    return Path(root).resolve() / "DEPLOYMENT_MANIFEST.json"


def _git(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    return value or None


def source_tree_hash(root: Path | str) -> str:
    """Hash source/deployment bytes deterministically, excluding runtime data."""

    root_path = Path(root).resolve()
    excluded_dirs = {
        ".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache",
        "data", "logs", "work", "archived_logs", "latest_logs", "models", "cache",
        "output", "outputs", "dist", "build",
        "runtime",
    }
    excluded_names = {
        "DEPLOYMENT_MANIFEST.json", "ml_registry.sqlite", "collector_status.json",
        "ml_run.lock.json",
    }
    digest = hashlib.sha256()
    if not root_path.exists():
        return digest.hexdigest()
    files: list[Path] = []
    allowed_suffixes = {
        ".py", ".yaml", ".yml", ".sh", ".service", ".timer", ".example",
        ".txt", ".md", ".json", ".toml", ".cfg", ".ini", ".env",
    }
    # Prune runtime/data directories while walking.  ``Path.rglob`` still
    # traverses excluded directories before filtering their entries, which is
    # unacceptable on a container with a large historical archive.
    for directory, directory_names, file_names in os.walk(root_path, topdown=True):
        directory_names[:] = sorted(
            name for name in directory_names if name not in excluded_dirs
        )
        directory_path = Path(directory)
        for name in sorted(file_names):
            path = directory_path / name
            if not path.is_file():
                continue
            relative = path.relative_to(root_path)
            if path.name in excluded_names or path.name.startswith(("V060_", "V061_", "V0611_")) or path.name.endswith((".pyc", ".pyo", ".tmp", ".db", ".sqlite")):
                continue
            # Only source and deployment descriptors belong to the release
            # hash; this prevents accidental local reports or arbitrary user
            # files from changing the installed artifact identity.
            if path.suffix.casefold() not in allowed_suffixes and path.name not in {"requirements.txt", "Dockerfile"}:
                continue
            files.append(path)
    for path in sorted(files, key=lambda item: item.relative_to(root_path).as_posix()):
        relative = path.relative_to(root_path).as_posix().encode("utf-8")
        try:
            content = path.read_bytes()
        except OSError:
            continue
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def load_deployment_manifest(root: Path | str) -> dict[str, Any] | None:
    return _read_json(deployment_manifest_path(root))


def write_deployment_manifest(
    root: Path | str,
    *,
    settings: Any | None = None,
    installer_version: str = "v0611",
    app_version: str | None = None,
    research_version: str = "0.6.1.1",
) -> dict[str, Any]:
    root_path = Path(root).resolve()
    commit = _git(root_path, "rev-parse", "HEAD") or os.getenv("WETTEN_GIT_COMMIT") or "unknown"
    branch = _git(root_path, "branch", "--show-current") or os.getenv("WETTEN_GIT_BRANCH") or "unknown"
    repository = _git(root_path, "config", "--get", "remote.origin.url") or os.getenv("WETTEN_SOURCE_REPOSITORY") or "unknown"
    tree_hash = source_tree_hash(root_path)
    try:
        from runtime_status import APP_VERSION, config_fingerprint

        resolved_app_version = app_version or os.getenv("WETTEN_APP_VERSION", APP_VERSION)
        fingerprint = config_fingerprint(settings) if settings is not None else None
    except Exception:
        resolved_app_version = app_version or os.getenv("WETTEN_APP_VERSION") or "unknown"
        fingerprint = None
    artifact_hash = hashlib.sha256(
        json.dumps(
            {
                "source_commit": commit,
                "source_tree_hash": tree_hash,
                "app_version": resolved_app_version,
                "research_version": research_version,
                "installer_version": installer_version,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    manifest = {
        "deployment_schema_version": DEPLOYMENT_SCHEMA_VERSION,
        "deployed_at": _now(),
        "installer_version": installer_version,
        "source_repository": repository,
        "source_commit": commit,
        "source_branch": branch,
        "source_tree_hash": tree_hash,
        "artifact_hash": artifact_hash,
        "app_version": resolved_app_version,
        "research_version": research_version,
        "python_version": platform.python_version(),
        "config_fingerprint": fingerprint,
        "working_tree_dirty": "NOT_APPLICABLE" if _git(root_path, "rev-parse", "--is-inside-work-tree") is None else bool(_git(root_path, "status", "--porcelain")),
    }
    _atomic_json_replace(deployment_manifest_path(root_path), manifest)
    return manifest


def deployment_status(root: Path | str, *, check_integrity: bool = False) -> dict[str, Any]:
    root_path = Path(root).resolve()
    manifest = load_deployment_manifest(root_path)
    result: dict[str, Any] = {
        "status": "PASS" if manifest else "MISSING",
        "manifest_path": str(deployment_manifest_path(root_path)),
        "manifest_present": manifest is not None,
        "manifest": manifest,
        "integrity_checked": bool(check_integrity),
        "current_tree_hash": None,
        "tree_hash_match": None,
    }
    if manifest is None:
        return result
    if check_integrity:
        current = source_tree_hash(root_path)
        result["current_tree_hash"] = current
        result["tree_hash_match"] = current == manifest.get("source_tree_hash")
        if not result["tree_hash_match"]:
            result["status"] = "FAIL"
    return result


__all__ = [
    "AlreadyRunningError",
    "DEPLOYMENT_SCHEMA_VERSION",
    "LockInspection",
    "LOCK_SCHEMA_VERSION",
    "ResearchLockError",
    "ResearchRunLock",
    "StaleLockError",
    "deployment_manifest_path",
    "deployment_status",
    "load_deployment_manifest",
    "source_tree_hash",
    "write_deployment_manifest",
]
