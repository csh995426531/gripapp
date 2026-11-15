"""Bridge: run a callable on the native UI main thread (AppKit / Win forms).

HTTP worker threads must not touch Keychain / file panels directly — macOS will
hang or show no dialog. ``native_app`` registers a runner at startup.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any, TypeVar

T = TypeVar("T")

_lock = threading.Lock()
_runner: Callable[[Callable[[], Any]], Any] | None = None


def register_main_runner(fn: Callable[[Callable[[], Any]], Any] | None) -> None:
    global _runner
    with _lock:
        _runner = fn


def clear_main_runner() -> None:
    register_main_runner(None)


def run_on_main(fn: Callable[[], T], *, fallback_local: bool = True) -> T:
    """Run ``fn`` on the UI main thread when a bridge is registered."""
    with _lock:
        runner = _runner
    if runner is None:
        if not fallback_local:
            raise RuntimeError("native main-thread runner not registered")
        return fn()
    return runner(fn)


def has_main_runner() -> bool:
    with _lock:
        return _runner is not None
