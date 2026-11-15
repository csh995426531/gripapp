from __future__ import annotations

import base64
import os
import re
import shlex
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from grip.resources import Resource
from grip.vault import Vault

WRITE_RE = re.compile(
    r"(?i)(\brm\s|\bshutdown\b|\breboot\b|\bmkfs\b|\bdd\s|[^\w]>\s|/etc/|"
    r"Remove-Item|Stop-Computer|Restart-Computer)",
)


@dataclass
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


def looks_like_write(command: str) -> bool:
    return bool(WRITE_RE.search(command))


_CLIXML_ERROR_RE = re.compile(
    r'<S\s+S="Error">([^<]*)</S>', re.IGNORECASE
)
_CLIXML_HEX_RE = re.compile(r"_x([0-9A-Fa-f]{4})_")


def decode_remote_output(data: bytes, *, windows: bool = False) -> str:
    """Decode SSH stdout/stderr bytes for the remote OS.

    Chinese Windows console tools (``ipconfig``, ``systeminfo``, …) typically
    emit GBK/CP936. Decoding those as UTF-8 produces mojibake like ``����``.
    Prefer strict UTF-8, then GBK on Windows; never leave binary as Latin-1.
    """
    if not data:
        return ""
    if not windows:
        return data.decode("utf-8", errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    for enc in ("gbk", "cp936", "gb18030"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    # Mixed/partial: score utf-8-replace vs gbk-replace by replacement count.
    u8 = data.decode("utf-8", errors="replace")
    gbk = data.decode("gbk", errors="replace")
    if u8.count("\ufffd") <= gbk.count("\ufffd"):
        return u8
    return gbk


def _is_windows_resource(resource: Resource) -> bool:
    return (resource.meta.get("os") or "").lower() in {"windows", "win", "win32"}


def scrub_powershell_clixml(text: str) -> str:
    """Turn PowerShell CLIXML stderr into readable text for the panel.

    Windows OpenSSH often returns ``#< CLIXML`` progress/error blobs instead of
    plain text; keep real stdout and extract Error records when present.
    """
    if not text:
        return ""
    if "#< CLIXML" not in text and "<Objs " not in text:
        return text

    def _decode_ps(s: str) -> str:
        s = s.replace("_x000D_", "\r").replace("_x000A_", "\n")
        return _CLIXML_HEX_RE.sub(lambda m: chr(int(m.group(1), 16)), s)

    errors = [_decode_ps(e) for e in _CLIXML_ERROR_RE.findall(text)]
    head = text.split("#< CLIXML", 1)[0].strip()
    if errors:
        body = "\n".join(e.strip() for e in errors if e.strip())
        if head:
            return f"{head}\n{body}".strip()
        return body.strip()
    return head or text.strip()


# Windows CreateProcess command-line limit is ~8191; stay under with margin.
_WIN_ARGV_LIMIT = 7500


def windows_powershell_remote(command: str) -> str:
    """Wrap a PowerShell script for Windows OpenSSH without quote breakage.

    ``powershell -Command`` + POSIX ``shlex.quote`` routinely fails on Windows
    OpenSSH with「字符串缺少终止符」. ``-EncodedCommand`` (UTF-16LE base64)
    avoids metacharacter issues for scripts that fit the argv limit.
    """
    encoded = base64.b64encode(command.encode("utf-16-le")).decode("ascii")
    remote = (
        "powershell -NoProfile -NonInteractive -EncodedCommand " + encoded
    )
    if len(remote) <= _WIN_ARGV_LIMIT:
        return remote
    # Too large for EncodedCommand argv — keep a short -Command that still
    # avoids nested quote hell by using only single-quoted -EncodedCommand
    # is impossible here; raise a clear error so callers (agent) can fall back.
    raise ValueError(
        f"windows_powershell_script_too_long:{len(remote)}>{_WIN_ARGV_LIMIT}"
    )


def _remote_command(resource: Resource, command: str) -> str:
    os_name = (resource.meta.get("os") or "unix").lower()
    if os_name in {"windows", "win", "win32"}:
        return windows_powershell_remote(command)
    return command


def _run_ssh_key(
    resource: Resource,
    vault: Vault,
    command: str,
    *,
    timeout_sec: int,
    os_wrap: bool = True,
    connect_timeout_sec: int | None = None,
) -> ExecResult:
    from grip.ssh_session import key_ssh_mux_opts

    key_material = vault.get(resource.secret_id)  # type: ignore[arg-type]
    host = resource.meta["host"]
    user = resource.meta["user"]
    port = int(resource.meta.get("port") or 22)
    remote = _remote_command(resource, command) if os_wrap else command
    # Fail dead hosts quickly; keep overall timeout for slow remote commands.
    connect_s = max(1, int(connect_timeout_sec if connect_timeout_sec is not None else min(30, timeout_sec)))

    with tempfile.NamedTemporaryFile(
        "w", delete=False, prefix="grip-ssh-", suffix=".pem"
    ) as tf:
        tf.write(key_material)
        if not key_material.endswith("\n"):
            tf.write("\n")
        key_path = tf.name
    try:
        os.chmod(key_path, 0o600)
        cmd = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            f"ConnectTimeout={connect_s}",
            *key_ssh_mux_opts(resource),
            "-i",
            key_path,
            "-p",
            str(port),
            f"{user}@{host}",
            remote,
        ]
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout_sec,
            check=False,
        )
        win = _is_windows_resource(resource)
        return ExecResult(
            proc.returncode,
            decode_remote_output(proc.stdout or b"", windows=win),
            decode_remote_output(proc.stderr or b"", windows=win),
        )
    finally:
        try:
            Path(key_path).unlink(missing_ok=True)
        except OSError:
            pass


