from __future__ import annotations

"""Local (unsigned) Grip launcher refresh + install + dist zip (macOS / Windows)."""

import os
import stat
import sys
import zipfile
from pathlib import Path

from grip import __version__
from grip import autostart

LAUNCHER_NAME = "Grip"
BUNDLE_ID = "local.grip.menubar"

# Launcher must resolve through symlinks (e.g. ~/Applications/Grip.app → repo).
_LAUNCHER_TEMPLATE = """\
#!/bin/bash
set -euo pipefail
MACOS_DIR="$(cd "$(dirname "$0")" && pwd -P)"
# .../apps/Grip.app/Contents/MacOS → repo root
ROOT="$(cd "$MACOS_DIR/../../../.." && pwd -P)"
LOG="${TMPDIR:-/tmp}/grip-menubar.log"
{
  echo "[$(date '+%F %T')] launching Grip from $ROOT"
  echo "python=$ROOT/.venv/bin/python"
} >>"$LOG"
export PATH="$ROOT/.venv/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export GRIP_MENUBAR_DIRECT=1
cd "$ROOT"
exec "$ROOT/.venv/bin/python" -c "from grip.native_app import run_native_menubar; run_native_menubar()"
"""

# apps/Grip.cmd → repo root is parent of apps/
_WIN_LAUNCHER_TEMPLATE = """\
@echo off
setlocal
set "APPS_DIR=%~dp0"
for %%I in ("%APPS_DIR%..") do set "ROOT=%%~fI"
set "LOG=%TEMP%\\grip-menubar.log"
echo [%DATE% %TIME%] launching Grip from %ROOT%>>"%LOG%"
echo python=%ROOT%\\.venv\\Scripts\\python.exe>>"%LOG%"
set "PATH=%ROOT%\\.venv\\Scripts;%PATH%"
set "GRIP_MENUBAR_DIRECT=1"
cd /d "%ROOT%"
"%ROOT%\\.venv\\Scripts\\python.exe" -c "from grip.native_app import run_native_menubar; run_native_menubar()"
"""

_INFO_PLIST_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key>
  <string>en</string>
  <key>CFBundleExecutable</key>
  <string>Grip</string>
  <key>CFBundleIdentifier</key>
  <string>{bundle_id}</string>
  <key>CFBundleInfoDictionaryVersion</key>
  <string>6.0</string>
  <key>CFBundleName</key>
  <string>Grip</string>
  <key>CFBundleDisplayName</key>
  <string>Grip</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>{version}</string>
  <key>CFBundleVersion</key>
  <string>1</string>
  <key>LSMinimumSystemVersion</key>
  <string>13.0</string>
  <key>LSUIElement</key>
  <true/>
  <key>NSHighResolutionCapable</key>
  <true/>
  <key>NSAppTransportSecurity</key>
  <dict>
    <key>NSAllowsLocalNetworking</key>
    <true/>
    <key>NSAllowsArbitraryLoads</key>
    <true/>
  </dict>
  <key>NSPrincipalClass</key>
  <string>NSApplication</string>
