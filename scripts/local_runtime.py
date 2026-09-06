"""Own the local UI, collector and paper worker as one restartable application.

No PID from a file is trusted for termination. Process ownership is checked
against exact entrypoint/root arguments (and creation time through psutil).
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
import webbrowser

import psutil

ENTRYPOINTS = {"ui": "app.py", "collector": "scripts/run_collector.py", "paper": "scripts/run_paper.py"}


def normalized(path: str | Path) -> str:
    return os.path.normcase(str(Path(path).resolve()))


def service_for(command: list[str], cwd: str, root: Path) -> str | None:
    if not command:
        return None
    args = command[1:]
    while args and args[0] in {"-u", "-B", "-E", "-I", "-s", "-S"}:
        args = args[1:]
    if not args or args[0] == "-c":
        return None
    if "--root" in args:
        index = args.index("--root") + 1
        if index >= len(args) or normalized(Path(cwd) / args[index]) != normalized(root):
            return None
    elif normalized(cwd) != normalized(root):
        return None
    for name, relative in ENTRYPOINTS.items():
        target = normalized(root / relative)
        # Only the executed entrypoint counts, never a path passed as data.
        if name == "ui":
            if len(args) < 4 or args[:3] != ["-m", "streamlit", "run"]:
                continue
            entrypoint = args[3]
        else:
            entrypoint = args[0]
        if not entrypoint.startswith("-") and normalized(Path(cwd) / entrypoint) == target:
            return name
    return None


def services(root: Path) -> dict[str, list[psutil.Process]]:
    result = {name: [] for name in ENTRYPOINTS}
    for process in psutil.process_iter(["pid", "ppid", "name", "cmdline", "cwd"]):
        try:
            if "python" not in (process.info["name"] or "").lower():
                continue
            name = service_for(process.info["cmdline"] or [], process.info["cwd"] or "", root)
            if name:
                result[name].append(process)
        except (psutil.Error, OSError, ValueError):
            continue
    # A Windows venv launcher and its child interpreter are one service.
    for name, processes in result.items():
        ids = {p.pid for p in processes}
        result[name] = [p for p in processes if p.info["ppid"] not in ids]
    return result


@contextmanager
def lock(path: Path, wait_seconds: float = 0):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        deadline = time.monotonic() + wait_seconds
        while True:
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.1)
        yield
    finally:
        handle.close()


def write_state(root: Path, value: dict) -> None:
    path = root / "logs/local-runtime.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def ui_ready(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/_stcore/health", timeout=1) as response:
            return response.status == 200 and response.read().strip() == b"ok"
    except (OSError, ValueError):
        return False


def fresh(value: str | None, seconds: int = 45) -> bool:
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - moment).total_seconds()
        return 0 <= age <= seconds
    except (ValueError, TypeError):
        return False


def health(root: Path, port: int) -> dict:
    found = services(root)
    result = {"database": str(root / "data/tipico.db"), "url": f"http://127.0.0.1:{port}",
              "services": {key: [p.pid for p in value] for key, value in found.items()},
              "ui_ready": ui_ready(port), "paper_last_seen": None, "collector_last_seen": None}
    try:
        connection = sqlite3.connect((root / "data/tipico.db").as_uri() + "?mode=ro", uri=True, timeout=1)
        try:
            row = connection.execute("SELECT setting_value FROM paper_runtime_settings WHERE setting_key='worker_last_seen_at'").fetchone()
            result["paper_last_seen"] = row[0] if row else None
        finally:
            connection.close()
    except sqlite3.Error:
        pass
    try:
        result["collector_last_seen"] = json.loads((root / "data/collector_status.json").read_text(encoding="utf-8"))["updated_at"]
    except (OSError, ValueError, KeyError):
        pass
    result["ready"] = bool(result["ui_ready"] and all(len(value) == 1 for value in found.values())
                           and fresh(result["paper_last_seen"]) and fresh(result["collector_last_seen"]))
    return result


def spawn(root: Path, name: str, command: list[str], env: dict | None = None) -> subprocess.Popen:
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with (root / f"logs/{name}.out.log").open("ab") as stdout, (root / f"logs/{name}.err.log").open("ab") as stderr:
        return subprocess.Popen(command, cwd=root, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                env=env, creationflags=flags, start_new_session=os.name != "nt")


def stop_services(root: Path, timeout: float = 45) -> list[int]:
    stop_path = root / "logs/local.stop"
    stop_path.touch()
    # Collector and paper see the stop file, finish requests and close SQLite.
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = services(root)
        if not current["collector"] and not current["paper"]:
            break
        time.sleep(.25)
    forced = []
    for name, processes in services(root).items():
        for process in processes:
            try:
                # Include only descendants of an exact project-owned process.
                targets = process.children(recursive=True) + [process]
                for target in reversed(targets):
                    try:
                        target.terminate()
                        if name != "ui":
                            forced.append(target.pid)
                    except psutil.NoSuchProcess:
                        pass
                _, alive = psutil.wait_procs(targets, timeout=5)
                for target in alive:
                    target.kill()
            except psutil.NoSuchProcess:
                pass
    return forced


def supervise(root: Path, port: int) -> None:
    with lock(root / "logs/local-supervisor.lock"):
        (root / "logs/local.stop").unlink(missing_ok=True)
        environment = dict(os.environ, PYTHONUNBUFFERED="1", WETTEN_STOP_FILE=str(root / "logs/local.stop"))
        commands = {
            "collector": [sys.executable, str(root / ENTRYPOINTS["collector"]), "--root", str(root)],
            "paper": [sys.executable, str(root / ENTRYPOINTS["paper"]), "--root", str(root), "--interval", "5"],
            "ui": [sys.executable, "-m", "streamlit", "run", str(root / "app.py"), "--server.headless", "true",
                   "--server.port", str(port), "--server.address", "127.0.0.1", "--browser.gatherUsageStats", "false"],
        }
        attempts = {name: 0 for name in commands}
        last_start = {name: 0.0 for name in commands}
        try:
            while not (root / "logs/local.stop").exists():
                found = services(root)
                for name, command in commands.items():
                    if not found[name] and time.monotonic() - last_start[name] >= min(30, 2 ** attempts[name]):
                        spawn(root, name, command, environment)
                        last_start[name] = time.monotonic()
                        attempts[name] = min(5, attempts[name] + 1)
                    elif found[name] and time.monotonic() - last_start[name] > 60:
                        attempts[name] = 0
                write_state(root, {"status": "RUNNING", "supervisor_pid": os.getpid(),
                                   "updated_at": datetime.now(timezone.utc).isoformat(), **health(root, port)})
                time.sleep(2)
        finally:
            forced = stop_services(root)
            write_state(root, {"status": "STOPPED", "forced_worker_pids": forced,
                               "updated_at": datetime.now(timezone.utc).isoformat(), **health(root, port)})


def start(root: Path, port: int, open_browser: bool, timeout: float) -> int:
    print("Tipico startet: Oberfläche und Hintergrunddienste werden geprüft …", flush=True)
    # Serialize double clicks before the detached supervisor acquires its lock.
    with lock(root / "logs/local-command.lock", wait_seconds=60):
        try:
            with lock(root / "logs/local-supervisor.lock"):
                running = False
        except OSError:
            running = True
        found = services(root)
        if any(len(group) > 1 for group in found.values()):
            raise RuntimeError("Mehrere Worker derselben Rolle gefunden. Erst STOP_TIPICO.bat ausführen.")
        with socket.socket() as probe:
            port_used = probe.connect_ex(("127.0.0.1", port)) == 0
        if port_used and not found["ui"]:
            raise RuntimeError(f"Port {port} ist durch eine andere Anwendung belegt; sie wird nicht beendet.")
        if found["ui"]:
            command = found["ui"][0].cmdline()
            if "--server.port" in command and command[command.index("--server.port") + 1] != str(port):
                raise RuntimeError("Die Projekt-Oberfläche läuft auf einem anderen Port. Erst stoppen oder denselben Port verwenden.")
        if not running:
            spawn(root, "local-runtime", [sys.executable, str(Path(__file__).resolve()), "supervise", "--root", str(root), "--port", str(port)])
        deadline = time.monotonic() + timeout
        browser_attempted = False
        while time.monotonic() < deadline:
            state = health(root, port)
            # A slow initial collection must not hide an already usable UI.
            # Full worker readiness remains independently visible in status.
            if state["ui_ready"] and open_browser and not browser_attempted:
                browser_attempted = True
                try:
                    opened = webbrowser.open(state["url"])
                except (OSError, webbrowser.Error):
                    opened = False
                if not opened:
                    print("Browser konnte nicht automatisch geöffnet werden. Bitte öffnen: " + state["url"], flush=True)
            if state["ready"]:
                print("Bereit: Oberfläche, Collector und Paper-Worker laufen. " + state["url"], flush=True)
                return 0
            if state["ui_ready"] and all(len(group) == 1 for group in state["services"].values()):
                print("Oberfläche bereit: " + state["url"], flush=True)
                print("Collector/Paper sind gestartet; aktuelle Statusmeldungen stehen noch aus. "
                      "Die Überwachung läuft weiter. STATUS_TIPICO.bat zeigt die vollständige Bereitschaft.", flush=True)
                return 0
            time.sleep(1)
        print(json.dumps(health(root, port), ensure_ascii=False, indent=2))
        print("Noch nicht betriebsbereit. Details: logs/local-runtime.err.log, collector.err.log und paper.err.log.\nDie Prozessverwaltung läuft weiter; STATUS_TIPICO.bat zeigt den Stand.")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "stop", "status", "supervise"])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--port", type=int, default=8506)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--timeout", type=float, default=45)
    args = parser.parse_args()
    root = args.root.resolve()
    if not all((root / path).is_file() for path in ENTRYPOINTS.values()):
        raise ValueError("Kein vollständiges Projektverzeichnis: " + str(root))
    (root / "logs").mkdir(exist_ok=True)
    if args.action == "supervise":
        supervise(root, args.port)
    elif args.action == "start":
        return start(root, args.port, not args.no_browser, args.timeout)
    elif args.action == "status":
        state = health(root, args.port)
        print(json.dumps(state, ensure_ascii=False, indent=2))
        return 0 if state["ready"] else 1
    else:
        with lock(root / "logs/local-command.lock", wait_seconds=60):
            (root / "logs/local.stop").touch()
            deadline = time.monotonic() + args.timeout + 10
            while True:
                try:
                    with lock(root / "logs/local-supervisor.lock"):
                        forced = stop_services(root, timeout=0)
                        break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Prozessverwaltung beendet noch laufende Anfragen. Status/Logs prüfen; kein neuer Start.")
                    time.sleep(.5)
            print("Oberfläche, Collector und Paper-Worker beendet. Daten bleiben erhalten.")
            if forced:
                print("Worker mussten beendet werden: " + str(forced))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as exc:
        print("Start/Stop fehlgeschlagen: " + str(exc), file=sys.stderr)
        raise SystemExit(1)