def _run_ssh_password(
    resource: Resource,
    vault: Vault,
    command: str,
    *,
    timeout_sec: int,
    os_wrap: bool = True,
    connect_timeout_sec: int | None = None,
) -> ExecResult:
    from grip.ssh_session import get_paramiko_client, invalidate_paramiko

    remote = _remote_command(resource, command) if os_wrap else command
    connect_s = max(1, int(connect_timeout_sec if connect_timeout_sec is not None else min(30, timeout_sec)))
    win = _is_windows_resource(resource)

    # Reuse a long-lived paramiko session (metrics every ~10s should not
    # reconnect each time). On channel failure, drop cache and retry once.
    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            client = get_paramiko_client(
                resource, vault, connect_timeout_sec=connect_s
            )
            _stdin, stdout, stderr = client.exec_command(remote, timeout=timeout_sec)
            out = decode_remote_output(stdout.read() or b"", windows=win)
            err = decode_remote_output(stderr.read() or b"", windows=win)
            code = stdout.channel.recv_exit_status()
            return ExecResult(code, out, err)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            invalidate_paramiko(resource.name)
            if attempt == 0:
                continue
            raise
    raise RuntimeError(str(last_exc) if last_exc else "ssh_failed")


def run_ssh(
    resource: Resource,
    vault: Vault,
    command: str,
    *,
    timeout_sec: int = 60,
    os_wrap: bool = True,
    connect_timeout_sec: int | None = None,
) -> ExecResult:
    """Run command over SSH (per-host concurrency capped; sessions reused).

    When os_wrap is True (default), Windows meta.os wraps the command in
    powershell -Command. Pass os_wrap=False for raw probes (e.g. OS detection)
    so a wrong/default meta.os cannot double-wrap or mis-route the check.

    connect_timeout_sec caps TCP/SSH handshake wait (defaults to min(30,
    timeout_sec)). Metrics sampling passes a shorter value so unreachable
    hosts do not block the 10s quiet poll / native tick.

    Per-host parallel SSH is limited (GRIP_SSH_MAX_PER_HOST, default 3); extra
    callers queue. Password auth keeps a paramiko client; key auth uses
    OpenSSH ControlMaster.
    """
    from grip.ssh_limit import HOST_SSH_GATE

    if resource.kind != "ssh":
        raise ValueError(f"resource {resource.name} is not ssh")
    if not resource.secret_id:
        raise RuntimeError("secret_missing")

    # Queue for a host slot; don't wait longer than the command budget.
    wait_s = float(timeout_sec) + 15.0
    with HOST_SSH_GATE.slot(resource.name, timeout_sec=wait_s):
        auth = (resource.meta.get("auth") or "key").lower()
        if auth == "password":
            return _run_ssh_password(
                resource,
                vault,
                command,
                timeout_sec=timeout_sec,
                os_wrap=os_wrap,
                connect_timeout_sec=connect_timeout_sec,
            )
        return _run_ssh_key(
            resource,
            vault,
            command,
            timeout_sec=timeout_sec,
            os_wrap=os_wrap,
            connect_timeout_sec=connect_timeout_sec,
        )


