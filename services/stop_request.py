"""Cooperative local shutdown without changing systemd or provider settings."""
import os
from pathlib import Path
import time


class StopRequest:
    def __init__(self) -> None:
        value = os.getenv("WETTEN_STOP_FILE")
        self.path = Path(value) if value else None

    def is_set(self) -> bool:
        return bool(self.path and self.path.exists())

    def wait(self, seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        while not self.is_set() and time.monotonic() < deadline:
            time.sleep(min(.2, max(0, deadline - time.monotonic())))
        return self.is_set()
