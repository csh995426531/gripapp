"""When the menubar app is running, MCP should reuse its SSH sessions.

Cursor / Claude spawn a separate ``Grip mcp`` process. AutoDL-style password
gateways often reject a second simultaneous login with ``Authentication failed``
while the panel (which already holds a warm paramiko session) still works.
Proxying ``run_command`` to the local panel HTTP API keeps one process on the wire.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from grip.paths import grip_home


def panel_meta_path() -> Path:
    return grip_home() / "panel.json"


def write_panel_meta(*, url: str, pid: int) -> None:
    path = panel_meta_path()
    path.write_text(
        json.dumps({"url": url.rstrip("/") + "/", "pid": pid}, ensure_ascii=False),
        encoding="utf-8",
    )
    try:
        path.chmod(0o600)
    except OSError:
        pass


def clear_panel_meta() -> None:
    try:
        panel_meta_path().unlink(missing_ok=True)
    except OSError:
        pass


def read_panel_url() -> str | None:
    path = panel_meta_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    url = str(data.get("url") or "").strip()
    return url or None


def try_panel_run_command(
    name: str,
    command: str,
    *,
    timeout_sec: int = 60,
    confirm_write: bool = False,
    background: bool = True,
    actor: str | None = None,
) -> dict[str, Any] | None:
    """POST /api/run on the live panel. None → caller should run locally."""
    base = read_panel_url()
    if not base:
        return None
    body = {
        "name": name,
        "command": command,
        "timeout_sec": timeout_sec,
        "confirm_write": confirm_write,
        "background": background,
        "actor": actor or "mcp",
    }
    req = urllib.request.Request(
        base + "api/run",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=min(30, max(5, timeout_sec))) as resp:
            raw = resp.read().decode("utf-8")
            data = json.loads(raw) if raw else {}
            if isinstance(data, dict):
                data.setdefault("via", "panel_proxy")
                return data
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None
    return None
