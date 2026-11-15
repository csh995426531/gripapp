from __future__ import annotations

"""Embed / launch Grip's userspace wireguard-go (no system wg-quick required)."""

import os
import platform
import signal
import subprocess
import threading
import time
from importlib import resources
from pathlib import Path
from typing import Any

from grip.paths import ensure_home
from grip.tunnel.wguapi import (
    DEFAULT_UAPI_DIR,
    WgConf,
    apply_uapi,
    parse_wg_quick_conf,
    uapi_sock_path,
)

WG_IFACE_LINUX = "grip0"
UAPI_DIR = DEFAULT_UAPI_DIR

# After user cancels / privilege bring-up fails, do not re-prompt for a while.
DEFAULT_PRIV_COOLDOWN_SEC = 300.0
_PRIVILEGE_HINTS = (
    "not permitted",
    "operation not permitted",
    "permission denied",
    "eperm",
    "eacces",
    "user canceled",
    "user cancelled",
    "(-128)",  # macOS osascript user canceled
)
_priv_deny_until = 0.0
_priv_lock = threading.Lock()


def looks_like_privilege_error(*parts: str) -> bool:
    text = " ".join(p for p in parts if p).lower()
    return any(h in text for h in _PRIVILEGE_HINTS)


def _priv_deny_path() -> Path:
    return ensure_home() / "run" / "wg_priv_deny_until"


def reset_privilege_cooldown_for_tests() -> None:
    global _priv_deny_until
    with _priv_lock:
        _priv_deny_until = 0.0
    try:
        p = _priv_deny_path()
        if p.is_file():
            p.unlink()
    except OSError:
        pass


def mark_privilege_denied(
    cooldown_sec: float = DEFAULT_PRIV_COOLDOWN_SEC,
    *,
    now: float | None = None,
) -> None:
    """Silence admin prompts for a while (memory + disk, survives restart)."""
    global _priv_deny_until
    mono = time.monotonic() if now is None else float(now)
    wall_until = time.time() + float(cooldown_sec)
    with _priv_lock:
        _priv_deny_until = mono + float(cooldown_sec)
    try:
        path = _priv_deny_path()
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        path.write_text(f"{wall_until:.3f}\n", encoding="utf-8")
    except OSError:
        pass


def clear_privilege_denied() -> None:
    global _priv_deny_until
    with _priv_lock:
        _priv_deny_until = 0.0
    try:
        p = _priv_deny_path()
        if p.is_file():
            p.unlink()
    except OSError:
        pass


def privilege_in_cooldown(*, now: float | None = None) -> bool:
    global _priv_deny_until
    mono = time.monotonic() if now is None else float(now)
    with _priv_lock:
        if _priv_deny_until > mono:
            return True
    # Reload wall-clock marker so a restarted process still cools down.
    try:
        p = _priv_deny_path()
        if p.is_file():
            wall_until = float(p.read_text(encoding="utf-8").strip())
            if wall_until > time.time():
                remaining = wall_until - time.time()
                with _priv_lock:
                    _priv_deny_until = time.monotonic() + remaining
                return True
            try:
                p.unlink()
            except OSError:
                pass
    except Exception:
        pass
    return False


def platform_tag(system: str | None = None, machine: str | None = None) -> str:
    sys_name = (system or platform.system()).lower()
    mach = (machine or platform.machine()).lower()
    if sys_name == "darwin":
        os_name = "darwin"
    elif sys_name == "linux":
        os_name = "linux"
    else:
        raise RuntimeError(f"unsupported_os:{sys_name}")
    if mach in {"x86_64", "amd64"}:
        arch = "amd64"
    elif mach in {"arm64", "aarch64"}:
        arch = "arm64"
    else:
        raise RuntimeError(f"unsupported_arch:{mach}")
    return f"{os_name}-{arch}"


def binary_name(tag: str | None = None) -> str:
    return f"wireguard-go-{tag or platform_tag()}"


