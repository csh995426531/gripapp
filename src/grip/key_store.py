from __future__ import annotations

"""OS keyring helpers for Grip master secrets (macOS Keychain / libsecret / Win Cred).

When keyring is disabled or unavailable, callers fall back to files under GRIP_HOME.
"""

import logging
import os
from typing import Literal

log = logging.getLogger("grip.key_store")

SERVICE = "grip"


def keyring_enabled() -> bool:
    return (os.environ.get("GRIP_KEYRING") or "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _call_keyring(fn, *, timeout_sec: float = 4.0, direct: bool = False):  # noqa: ANN001
    """Run keyring I/O.

    ``direct=True`` runs on the *current* thread (required for Keychain UI —
    must be the AppKit main thread). Otherwise use a short timeout worker so
    background probes cannot hang the whole process forever.
    """
    if direct:
        try:
            return fn(), None
        except Exception as exc:  # noqa: BLE001
            return None, exc

    import threading

    box: dict = {}

    def worker() -> None:
        try:
            box["value"] = fn()
        except Exception as exc:  # noqa: BLE001
            box["exc"] = exc

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join(timeout=max(0.5, float(timeout_sec)))
    if t.is_alive():
        log.warning("keyring call timed out after %.1fs", timeout_sec)
        return None, TimeoutError("keyring_timeout")
    if "exc" in box:
        return None, box["exc"]
    return box.get("value"), None


def get_secret(account: str, *, direct: bool = False) -> bytes | None:
    """Return secret bytes from OS keyring, or None if missing/unavailable."""
    if not keyring_enabled():
        return None
    try:
        import keyring
    except ImportError:
        log.debug("keyring package not installed")
        return None
    raw, err = _call_keyring(
        lambda: keyring.get_password(SERVICE, account), direct=direct
    )
    if err is not None:
        if not isinstance(err, TimeoutError):
            log.warning("keyring get failed (%s): %s", account, err)
        return None
    if raw is None or raw == "":
        return None
    return raw.encode("utf-8")


def set_secret(account: str, value: bytes, *, direct: bool = False) -> bool:
    """Store secret in OS keyring. Returns False if keyring unavailable."""
    if not keyring_enabled():
        return False
    try:
        import keyring
    except ImportError:
        return False
    _, err = _call_keyring(
        lambda: keyring.set_password(SERVICE, account, value.decode("utf-8")),
        direct=direct,
        timeout_sec=60.0 if direct else 4.0,
    )
    if err is not None:
        if not isinstance(err, TimeoutError):
            log.warning("keyring set failed (%s): %s", account, err)
        return False
    return True


def delete_secret(account: str) -> None:
    if not keyring_enabled():
        return
    try:
        import keyring
    except ImportError:
        return
    try:
        keyring.delete_password(SERVICE, account)
    except Exception:  # noqa: BLE001
        pass


Backend = Literal["keyring", "file"]
