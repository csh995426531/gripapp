# -*- mode: python ; coding: utf-8 -*-
"""Standalone Grip tray/menu-bar app (macOS .app / Windows onedir)."""

from __future__ import annotations

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).resolve().parent
ENTRY = ROOT / "packaging" / "entry_menubar.py"

datas: list = []
binaries: list = []

# Codex-patched CPython SSLContext.__new__ loads this sibling of ssl.py.
# Bundle it so frozen TLS does not raise FileNotFoundError.
_CODEX_PEM = ROOT / "packaging" / "codex-rejected-cas.pem"
if _CODEX_PEM.is_file():
    datas.append((str(_CODEX_PEM), "."))

# Custom Grip icons (app .icns via BUNDLE; menubar PNGs as Resources)
_ICON_DIR = ROOT / "packaging" / "icons"
_ICON_ICNS = _ICON_DIR / "Grip.icns"
if (_ICON_DIR / "aaron.s@example.org").is_file():
    datas.append((str(_ICON_DIR / "aaron.s@example.org"), "grip_icons"))
if (_ICON_DIR / "menubar.png").is_file():
    datas.append((str(_ICON_DIR / "menubar.png"), "grip_icons"))

hiddenimports: list = [
    "grip",
    "grip.native_app",
    "grip.web.app",
    "grip.service",
    "grip.mcp_server",
    "paramiko",
    "cryptography",
    "keyring",
    "keyring.backends",
    "keyring.backends.macOS",
    "keyring.backends.Windows",
    "mcp",
]

for pkg in (
    "grip",
    "cryptography",
    "keyring",
    "paramiko",
    "certifi",
):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        hiddenimports += collect_submodules(pkg)

# mcp: avoid collect_all (cli submodule needs optional typer)
try:
    hiddenimports += [
        m
        for m in collect_submodules("mcp")
        if not m.startswith("mcp.cli")
    ]
except Exception:
    pass

if sys.platform == "darwin":
    for pkg in (
        "Cocoa",
        "Foundation",
        "AppKit",
        "WebKit",
        "CoreFoundation",
        "objc",
    ):
        try:
            hiddenimports += collect_submodules(pkg)
        except Exception:
            pass
    hiddenimports += [
        "grip.native_app",
        "AppKit",
        "Foundation",
        "WebKit",
    ]
elif sys.platform == "win32":
    for pkg in ("pystray", "webview", "PIL", "bottle", "proxy_tools", "clr"):
        try:
            d, b, h = collect_all(pkg)
            datas += d
            binaries += b
            hiddenimports += h
        except Exception:
            try:
                hiddenimports += collect_submodules(pkg)
            except Exception:
                pass
    hiddenimports += [
        "grip.native_win",
        "grip.win_dialogs",
        "pystray",
        "webview",
        "PIL",
    ]

a = Analysis(
    [str(ENTRY)],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=sorted(set(hiddenimports)),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(ROOT / "packaging" / "pyi_rth_grip_keyring.py")],
    excludes=["tkinter", "matplotlib", "numpy", "pandas", "pytest"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Grip",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # console=False on macOS — console=True makes PyInstaller set LSBackgroundOnly,
    # which yields a 0×0 / invisible NSStatusItem. Boot errors go to ~/Library/Logs/Grip.
    console=(sys.platform != "darwin"),
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Grip",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Grip.app",
        icon=str(_ICON_ICNS) if _ICON_ICNS.is_file() else None,
        bundle_identifier="local.grip.menubar",
        info_plist={
            "CFBundleName": "Grip",
            "CFBundleDisplayName": "Grip",
            "CFBundleShortVersionString": "0.2.0",
            "CFBundleVersion": "0.2.0",
            "CFBundleIconFile": "Grip",
            "LSMinimumSystemVersion": "13.0",
            "LSUIElement": True,
            # Explicitly false — never ship as LSBackgroundOnly (invisible menu bar).
            "LSBackgroundOnly": False,
            "NSHighResolutionCapable": True,
            "NSPrincipalClass": "NSApplication",
            "NSAppTransportSecurity": {
                "NSAllowsLocalNetworking": True,
                "NSAllowsArbitraryLoads": True,
            },
        },
    )