def bundled_binary_path(tag: str | None = None) -> Path | None:
    name = binary_name(tag)
    try:
        root = resources.files("grip.tunnel.bins")
        cand = root.joinpath(name)
        if cand.is_file():
            return Path(str(cand))
    except Exception:
        pass
    local = Path(__file__).resolve().parent / "bins" / name
    if local.is_file():
        return local
    return None


def install_binary_to(dest_dir: Path, tag: str | None = None) -> Path:
    """Copy embedded binary into dest_dir/wireguard-go (executable)."""
    src = bundled_binary_path(tag)
    if src is None:
        raise FileNotFoundError(f"embedded_wireguard_go_missing:{tag or platform_tag()}")
    dest_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    dest = dest_dir / "wireguard-go"
    data = src.read_bytes()
    dest.write_bytes(data)
    dest.chmod(0o755)
    return dest


def operator_bin_dir() -> Path:
    d = ensure_home() / "bin"
    d.mkdir(parents=True, mode=0o700, exist_ok=True)
    return d


def ensure_operator_binary(*, force: bool = False) -> Path:
    """Install/refresh operator wireguard-go from the bundled build."""
    dest = operator_bin_dir() / "wireguard-go"
    src = bundled_binary_path()
    if src is None:
        if dest.is_file() and dest.stat().st_size > 0:
            return dest
        raise FileNotFoundError("embedded_wireguard_go_missing")
    if (
        force
        or not dest.is_file()
        or dest.stat().st_size != src.stat().st_size
    ):
        return install_binary_to(operator_bin_dir())
    return dest


def iface_for_os() -> str:
    if platform.system().lower() == "darwin":
        return "utun"
    return WG_IFACE_LINUX


def pid_file(iface: str) -> Path:
    return ensure_home() / "run" / f"wireguard-{iface}.pid"


