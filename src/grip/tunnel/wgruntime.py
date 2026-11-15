from __future__ import annotations

"""Bring up WireGuard: prefer Grip-embedded wireguard-go, else system wg-quick."""

import shutil
import subprocess
from pathlib import Path
from typing import Any

WG_IFACE = "grip0"
WG_LISTEN_PORT = 51820
OPERATOR_TUNNEL_IP = "10.77.0.1"
TUNNEL_NET_PREFIX = "10.77.0"


def wg_tools_available() -> bool:
    """True if either embedded binary or system wg-quick can be used."""
    try:
        from grip.tunnel.wgembed import bundled_binary_path

        if bundled_binary_path() is not None:
            return True
    except Exception:
        pass
    return bool(shutil.which("wg") and shutil.which("wg-quick"))


def allocate_tunnel_ip(used: set[str] | None = None) -> str:
    """Next free address in 10.77.0.2–10.77.0.254 (operator keeps .1)."""
    taken = set(used or ())
    taken.add(OPERATOR_TUNNEL_IP)
    for i in range(2, 255):
        ip = f"{TUNNEL_NET_PREFIX}.{i}"
        if ip not in taken:
            return ip
    raise RuntimeError("tunnel_ip_pool_exhausted")


def _run(cmd: list[str], timeout: float = 20.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _apply_system_wg_quick(conf_path: Path, *, iface: str) -> dict[str, Any]:
    wg_quick = shutil.which("wg-quick")
    if not wg_quick or not shutil.which("wg"):
        return {"ok": False, "error": "wg_tools_missing", "iface": iface}

    named = conf_path.parent / f"{iface}.conf"
    body = conf_path.read_text(encoding="utf-8")
    if named.resolve() != conf_path.resolve():
        named.write_text(body, encoding="utf-8")
        try:
            named.chmod(0o600)
        except OSError:
            pass
    else:
        named = conf_path

    existing = _run(["wg", "show", iface], timeout=5)
    if existing.returncode == 0:
        strip = _run([wg_quick, "strip", str(named)], timeout=10)
        if strip.returncode != 0:
            return {
                "ok": False,
                "error": "wg_quick_strip_failed",
                "detail": (strip.stderr or strip.stdout or "")[:400],
                "iface": iface,
            }
        strip_path = named.with_suffix(".strip")
        try:
            strip_path.write_text(strip.stdout or "", encoding="utf-8")
            sync = _run(["wg", "syncconf", iface, str(strip_path)], timeout=10)
        finally:
            try:
                if strip_path.is_file():
                    strip_path.unlink()
            except OSError:
                pass
        if sync.returncode != 0:
            return {
                "ok": False,
                "error": "wg_syncconf_failed",
                "detail": (sync.stderr or sync.stdout or "")[:400],
                "iface": iface,
            }
        return {"ok": True, "iface": iface, "action": "syncconf", "engine": "system-wg-quick"}

    up = _run([wg_quick, "up", str(named)], timeout=30)
    if up.returncode != 0:
        _run([wg_quick, "down", str(named)], timeout=15)
        up = _run([wg_quick, "up", str(named)], timeout=30)
    if up.returncode != 0:
        return {
            "ok": False,
            "error": "wg_quick_up_failed",
            "detail": (up.stderr or up.stdout or "")[:400],
            "iface": iface,
        }
    return {"ok": True, "iface": iface, "action": "up", "engine": "system-wg-quick"}


def apply_wg_conf(
    conf_path: Path,
    *,
    iface: str = WG_IFACE,
) -> dict[str, Any]:
    """Install/replace WireGuard config. Prefer embedded userspace engine."""
    conf_path = Path(conf_path)

    # 1) Grip-embedded wireguard-go + UAPI (no system tools).
    try:
        from grip.tunnel.wgembed import apply_embedded_wg, bundled_binary_path

        if bundled_binary_path() is not None:
            embedded = apply_embedded_wg(conf_path)
            if embedded.get("ok"):
                return embedded
            # Honest failure — do not claim success; optional system fallback.
            embed_err = embedded
        else:
            embed_err = {"ok": False, "error": "embedded_binary_missing", "fallback": "relay"}
    except Exception as exc:
        embed_err = {
            "ok": False,
            "error": "embedded_exception",
            "detail": str(exc),
            "fallback": "relay",
        }

    # 2) Optional system wg-quick fallback (if user installed tools).
    system = _apply_system_wg_quick(conf_path, iface=iface)
    if system.get("ok"):
        return system

    return {
        "ok": False,
        "error": "wg_apply_failed",
        "embedded": embed_err,
        "system": system,
        "iface": iface,
        "fallback": "relay",
    }