</dict>
</plist>
"""


def repo_root() -> Path:
    env = os.environ.get("GRIP_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    # src/grip/install_menubar.py → parents[2] = package / repo root
    return Path(__file__).resolve().parents[2]


def default_app_path() -> Path:
    env = os.environ.get("GRIP_APP")
    if env:
        return Path(env).expanduser().resolve()
    if sys.platform == "win32":
        return (repo_root() / "apps" / "Grip.cmd").resolve()
    return (repo_root() / "apps" / "Grip.app").resolve()


def default_win_launcher_path() -> Path:
    env = os.environ.get("GRIP_APP")
    if env:
        return Path(env).expanduser().resolve()
    return (repo_root() / "apps" / "Grip.cmd").resolve()


def win_launcher_script() -> str:
    return _WIN_LAUNCHER_TEMPLATE


def user_applications_dir() -> Path:
    return Path.home() / "Applications"


def user_start_menu_dir() -> Path:
    appdata = os.environ.get("APPDATA") or str(
        Path.home() / "AppData" / "Roaming"
    )
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def system_applications_dir() -> Path:
    return Path("/Applications")


def launcher_script() -> str:
    return _LAUNCHER_TEMPLATE


def info_plist(*, version: str | None = None) -> str:
    return _INFO_PLIST_TEMPLATE.format(
        bundle_id=BUNDLE_ID,
        version=version or __version__,
    )


def refresh_app_bundle(
    app_path: Path | None = None,
    *,
    version: str | None = None,
) -> Path:
    """Ensure apps/Grip.app Contents + executable launcher are present and current."""
    app = (app_path or (repo_root() / "apps" / "Grip.app")).resolve()
    macos = app / "Contents" / "MacOS"
    macos.mkdir(parents=True, exist_ok=True)
    plist = app / "Contents" / "Info.plist"
    plist.write_text(info_plist(version=version), encoding="utf-8")
    exe = macos / LAUNCHER_NAME
    exe.write_text(launcher_script(), encoding="utf-8")
    mode = exe.stat().st_mode
    exe.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return app


def refresh_win_launcher(launcher_path: Path | None = None) -> Path:
    """Ensure apps/Grip.cmd launcher is present (repo .venv → native_win)."""
    path = (launcher_path or default_win_launcher_path()).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(win_launcher_script(), encoding="utf-8")
    return path


def install_start_menu_launcher(
    launcher_path: Path | None = None,
    *,
    dest_dir: Path | None = None,
) -> dict:
    """Copy Grip.cmd into the user Start Menu Programs folder."""
    src = (launcher_path or default_win_launcher_path()).resolve()
    if not src.is_file():
        raise FileNotFoundError(f"Grip.cmd not found: {src}")
    dest_root = (dest_dir or user_start_menu_dir()).expanduser()
    dest = dest_root / "Grip.cmd"
    note = None
    try:
        dest_root.mkdir(parents=True, exist_ok=True)
        dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        ok = True
        installed = str(dest)
    except OSError as exc:
        ok = False
        installed = None
        note = f"could not install into {dest_root}: {exc}"
    return {
        "ok": ok,
        "app": str(src),
        "link": str(dest),
        "installed": installed,
        "dest_dir": str(dest_root),
        "note": note,
    }


def _symlink_force(target: Path, link: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.is_symlink() or link.exists():
        if link.is_symlink() or link.is_file():
            link.unlink()
        elif link.is_dir() and not link.is_symlink():
            # Refuse to wipe a real directory tree
            raise IsADirectoryError(
                f"refusing to replace existing directory: {link} "
                f"(remove it manually, then retry)"
            )
    link.symlink_to(target, target_is_directory=True)


def install_applications_symlink(
    app_path: Path | None = None,
    *,
    dest_dir: Path | None = None,
) -> dict:
    """
    Symlink Grip.app into a user Applications folder (default: ~/Applications).

    Does not write to /Applications (needs admin). Returns a hint if that was
    requested indirectly via an unwritable dest.
    """
    app = (app_path or default_app_path()).resolve()
    if not app.is_dir():
        raise FileNotFoundError(f"Grip.app not found: {app}")

    dest_root = (dest_dir or user_applications_dir()).expanduser()
    link = dest_root / "Grip.app"
    note = None
    try:
        dest_root.mkdir(parents=True, exist_ok=True)
        _symlink_force(app, link)
        installed = str(link.resolve()) if link.exists() else str(link)
        ok = True
    except OSError as exc:
        ok = False
        installed = None
        sys_apps = system_applications_dir()
        note = (
            f"could not install into {dest_root}: {exc}. "
            f"Prefer user-writable ~/Applications; "
            f"system {sys_apps} needs admin "
            f"(e.g. sudo ln -s {app} {sys_apps / 'Grip.app'}). "
            f"Repo app still works: open {app}"
        )

    return {
        "ok": ok,
        "app": str(app),
        "link": str(link),
        "installed": installed,
        "dest_dir": str(dest_root),
        "note": note,
    }


def install_menubar(
    *,
    applications: bool = False,
    autostart_enable: bool = False,
    app_path: Path | None = None,
    applications_dir: Path | None = None,
) -> dict:
    """
    Refresh repo launcher; optionally install into Applications / Start Menu
    and/or enable login autostart.
    """
    if sys.platform == "win32":
        app = refresh_win_launcher(app_path)
        out: dict = {
            "app": str(app),
            "refreshed": True,
            "applications": None,
            "autostart": None,
            "how_to_run": str(app),
            "platform": "win32",
        }
        launch_app = app
        if applications:
            apps_out = install_start_menu_launcher(
                app, dest_dir=applications_dir
            )
            out["applications"] = apps_out
            if apps_out.get("ok") and apps_out.get("link"):
                launch_app = Path(apps_out["link"])
                out["how_to_run"] = str(launch_app)
            elif apps_out.get("note"):
                out["note"] = apps_out["note"]
        if autostart_enable:
            out["autostart"] = autostart.enable(app_path=launch_app)
        return out

    app = refresh_app_bundle(app_path)
    out = {
        "app": str(app),
        "refreshed": True,
        "applications": None,
        "autostart": None,
        "how_to_run": f"open {app}",
        "platform": "darwin",
    }

    launch_app = app
    if applications:
        apps_out = install_applications_symlink(app, dest_dir=applications_dir)
        out["applications"] = apps_out
        if apps_out.get("ok") and apps_out.get("link"):
            # Prefer the symlink path for LaunchServices / open(1); do not
            # resolve through to the repo so status reports ~/Applications.
            launch_app = Path(apps_out["link"])
            out["how_to_run"] = f"open {launch_app}"
        elif apps_out.get("note"):
            out["note"] = apps_out["note"]

    if autostart_enable:
        out["autostart"] = autostart.enable(app_path=launch_app)

    return out


_PACKAGE_README = """\
Grip menu-bar — unsigned / developer distribution
=================================================

