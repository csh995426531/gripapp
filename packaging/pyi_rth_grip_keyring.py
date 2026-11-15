"""PyInstaller runtime hook: force a concrete keyring backend when frozen."""

from __future__ import annotations

import sys


def _configure() -> None:
    if not getattr(sys, "frozen", False):
        return
    try:
        import keyring
        import keyring.backends
    except Exception:
        return
    try:
        if sys.platform == "darwin":
            from keyring.backends.macOS import Keyring as MacKeyring

            keyring.set_keyring(MacKeyring())
        elif sys.platform == "win32":
            from keyring.backends.Windows import WinVaultKeyring

            keyring.set_keyring(WinVaultKeyring())
    except Exception:
        # Fall through to keyring default discovery / file vault fallback.
        pass


_configure()
