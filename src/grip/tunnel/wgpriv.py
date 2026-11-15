from __future__ import annotations

"""Privilege helpers for WireGuard bring-up.

macOS: one `osascript` admin prompt runs a root bootstrap script.
Linux: best-effort `setcap cap_net_admin` / `sudo -n`.
"""

import os
import platform
import shlex
import subprocess
import tempfile
import textwrap
from pathlib import Path
from typing import Any


def _run(cmd: list[str], timeout: float = 120.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, check=False
    )


_ADMIN_PROMPT = "Grip 需要在这台 Mac 上建立直连网卡。请输入这台 Mac 的登录密码。"
_ADMIN_KILL_PROMPT = "Grip 需要管理员权限才能关闭直连网卡。请输入这台 Mac 的登录密码。"


def _as_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def darwin_admin_source(script_path: Path, out_path: Path, err_path: Path, prompt: str) -> str:
    """AppleScript whose dialog is attributed to an app named Grip, not osascript."""
    return "\n".join(
        [
            f"set scriptPath to {_as_string(str(script_path))}",
            f"set outPath to {_as_string(str(out_path))}",
            f"set errPath to {_as_string(str(err_path))}",
            f"set promptText to {_as_string(prompt)}",
            "try",
            "  set output to do shell script \"/bin/bash \" & quoted form of scriptPath with prompt promptText with administrator privileges",
            "  do shell script \"printf %s \" & quoted form of output & \" > \" & quoted form of outPath",
            "on error errMsg number errNum",
            "  do shell script \"printf %s \" & quoted form of ((errNum as text) & \":\" & errMsg) & \" > \" & quoted form of errPath",
            "end try",
            "",
        ]
    )


