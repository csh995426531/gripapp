from __future__ import annotations

"""Login / Startup autostart — macOS LaunchAgent or Windows Startup folder."""

import os
import plistlib
import subprocess
import sys
from pathlib import Path

LABEL = "local.grip.menubar"


def _agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def plist_path() -> Path:
    return _agents_dir() / f"{LABEL}.plist"


def default_app_path() -> Path:
    env = os.environ.get("GRIP_APP")
    if env:
        return Path(env).expanduser().resolve()
    root = Path(__file__).resolve().parents[2]
    if sys.platform == "win32":
        return (root / "apps" / "Grip.cmd").resolve()
    return (root / "apps" / "Grip.app").resolve()


def _startup_dir() -> Path:
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def startup_cmd_path() -> Path:
    return _startup_dir() / "Grip.cmd"


def is_enabled() -> bool:
    if sys.platform == "darwin":
        return plist_path().is_file()
    if sys.platform == "win32":
        return startup_cmd_path().is_file()
    return False


def _app_from_plist(path: Path) -> Path | None:
    if not path.is_file():
        return None
    try:
        data = plistlib.loads(path.read_bytes())
    except Exception:
        return None
    args = data.get("ProgramArguments") or []
    # Expected: ["/usr/bin/open", "-a", "<app>"]
    if len(args) >= 3 and str(args[1]) == "-a":
        return Path(str(args[2])).expanduser()
    return None


def status() -> dict:
    if sys.platform == "win32":
        path = startup_cmd_path()
        app = default_app_path()
        return {
            "enabled": is_enabled(),
            "startup_cmd": str(path),
            "app": str(app),
            "app_exists": app.is_file(),
            "label": LABEL,
            "platform": "win32",
        }
    path = plist_path()
    app = _app_from_plist(path) or default_app_path()
    return {
        "enabled": is_enabled(),
        "plist": str(path),
        "app": str(app),
        "app_exists": app.is_dir(),
        "label": LABEL,
        "platform": "darwin",
    }


def _write_startup_cmd(launcher: Path) -> Path:
    dest = startup_cmd_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Absolute path; do not resolve through final symlink if any.
    target = Path(os.path.abspath(Path(launcher).expanduser()))
    body = (
        "@echo off\r\n"
        f'call "{target}"\r\n'
    )
    dest.write_text(body, encoding="utf-8")
    return dest


def enable(*, app_path: Path | None = None) -> dict:
    if sys.platform == "win32":
        raw = app_path or default_app_path()
        app = Path(os.path.abspath(Path(raw).expanduser()))
        if not app.is_file():
            raise FileNotFoundError(f"Grip launcher not found: {app}")
        _write_startup_cmd(app)
        return status()

    raw = app_path or default_app_path()
    # Absolute path without resolving the final symlink so
    # ~/Applications/Grip.app → repo stays the LaunchAgent target.
    app = Path(os.path.abspath(Path(raw).expanduser()))
    if not app.is_dir():
        raise FileNotFoundError(f"Grip.app not found: {app}")
    agents = _agents_dir()
    agents.mkdir(parents=True, exist_ok=True)
    payload = {
        "Label": LABEL,
        "ProgramArguments": ["/usr/bin/open", "-a", str(app)],
        "RunAtLoad": True,
        # Do not KeepAlive — menubar app manages its own lifetime
    }
    path = plist_path()
    path.write_bytes(plistlib.dumps(payload))
    # Load into current session when possible (best-effort)
    try:
        subprocess.run(
            ["launchctl", "unload", str(path)],
            capture_output=True,
            check=False,
        )
        subprocess.run(
            ["launchctl", "load", str(path)],
            capture_output=True,
            check=False,
        )
    except OSError:
        pass
    return status()


def disable() -> dict:
    if sys.platform == "win32":
        path = startup_cmd_path()
        if path.is_file():
            try:
                path.unlink()
            except OSError:
                pass
        return status()

    path = plist_path()
    if path.is_file():
        try:
            subprocess.run(
                ["launchctl", "unload", str(path)],
                capture_output=True,
                check=False,
            )
        except OSError:
            pass
        try:
            path.unlink()
        except OSError:
            pass
    return status()