This archive is NOT signed and NOT Apple-notarized. macOS Gatekeeper will warn
when you open Grip.app (right-click → Open, or allow under System Settings →
Privacy & Security). Do not expect a notarized DMG or Developer ID signature.

There is no portable standalone binary here: a full self-contained app would
need a bundled Python runtime. Grip.app is a thin launcher that resolves to a
grip repo checkout and runs:

  <repo>/.venv/bin/python  →  grip.native_app.run_native_menubar

Layout in this zip
------------------
  README.txt           this file
  apps/Grip.app/       refreshed launcher (same layout as `grip install-menubar`)

Requirements
------------
1. A grip repository clone on the machine (this package is meant to sit in /
   refresh against that clone — same model as install_menubar symlinks).
2. A venv at <repo>/.venv with the menubar extras installed, e.g.:
     python3 -m venv .venv
     .venv/bin/pip install -e ".[menubar]"

Install / run
-------------
  cd /path/to/grip
  # Optional: unpack this zip over the clone root (writes apps/Grip.app + README.txt)
  #   unzip -o /path/to/grip-menubar-unsigned-*.zip -d .
  .venv/bin/grip install-menubar --applications
  open ~/Applications/Grip.app
  # or: open apps/Grip.app

Standalone DMG: ./scripts/build_macos_installer.sh (unsigned by default).
Signed / notarized DMG remains Deferred without Apple Developer ID
(see docs/ACCEPTANCE.md; optional GRIP_CODESIGN_IDENTITY / GRIP_NOTARY_*).
"""

_PACKAGE_README_WIN = """\
Grip tray — Windows developer distribution
==========================================

Thin launcher (not a frozen exe). Grip.cmd resolves to a grip repo checkout and
runs:

  <repo>\\.venv\\Scripts\\python.exe  →  grip.native_app.run_native_menubar
  (Windows implementation: grip.native_win — pystray + pywebview)

Layout in this zip
------------------
  README.txt      this file
  apps/Grip.cmd   launcher (same as `grip install-menubar`)

Requirements
------------
1. Clone this grip repo on the machine.
2. Create a venv and install menubar extras:
     py -3.11 -m venv .venv
     .venv\\Scripts\\pip install -e ".[menubar]"

Install / run
-------------
  cd \\path\\to\\grip
  .venv\\Scripts\\grip install-menubar --applications --autostart
  .venv\\Scripts\\grip start
  :: or double-click apps\\Grip.cmd

Tray icon: left-click opens the panel; right-click for Probe / Metrics / Sync / Quit.
"""


def package_readme() -> str:
    if sys.platform == "win32":
        return _PACKAGE_README_WIN
    return _PACKAGE_README


def default_dist_dir() -> Path:
    return (repo_root() / "dist").resolve()


def package_menubar(
    *,
    dist_dir: Path | None = None,
    app_path: Path | None = None,
    version: str | None = None,
) -> dict:
    """
    Refresh launcher and write an unsigned zip under dist/.

    macOS: apps/Grip.app + Gatekeeper note.
    Windows: apps/Grip.cmd + tray README.
    """
    ver = version or __version__
    out_dir = (dist_dir or default_dist_dir()).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if sys.platform == "win32":
        launcher = refresh_win_launcher(app_path)
        zip_path = out_dir / f"grip-menubar-windows-{ver}.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("README.txt", package_readme())
            zf.write(launcher, "apps/Grip.cmd")
        return {
            "ok": True,
            "zip": str(zip_path),
            "app": str(launcher),
            "version": ver,
            "unsigned": True,
            "platform": "win32",
            "note": (
                "Windows tray package. Requires a grip clone + .venv "
                "with pip install -e \".[menubar]\"."
            ),
        }

    app = refresh_app_bundle(app_path, version=ver)
    zip_path = out_dir / f"grip-menubar-unsigned-{ver}.zip"

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("README.txt", package_readme())
        for path in sorted(app.rglob("*")):
            if not path.is_file():
                continue
            arcname = f"apps/Grip.app/{path.relative_to(app).as_posix()}"
            zf.write(path, arcname)

    return {
        "ok": True,
        "zip": str(zip_path),
        "app": str(app),
        "version": ver,
        "unsigned": True,
        "notarized": False,
        "platform": "darwin",
        "note": (
            "Unsigned developer package; Gatekeeper will warn. "
            "Requires a grip clone + .venv (launcher symlinks/resolves to repo)."
        ),
    }
