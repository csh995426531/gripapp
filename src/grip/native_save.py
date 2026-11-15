"""Native save-path chooser for panel export (macOS NSSavePanel / osascript).

HTTP handlers run on worker threads; AppKit panels must run on the menubar
main thread. ``native_app`` registers a bridge via ``register_save_path_chooser``.
When no bridge is registered, Darwin falls back to ``osascript``; otherwise
callers get ``unavailable`` and should use a browser download.
"""

from __future__ import annotations

import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

ChooseStatus = Literal["ok", "cancelled", "unavailable"]
ChooserFn = Callable[[str, str], tuple[str | None, ChooseStatus]]

__all__ = [
    "ChooseStatus",
    "choose_save_path",
    "clear_save_path_chooser",
    "display_path",
    "register_save_path_chooser",
    "save_export_with_panel",
    "write_text_file",
]

_chooser_lock = threading.Lock()
_chooser: ChooserFn | None = None


def register_save_path_chooser(fn: ChooserFn | None) -> None:
    """Register / clear the main-thread save-panel bridge (from native_app)."""
    global _chooser
    with _chooser_lock:
        _chooser = fn


def clear_save_path_chooser() -> None:
    register_save_path_chooser(None)


def display_path(path: str | Path) -> str:
    """Human-friendly path with ``~`` when under home."""
    p = Path(path).expanduser()
    try:
        p = p.resolve()
    except OSError:
        p = Path(path).expanduser()
    try:
        home = Path.home().resolve()
    except OSError:
        home = Path.home()
    try:
        rel = p.relative_to(home)
        return f"~/{rel.as_posix()}"
    except ValueError:
        return str(p)


def write_text_file(path: str | Path, body: str) -> Path:
    dest = Path(path).expanduser()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(body, encoding="utf-8")
    try:
        return dest.resolve()
    except OSError:
        return dest


def _apple_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _choose_via_osascript(
    default_name: str, prompt: str
) -> tuple[str | None, ChooseStatus]:
    script = "\n".join(
        [
            "try",
            f'  set _p to choose file name with prompt "{_apple_escape(prompt)}" '
            f'default name "{_apple_escape(default_name)}"',
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


def choose_save_path(
    default_name: str,
    *,
    prompt: str = "选择保存位置",
) -> tuple[str | None, ChooseStatus]:
    """Return ``(path, status)`` — ``ok`` / ``cancelled`` / ``unavailable``."""
    with _chooser_lock:
        chooser = _chooser
    if chooser is not None:
        return chooser(default_name, prompt)
    if sys.platform == "darwin":
        return _choose_via_osascript(default_name, prompt)
    if sys.platform == "win32":
        try:
            from grip.win_dialogs import choose_save_path_win
        except Exception:
            return None, "unavailable"
        return choose_save_path_win(default_name, prompt)
    return None, "unavailable"


def save_export_with_panel(
    *,
    body: str,
    filename: str,
    media_type: str = "application/x-ndjson; charset=utf-8",
    prompt: str = "选择保存位置",
) -> dict[str, Any]:
    """Show save UI, write body, return a JSON-serializable result for the panel."""
    path, status = choose_save_path(filename, prompt=prompt)
    if status == "unavailable":
        return {
            "native": False,
            "filename": filename,
            "media_type": media_type,
            "body": body,
        }
    if status == "cancelled" or not path:
        return {"cancelled": True}
    try:
        dest = write_text_file(path, body)
    except OSError as exc:
        return {"error": f"写入失败: {exc}", "path": path}
    return {
        "ok": True,
        "path": str(dest),
        "display_path": display_path(dest),
        "filename": dest.name,
    }
