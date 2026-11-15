"""Native open-file chooser for panel forms (macOS NSOpenPanel / osascript).

Mirrors ``native_save``: HTTP workers call here; AppKit must run on the
menubar main thread via ``register_open_path_chooser``.
"""

from __future__ import annotations

import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from grip.native_save import ChooseStatus, display_path

# (prompt, start_dir | None) → (path, status)
OpenChooserFn = Callable[[str, str | None], tuple[str | None, ChooseStatus]]

__all__ = [
    "choose_open_path",
    "clear_open_path_chooser",
    "default_ssh_key_dir",
    "pick_file_with_panel",
    "register_open_path_chooser",
    "resolve_start_dir",
]

_lock = threading.Lock()
_chooser: OpenChooserFn | None = None


def register_open_path_chooser(fn: OpenChooserFn | None) -> None:
    global _chooser
    with _lock:
        _chooser = fn


def clear_open_path_chooser() -> None:
    register_open_path_chooser(None)


def default_ssh_key_dir() -> str:
    """User home ``.ssh`` if it exists, otherwise home."""
    ssh = Path.home() / ".ssh"
    return str(ssh if ssh.is_dir() else Path.home())


def resolve_start_dir(start_dir: str | None) -> str | None:
    if not start_dir or not str(start_dir).strip():
        return None
    path = Path(str(start_dir).strip()).expanduser()
    try:
        path = path.resolve()
    except OSError:
        return None
    if path.is_dir():
        return str(path)
    if path.parent.is_dir():
        return str(path.parent)
    return None


def _apple_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _choose_via_osascript(
    prompt: str, start_dir: str | None = None
) -> tuple[str | None, ChooseStatus]:
    lines = ["try"]
    resolved = resolve_start_dir(start_dir)
    if resolved:
        lines.append(
            f'  set _p to choose file with prompt "{_apple_escape(prompt)}" '
            f'default location (POSIX file "{_apple_escape(resolved)}")'
        )
    else:
        lines.append(f'  set _p to choose file with prompt "{_apple_escape(prompt)}"')
    lines.extend(
        [
            "  return POSIX path of _p",
            "on error _msg number _num",
            "  if _num is -128 then",
            '    return "__GRIP_CANCELLED__"',
            "  else",
            '    return "__GRIP_UNAVAILABLE__"',
            "  end if",
            "end try",
        ]
    )
    script = "\n".join(lines)
    try:
        proc = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, "unavailable"
    out = (proc.stdout or "").strip()
    if out == "__GRIP_CANCELLED__":
        return None, "cancelled"
    if out == "__GRIP_UNAVAILABLE__" or not out:
        return None, "unavailable"
    return out, "ok"


def choose_open_path(
    *, prompt: str = "选择文件", start_dir: str | None = None
) -> tuple[str | None, ChooseStatus]:
    with _lock:
        chooser = _chooser
    if chooser is not None:
        return chooser(prompt, start_dir)
    if sys.platform == "darwin":
        return _choose_via_osascript(prompt, start_dir)
    if sys.platform == "win32":
        try:
            from grip.win_dialogs import choose_open_path_win
        except Exception:
            return None, "unavailable"
        return choose_open_path_win(prompt, start_dir)
    return None, "unavailable"


def pick_file_with_panel(
    *, prompt: str = "选择文件", start_dir: str | None = None
) -> dict[str, Any]:
    path, status = choose_open_path(prompt=prompt, start_dir=start_dir)
    if status == "unavailable":
        return {"error": "file_picker_unavailable"}
    if status == "cancelled" or not path:
        return {"cancelled": True}
    return {
        "ok": True,
        "path": path,
        "display_path": display_path(path),
    }
