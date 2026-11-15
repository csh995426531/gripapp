from __future__ import annotations

import socket
import time
import urllib.error
import urllib.request
from typing import Any, Callable


def normalize_probe(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    if not raw:
        return None
    typ = (raw.get("type") or "").lower().strip()
    if typ not in {"file", "tcp", "http"}:
        raise ValueError("await_probe.type must be file|tcp|http")
    target = raw.get("target")
    if not target:
        raise ValueError("await_probe.target required")
    timeout = int(raw.get("timeout_sec") or 120)
    return {"type": typ, "target": str(target), "timeout_sec": timeout}


def wait_probe(
    probe: dict[str, Any],
    *,
    ssh_check_file: Callable[[str], bool] | None = None,
) -> tuple[bool, str]:
    """Block until probe succeeds or timeout.

    - tcp/http: checked from the Grip machine (published port / URL).
    - file: remote existence via ssh_check_file(path).
    """
    typ = probe["type"]
    target = probe["target"]
    deadline = time.time() + int(probe["timeout_sec"])
    last = ""
    while time.time() < deadline:
        try:
            if typ == "tcp":
                host, _, port_s = target.rpartition(":")
                if not host or not port_s:
                    return False, "tcp target must be host:port"
                try:
                    with socket.create_connection((host, int(port_s)), timeout=2):
                        return True, f"tcp open {target}"
                except OSError as e:
                    last = str(e)
            elif typ == "http":
                try:
                    with urllib.request.urlopen(target, timeout=3) as resp:
                        code = getattr(resp, "status", 200)
                        if 200 <= code < 500:
                            return True, f"http {code} {target}"
                        last = f"status {code}"
                except urllib.error.HTTPError as e:
                    if e.code < 500:
                        return True, f"http {e.code} {target}"
                    last = str(e)
                except Exception as e:  # noqa: BLE001
                    last = str(e)
            elif typ == "file":
                if ssh_check_file is None:
                    return False, "file probe requires remote checker"
                if ssh_check_file(target):
                    return True, f"file exists {target}"
                last = "file missing"
        except OSError as e:
            last = str(e)
        time.sleep(1.0)
    return False, f"probe timeout ({typ} {target}): {last}"
