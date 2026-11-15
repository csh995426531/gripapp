from __future__ import annotations

"""Per-host SSH concurrency gate: cap parallel sessions, queue the rest."""

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager


def max_per_host() -> int:
    raw = (os.environ.get("GRIP_SSH_MAX_PER_HOST") or "3").strip()
    try:
        n = int(raw)
    except ValueError:
        n = 3
    return max(1, min(n, 16))


class HostSSHGate:
    """One semaphore per resource name; acquire before any SSH to that host."""

    def __init__(self, *, limit: int | None = None) -> None:
        self._limit = max_per_host() if limit is None else max(1, min(int(limit), 16))
        self._guard = threading.Lock()
        self._sems: dict[str, threading.Semaphore] = {}

    @property
    def limit(self) -> int:
        return self._limit

    def _sem(self, host_key: str) -> threading.Semaphore:
        with self._guard:
            sem = self._sems.get(host_key)
            if sem is None:
                sem = threading.Semaphore(self._limit)
                self._sems[host_key] = sem
            return sem

    @contextmanager
    def slot(self, host_key: str, *, timeout_sec: float | None = None) -> Iterator[None]:
        """Block until a slot is free (queue). timeout_sec=None waits forever."""
        key = (host_key or "").strip() or "_"
        sem = self._sem(key)
        if timeout_sec is None:
            sem.acquire()
        else:
            ok = sem.acquire(timeout=max(0.1, float(timeout_sec)))
            if not ok:
                raise TimeoutError("ssh_host_busy")
        try:
            yield
        finally:
            sem.release()


# Process-wide gate shared by commands + metrics.
HOST_SSH_GATE = HostSSHGate()