def build_grip_admin_app(source: str, dest_dir: Path) -> Path:
    """Compile an applet named Grip so the macOS password dialog says Grip."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    src = dest_dir / "grip-admin.applescript"
    app = dest_dir / "Grip.app"
    src.write_text(source, encoding="utf-8")
    if app.exists():
        import shutil

        shutil.rmtree(app)
    proc = _run(["osacompile", "-o", str(app), str(src)], timeout=30)
    if proc.returncode != 0 or not app.is_dir():
        detail = ((proc.stderr or proc.stdout or "").strip())[:300]
        raise RuntimeError(detail or "osacompile_failed")
    plist_path = app / "Contents" / "Info.plist"
    if plist_path.is_file():
        import plistlib

        info = plistlib.loads(plist_path.read_bytes())
        info["CFBundleName"] = "Grip"
        info["CFBundleDisplayName"] = "Grip"
        plist_path.write_bytes(plistlib.dumps(info))
    return app


def darwin_admin_shell(
    shell_cmd: str,
    *,
    timeout: float = 180.0,
    prompt: str | None = None,
) -> dict[str, Any]:
    """Run a shell command with macOS administrator privileges (one GUI prompt).

    The dialog is a Grip applet, so macOS shows Grip instead of osascript.
    """
    import shutil

    work = Path(tempfile.mkdtemp(prefix="grip-admin-"))
    script_path = work / "command.sh"
    out_path = work / "out.txt"
    err_path = work / "err.txt"
    script_path.write_text(shell_cmd if shell_cmd.endswith("\n") else shell_cmd + "\n", encoding="utf-8")
    script_path.chmod(0o700)
    text = prompt or _ADMIN_PROMPT
    try:
        source = darwin_admin_source(script_path, out_path, err_path, text)
        app = build_grip_admin_app(source, work)
        proc = _run(["open", "-W", "-n", str(app)], timeout=timeout)
        stdout = out_path.read_text(encoding="utf-8", errors="replace") if out_path.is_file() else ""
        stderr = err_path.read_text(encoding="utf-8", errors="replace") if err_path.is_file() else ""
        if not stderr and proc.returncode != 0:
            stderr = (proc.stderr or proc.stdout or "").strip()
        ok = bool(stdout) and not stderr and proc.returncode == 0
        # User cancel is an AppleScript error number, not a shell failure.
        if stderr.startswith("-128:") or "User canceled" in stderr or "(-128)" in stderr:
            ok = False
        elif stdout and not stderr:
            ok = True
        return {
            "ok": ok,
            "returncode": 0 if ok else (proc.returncode or 1),
            "stdout": stdout.strip(),
            "stderr": stderr.strip(),
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)


def linux_setcap_net_admin(binary: Path) -> dict[str, Any]:
    """Try to grant CAP_NET_ADMIN on wireguard-go (non-interactive)."""
    binary = Path(binary)
    if not binary.is_file():
        return {"ok": False, "error": "binary_missing"}
    # Prefer passwordless sudo; fall back to plain setcap (may work for root).
    attempts = [
        ["sudo", "-n", "setcap", "cap_net_admin,cap_net_bind_service+ep", str(binary)],
        ["setcap", "cap_net_admin,cap_net_bind_service+ep", str(binary)],
    ]
    details: list[str] = []
    for cmd in attempts:
        proc = _run(cmd, timeout=15)
        if proc.returncode == 0:
            return {"ok": True, "cmd": cmd}
        details.append(
            f"{' '.join(cmd)} → {(proc.stderr or proc.stdout or '')[:160]}"
        )
    return {"ok": False, "error": "setcap_failed", "detail": " | ".join(details)}


def write_darwin_priv_bootstrap(
    *,
    binary: Path,
    conf_path: Path,
    remote_wg_py: Path,
    log_path: Path,
    pid_path: Path,
    uapi_dir: Path,
    python_bin: str | None = None,
) -> Path:
    """Write a root bootstrap script: start wireguard-go, UAPI, ifconfig/routes."""
    import sys

    py = python_bin or sys.executable or "/usr/bin/python3"
    script = textwrap.dedent(
        f"""\
        #!/bin/bash
        set -e
        BIN={shlex.quote(str(binary))}
        CONF={shlex.quote(str(conf_path))}
        BOOT={shlex.quote(str(remote_wg_py))}
        LOG={shlex.quote(str(log_path))}
        PIDF={shlex.quote(str(pid_path))}
        UAPI={shlex.quote(str(uapi_dir))}
        PY={shlex.quote(str(py))}
        mkdir -p "$UAPI" "$(dirname "$LOG")" "$(dirname "$PIDF")"
        chmod 755 "$UAPI" || true
        # Stop previous
        if [ -f "$PIDF" ]; then
          kill "$(cat "$PIDF")" 2>/dev/null || true
          rm -f "$PIDF"
        fi
        rm -f "$UAPI"/utun*.sock 2>/dev/null || true
        export WG_PROCESS_FOREGROUND=1
        nohup "$BIN" -f utun >>"$LOG" 2>&1 &
        echo $! >"$PIDF"
        # Wait for utun sock (root-created; chmod so user can diagnose)
        for i in $(seq 1 60); do
          SOCK=$(ls "$UAPI"/utun*.sock 2>/dev/null | head -1 || true)
          if [ -n "$SOCK" ]; then
            chmod 666 "$SOCK" 2>/dev/null || true
            break
          fi
          sleep 0.1
        done
        if [ -z "${{SOCK:-}}" ]; then
          echo "ERR utun_sock_missing" >&2
          exit 1
        fi
        # Prefer the same interpreter Grip is running; fall back to PATH python3.
        if [ ! -x "$PY" ]; then
          PY="$(command -v python3 || true)"
        fi
        if [ -z "$PY" ]; then
          echo "ERR python3_missing" >&2
          exit 1
        fi
        # remote_wg.py applies UAPI + ifconfig/routes only (daemon already up)
        "$PY" "$BOOT" "$CONF" --apply-only
        echo OK
        """
    )
    fd, path = tempfile.mkstemp(prefix="grip-wg-priv-", suffix=".sh")
    os.close(fd)
    p = Path(path)
    p.write_text(script, encoding="utf-8")
    p.chmod(0o700)
    return p


def darwin_privileged_wg_up(
    *,
    binary: Path,
    conf_path: Path,
    remote_wg_py: Path | None = None,
    log_path: Path,
    pid_path: Path,
    uapi_dir: Path,
) -> dict[str, Any]:
    """One admin prompt: start embedded WG as root and configure addresses."""
    if platform.system().lower() != "darwin":
        return {"ok": False, "error": "not_darwin"}
    boot = remote_wg_py or (
        Path(__file__).resolve().parent / "remote_wg.py"
    )
    if not boot.is_file():
        return {"ok": False, "error": "remote_wg_missing"}
    import sys

    script = write_darwin_priv_bootstrap(
        binary=binary,
        conf_path=conf_path,
        remote_wg_py=boot,
        log_path=log_path,
        pid_path=pid_path,
        uapi_dir=uapi_dir,
        python_bin=sys.executable,
    )
    try:
        # Run the script itself with admin rights (single prompt).
        # Keep the script until osascript finishes (it runs synchronously).
        result = darwin_admin_shell(f"/bin/bash {shlex.quote(str(script))}")
        result["script"] = str(script)
        result["python"] = sys.executable
        return result
    finally:
        try:
            script.unlink()
        except OSError:
            pass


def darwin_admin_kill(pid: int) -> dict[str, Any]:
    """Best-effort admin kill for a root-owned wireguard-go process."""
    if pid <= 0:
        return {"ok": False, "error": "bad_pid"}
    return darwin_admin_shell(
        f"/bin/kill {int(pid)} || /bin/kill -9 {int(pid)} || true",
        prompt=_ADMIN_KILL_PROMPT,
    )