def _read_pid(iface: str) -> int | None:
    p = pid_file(iface)
    if not p.is_file():
        return None
    try:
        return int(p.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def _write_pid(iface: str, pid: int) -> None:
    path = pid_file(iface)
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    path.write_text(str(pid) + "\n", encoding="utf-8")


def _clear_pid(iface: str) -> None:
    p = pid_file(iface)
    try:
        if p.is_file():
            p.unlink()
    except OSError:
        pass


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _kill_pid(pid: int | None) -> None:
    if not pid:
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return
    for _ in range(20):
        if not _pid_alive(pid):
            return
        time.sleep(0.05)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def _read_log_tail(log_path: Path, limit: int = 400) -> str:
    try:
        data = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return data[-limit:]


def discover_utun_iface(
    timeout_sec: float = 3.0,
    *,
    before: set[str] | None = None,
) -> str | None:
    """Find a utun UAPI sock, preferring ones not in ``before``."""
    before = before or set()
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if UAPI_DIR.is_dir():
            names = {p.stem for p in UAPI_DIR.glob("utun*.sock")}
            fresh = sorted(names - before)
            if fresh:
                return fresh[0]
            if names and not before:
                return sorted(names)[0]
        time.sleep(0.05)
    return None


def start_wireguard_go(
    *,
    binary: Path | None = None,
    iface: str | None = None,
) -> dict[str, Any]:
    """Start embedded wireguard-go in background; return iface + pid."""
    binary = binary or ensure_operator_binary()
    want = iface or iface_for_os()
    UAPI_DIR.mkdir(parents=True, mode=0o755, exist_ok=True)

    existing_pid = _read_pid(want)
    if want != "utun":
        sock = uapi_sock_path(want, UAPI_DIR)
        if sock.exists() and existing_pid and _pid_alive(existing_pid):
            return {"ok": True, "iface": want, "pid": existing_pid, "action": "reuse"}
    else:
        found = discover_utun_iface(0.2)
        real_pid = existing_pid
        if found:
            real_pid = _read_pid(found) or existing_pid
        if found and real_pid and _pid_alive(real_pid):
            return {"ok": True, "iface": found, "pid": real_pid, "action": "reuse"}

    before_utun = (
        {p.stem for p in UAPI_DIR.glob("utun*.sock")} if want == "utun" else set()
    )

    env = os.environ.copy()
    env["WG_PROCESS_FOREGROUND"] = "1"
    log_path = ensure_home() / "run" / f"wireguard-{want}.log"
    log_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    log_f = open(log_path, "ab", buffering=0)  # noqa: SIM115
    try:
        proc = subprocess.Popen(
            [str(binary), "-f", want],
            stdout=log_f,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
    except Exception as exc:
        log_f.close()
        return {"ok": False, "error": "wireguard_go_start_failed", "detail": str(exc)}

    _write_pid(want, proc.pid)
    real_iface = want
    if want == "utun":
        real = discover_utun_iface(3.0, before=before_utun)
        if not real or not _pid_alive(proc.pid):
            tail = _read_log_tail(log_path)
            _kill_pid(proc.pid)
            _clear_pid(want)
            detail = tail or "process_exited"
            return {
                "ok": False,
                "error": "utun_iface_not_found",
                "detail": detail,
                "need_privilege": looks_like_privilege_error(detail),
                "log": str(log_path),
            }
        real_iface = real
        _write_pid(real_iface, proc.pid)
    else:
        sock = uapi_sock_path(want, UAPI_DIR)
        deadline = time.time() + 3.0
        while time.time() < deadline and not sock.exists():
            if not _pid_alive(proc.pid):
                break
            time.sleep(0.05)
        if not sock.exists() or not _pid_alive(proc.pid):
            tail = _read_log_tail(log_path)
            _kill_pid(proc.pid)
            _clear_pid(want)
            detail = tail or "process_exited"
            return {
                "ok": False,
                "error": "uapi_sock_missing",
                "detail": detail,
                "need_privilege": looks_like_privilege_error(detail),
                "log": str(log_path),
            }

    return {
        "ok": True,
        "iface": real_iface,
        "pid": proc.pid,
        "action": "started",
        "binary": str(binary),
        "log": str(log_path),
    }


def configure_addresses(
    iface: str,
    conf: WgConf,
    *,
    privileged: bool = False,
) -> dict[str, Any]:
    """Assign Address= and host routes for AllowedIPs."""
    details: list[str] = []
    ok = True
    addr = (conf.address or "").split("/")[0].strip()
    system = platform.system().lower()

    def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(cmd, capture_output=True, text=True, check=False)

    if addr:
        if system == "darwin":
            cmds = [
                ["ifconfig", iface, "inet", addr, addr, "alias"],
                ["ifconfig", iface, "inet", addr, addr],
            ]
            r = None
            for cmd in cmds:
                if privileged:
                    from grip.tunnel.wgpriv import darwin_admin_shell
                    import shlex

                    res = darwin_admin_shell(" ".join(shlex.quote(c) for c in cmd))
                    if res.get("ok"):
                        r = subprocess.CompletedProcess(cmd, 0, "", "")
                        break
                    r = subprocess.CompletedProcess(
                        cmd, 1, "", str(res.get("stderr") or res.get("stdout") or "")
                    )
                else:
                    r = run(cmd)
                    if r.returncode == 0:
                        break
            if r is None or r.returncode != 0:
                ok = False
                details.append(
                    ((r.stderr if r else "") or (r.stdout if r else "") or "ifconfig_failed")[
                        :200
                    ]
                )
        else:
            run(["ip", "link", "set", "dev", iface, "up"])
            r = run(["ip", "address", "replace", f"{addr}/32", "dev", iface])
            if r.returncode != 0:
                ok = False
                details.append((r.stderr or r.stdout or "ip_addr_failed")[:200])

    for peer in conf.peers:
        for allowed in peer.allowed_ips:
            host = allowed.split("/")[0].strip()
            if not host:
                continue
            if system == "darwin":
                if not privileged:
                    run(["route", "-n", "delete", "-host", host])
                    r = run(["route", "-n", "add", "-host", host, "-interface", iface])
                else:
                    from grip.tunnel.wgpriv import darwin_admin_shell
                    import shlex

                    cmd = (
                        f"route -n delete -host {shlex.quote(host)} || true; "
                        f"route -n add -host {shlex.quote(host)} -interface {shlex.quote(iface)}"
                    )
                    res = darwin_admin_shell(cmd)
                    r = subprocess.CompletedProcess(
                        ["route"],
                        0 if res.get("ok") else 1,
                        res.get("stdout") or "",
                        res.get("stderr") or "",
                    )
            else:
                r = run(["ip", "route", "replace", allowed, "dev", iface])
            if r.returncode != 0:
                details.append((r.stderr or r.stdout or "route_failed")[:200])
                ok = False

    return {"ok": ok, "detail": "; ".join(details) if details else ""}


def _apply_unprivileged(
    conf_path: Path,
    conf: WgConf,
    *,
    binary: Path,
) -> dict[str, Any]:
    started = start_wireguard_go(binary=binary)
    if not started.get("ok"):
        return started
    iface = str(started["iface"])
    pid = started.get("pid")
    try:
        apply_uapi(iface, conf, uapi_dir=UAPI_DIR)
    except Exception as exc:
        _kill_pid(int(pid) if pid else None)
        _clear_pid(iface)
        _clear_pid("utun")
        return {
            "ok": False,
            "error": "uapi_apply_failed",
            "detail": str(exc),
            "iface": iface,
        }

    net = configure_addresses(iface, conf, privileged=False)
    if not net.get("ok"):
        detail = str(net.get("detail") or "")
        # Only escalate to admin when the failure looks like a permission error.
        need_priv = platform.system().lower() == "darwin" and looks_like_privilege_error(
            detail
        )
        return {
            "ok": False,
            "error": "net_configure_failed",
            "detail": detail,
            "iface": iface,
            "pid": pid,
            "need_privilege": need_priv,
            "partial": started,
            "net": net,
        }

    return {
        "ok": True,
        "iface": iface,
        "pid": pid,
        "action": started.get("action"),
        "engine": "embedded-wireguard-go",
        "net": net,
    }


def _apply_darwin_privileged(conf_path: Path, *, binary: Path) -> dict[str, Any]:
    from grip.tunnel.wgpriv import darwin_privileged_wg_up

    log_path = ensure_home() / "run" / "wireguard-utun.log"
    pid_path = pid_file("utun")
    before = {p.stem for p in UAPI_DIR.glob("utun*.sock")} if UAPI_DIR.is_dir() else set()
    result = darwin_privileged_wg_up(
        binary=binary,
        conf_path=conf_path,
        remote_wg_py=Path(__file__).resolve().parent / "remote_wg.py",
        log_path=log_path,
        pid_path=pid_path,
        uapi_dir=UAPI_DIR,
    )
    if not result.get("ok"):
        return {
            "ok": False,
            "error": "darwin_privilege_failed",
            "detail": (result.get("stderr") or result.get("stdout") or "")[:400],
            "need_privilege": True,
            "engine": "embedded-wireguard-go-admin",
        }
    iface = discover_utun_iface(2.0, before=before) or discover_utun_iface(0.5)
    pid = _read_pid("utun")
    if iface and pid:
        _write_pid(iface, pid)
    if not iface:
        return {
            "ok": False,
            "error": "darwin_privilege_no_iface",
            "detail": result.get("stdout") or "",
            "need_privilege": True,
        }
    return {
        "ok": True,
        "iface": iface,
        "pid": pid,
        "action": "privileged-up",
        "engine": "embedded-wireguard-go-admin",
        "net": {"ok": True, "detail": ""},
    }


def privilege_prompts_allowed() -> bool:
    """Whether macOS admin prompts are allowed (disabled in CI / cooldown / GRIP_WG_PRIV=0).

    ``GRIP_WG_PRIV=1`` forces prompts even during cooldown (debug / explicit retry).
    """
    flag = (os.environ.get("GRIP_WG_PRIV") or "").strip().lower()
    if flag in {"0", "false", "no", "off"}:
        return False
    if flag in {"1", "true", "yes", "on"}:
        return True
    if privilege_in_cooldown():
        return False
    if (os.environ.get("CI") or "").strip():
        return False
    return True


def apply_embedded_wg(
    conf_path: Path,
    *,
    binary: Path | None = None,
    allow_privilege: bool | None = None,
) -> dict[str, Any]:
    """Start userspace WG + UAPI + routes. Honest ok; may prompt on macOS once."""
    conf_path = Path(conf_path)
    text = conf_path.read_text(encoding="utf-8")
    conf = parse_wg_quick_conf(text)
    binary = binary or ensure_operator_binary(force=False)
    force_priv = (os.environ.get("GRIP_WG_PRIV") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if allow_privilege is None:
        allow_privilege = privilege_prompts_allowed()

    unpriv = _apply_unprivileged(conf_path, conf, binary=binary)
    if unpriv.get("ok"):
        clear_privilege_denied()
        return unpriv

    system = platform.system().lower()
    if (
        allow_privilege
        and system == "darwin"
        and unpriv.get("need_privilege")
        and (force_priv or not privilege_in_cooldown())
    ):
        # Drop partial unprivileged process before admin bootstrap.
        if unpriv.get("pid"):
            _kill_pid(int(unpriv["pid"]))
        _clear_pid(str(unpriv.get("iface") or "utun"))
        _clear_pid("utun")
        priv = _apply_darwin_privileged(conf_path, binary=binary)
        if priv.get("ok"):
            clear_privilege_denied()
            return priv
        # User canceled or admin bring-up failed — cool down to avoid prompt spam.
        detail = str(priv.get("detail") or unpriv.get("detail") or "")
        mark_privilege_denied()
        return {
            "ok": False,
            "error": priv.get("error") or "wg_privilege_failed",
            "detail": detail,
            "unprivileged": unpriv,
            "privileged": priv,
            "fallback": "relay",
            "privilege_cooldown_sec": DEFAULT_PRIV_COOLDOWN_SEC,
        }

    # Linux / no privilege path: clean fail → caller uses relay.
    if unpriv.get("pid") and unpriv.get("error") == "net_configure_failed":
        # Interface up but no routes — not usable for SSH; tear down.
        _kill_pid(int(unpriv["pid"]))
        _clear_pid(str(unpriv.get("iface") or ""))
    return {
        **unpriv,
        "ok": False,
        "fallback": "relay",
        "privilege_cooldown": privilege_in_cooldown(),
    }


def stop_embedded_wg(iface: str | None = None) -> dict[str, Any]:
    targets = []
    if iface:
        targets.append(iface)
    else:
        run_dir = ensure_home() / "run"
        if run_dir.is_dir():
            for p in run_dir.glob("wireguard-*.pid"):
                targets.append(p.stem.replace("wireguard-", "", 1))
    stopped = []
    privileged_kills = []
    for name in targets:
        pid = _read_pid(name)
        if pid and _pid_alive(pid):
            _kill_pid(pid)
            if _pid_alive(pid) and platform.system().lower() == "darwin":
                # Likely root-owned from admin bootstrap. Explicit stop may
                # prompt once even during bring-up cooldown.
                try:
                    from grip.tunnel.wgpriv import darwin_admin_kill

                    res = darwin_admin_kill(pid)
                    privileged_kills.append({"pid": pid, **res})
                except Exception as exc:
                    privileged_kills.append(
                        {"pid": pid, "ok": False, "detail": str(exc)}
                    )
            if not _pid_alive(pid):
                stopped.append(pid)
        _clear_pid(name)
    return {
        "ok": True,
        "stopped": stopped,
        "privileged_kills": privileged_kills,
    }
