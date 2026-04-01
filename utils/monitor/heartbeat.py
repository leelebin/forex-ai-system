import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import psutil
except ModuleNotFoundError:  # pragma: no cover - runtime environment dependent
    psutil = None


class HeartbeatMonitor:
    def __init__(self, path: str = "heartbeat.txt", interval_sec: int = 600) -> None:
        self.path = Path(path)
        self.interval_sec = interval_sec
        self._stop_event = threading.Event()
        self._thread = None
        self._last_main_loop_tick = 0.0
        self._process = None
        if psutil is not None:
            self._process = psutil.Process()
            self._process.cpu_percent(interval=None)

    def tick(self) -> None:
        """Update main loop alive timestamp."""
        self._last_main_loop_tick = time.time()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.tick()
        self._thread = threading.Thread(target=self._run, name="heartbeat-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self._stop_event.wait(self.interval_sec)
            if self._stop_event.is_set():
                break

            stale_timeout = self.interval_sec + 30
            if time.time() - self._last_main_loop_tick > stale_timeout:
                continue

            self._write_heartbeat()

    def _write_heartbeat(self) -> None:
        memory_mb = None
        cpu_percent = None
        if self._process is not None:
            memory_mb = self._process.memory_info().rss / 1024 / 1024
            cpu_percent = self._process.cpu_percent(interval=None)

        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "RUNNING",
            "memory_mb": round(memory_mb, 2) if memory_mb is not None else None,
            "cpu_percent": round(cpu_percent, 2) if cpu_percent is not None else None,
            "metrics_source": "psutil" if self._process is not None else "unavailable",
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