def needs_os_probe(os_name: str | None) -> bool:
    """True when meta.os is missing or the ambiguous default 'unix'."""
    o = (os_name or "").strip().lower()
    if not o:
        return True
    if o in {"windows", "win", "win32", "linux", "macos", "darwin"}:
        return False
    # Generic "unix" (add_ssh default) may be wrong for Windows hosts.
    return o == "unix"


def normalize_detected_os(raw: str | None) -> str | None:
    """Map probe/uname output → meta.os value (windows|linux|macos|unix)."""
    if not raw:
        return None
    s = raw.strip().lower()
    if not s:
        return None
    if "windows" in s or s in {"win", "win32"}:
        return "windows"
    if "linux" in s:
        return "linux"
    if "darwin" in s or "macos" in s or s == "mac":
        return "macos"
    # Other uname -s (FreeBSD, etc.)
    if re.fullmatch(r"[a-z0-9._-]+", s) and "windows" not in s:
        return "unix"
    return None


def detect_remote_os(
    resource: Resource,
    vault: Vault,
    *,
    timeout_sec: int = 12,
) -> str | None:
    """Cheap SSH OS probe without trusting meta.os wrapping.

    Tries ``uname -s`` first, then a Windows PowerShell check.
    Returns windows|linux|macos|unix, or None if detection fails.
    """
    if resource.kind != "ssh":
        return None
    try:
        unix = run_ssh(
            resource,
            vault,
            "uname -s",
            timeout_sec=timeout_sec,
            os_wrap=False,
        )
        if unix.exit_code == 0:
            mapped = normalize_detected_os(unix.stdout.splitlines()[0] if unix.stdout else "")
            if mapped and mapped != "windows":
                return mapped
    except Exception:  # noqa: BLE001
        pass

    win_cmd = (
        "powershell -NoProfile -NonInteractive -Command "
        "\"Write-Output $env:OS\""
    )
    try:
        win = run_ssh(
            resource,
            vault,
            win_cmd,
            timeout_sec=timeout_sec,
            os_wrap=False,
        )
        if win.exit_code == 0:
            mapped = normalize_detected_os(win.stdout)
            if mapped == "windows":
                return "windows"
    except Exception:  # noqa: BLE001
        pass
    return None


def remote_file_exists(resource: Resource, vault: Vault, path: str) -> bool:
    os_name = (resource.meta.get("os") or "unix").lower()
    if os_name in {"windows", "win", "win32"}:
        cmd = (
            f"if (Test-Path -LiteralPath "
            f"'{path.replace(chr(39), chr(39)+chr(39))}') {{ exit 0 }} else {{ exit 1 }}"
        )
    else:
        cmd = f"test -e {shlex.quote(path)}"
    result = run_ssh(resource, vault, cmd, timeout_sec=30)
    return result.exit_code == 0
