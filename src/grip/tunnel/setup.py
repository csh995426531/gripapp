from __future__ import annotations

"""Pick a public SSH host as rendezvous and enroll LAN devices from the panel."""

import base64
import json
import secrets
from pathlib import Path
from typing import Any

from grip.resources import ResourceStore
from grip.ssh_exec import run_ssh
from grip.tunnel.enroll import enroll_resource
from grip.tunnel.hosts import is_lan_host
from grip.tunnel.store import TunnelStore
from grip.vault import Vault

RENDEZVOUS_PORT = 443
# Localhost only, used when nginx already owns public 443.
_BEHIND_PORT = 17443

_SERVE_PY = """#!/usr/bin/env python3
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from grip.tunnel.rendezvous import start_rendezvous

cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
public = int(cfg.get("port") or 443)
listen_port = int(cfg.get("listen_port") or public)
httpd, _url = start_rendezvous(
    str(cfg.get("listen_host") or "0.0.0.0"),
    listen_port,
    background=False,
    token=str(cfg.get("token") or ""),
    stun_port=public,
)
print("GRIP_RENDEZVOUS_OK", flush=True)
httpd.serve_forever()
"""

_BOOT_PY = r'''
import base64, json, os, subprocess, sys, time
from pathlib import Path

blob = json.loads(base64.b64decode(os.environ["GRIP_RV_BLOB"]).decode("utf-8"))
root = Path.home() / ".grip-rendezvous"
for rel, b64 in blob["files"].items():
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(base64.b64decode(b64))
public = int(blob.get("port") or 443)
listen_host, listen_port = "0.0.0.0", public

def _tcp_busy(number):
    import socket
    sock = socket.socket()
    try:
        sock.bind(("0.0.0.0", int(number)))
        return False
    except OSError:
        return True
    finally:
        sock.close()

def _attach_nginx(upstream):
    nl = chr(10)
    snippet = nl.join([
        "location = /health {",
        f"    proxy_pass http://127.0.0.1:{upstream}/health;",
        "    proxy_http_version 1.1;",
        "    proxy_set_header Host $host;",
        '    proxy_set_header Connection "";',
        "}",
        "location = /v1/relay {",
        f"    proxy_pass http://127.0.0.1:{upstream}/v1/relay;",
        "    proxy_http_version 1.1;",
        "    proxy_set_header Host $host;",
        "    proxy_set_header Upgrade $http_upgrade;",
        '    proxy_set_header Connection "upgrade";',
        "    proxy_buffering off;",
        "    proxy_request_buffering off;",
        "    proxy_read_timeout 3600s;",
        "}",
        "location /v1/ {",
        f"    proxy_pass http://127.0.0.1:{upstream};",
        "    proxy_http_version 1.1;",
        "    proxy_set_header Host $host;",
        '    proxy_set_header Connection "";',
        "}",
        "",
    ])
    dest = Path("/tmp/grip-rendezvous.conf")
    dest.write_text(snippet, encoding="utf-8")
    subprocess.check_call(["sudo", "-n", "mkdir", "-p", "/etc/nginx/snippets"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.check_call(["sudo", "-n", "cp", str(dest), "/etc/nginx/snippets/grip-rendezvous.conf"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    include = "include /etc/nginx/snippets/grip-rendezvous.conf;"
    roots = list(Path("/etc/nginx/sites-enabled").glob("*")) + list(Path("/etc/nginx/conf.d").glob("*.conf"))
    touched = False
    for site in roots:
        if not site.is_file():
            continue
        raw = site.read_text(encoding="utf-8", errors="replace")
        if include in raw:
            touched = True
            continue
        lines = raw.splitlines(keepends=True)
        out = []
        added = False
        for line in lines:
            out.append(line)
            bare = line.split("#", 1)[0]
            if (not added) and bare.strip().startswith("listen") and "443" in bare:
                indent = line[: len(line) - len(line.lstrip())]
                out.append(f"{indent}{include}{nl}")
                added = True
        if added:
            tmp = Path("/tmp/grip-nginx-site")
            tmp.write_text("".join(out), encoding="utf-8")
            subprocess.check_call(["sudo", "-n", "cp", str(tmp), str(site)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            touched = True
    if not touched:
        raise RuntimeError("nginx_443_not_found")
    subprocess.check_call(["sudo", "-n", "nginx", "-t"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.check_call(["sudo", "-n", "nginx", "-s", "reload"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

if _tcp_busy(public):
    listen_host, listen_port = "127.0.0.1", 17443
    _attach_nginx(listen_port)
cfg_path = root / "config.json"
cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
cfg["port"] = public
cfg["listen_host"] = listen_host
cfg["listen_port"] = listen_port
cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
log = root / "serve.log"
pidf = root / "serve.pid"
if pidf.is_file():
    try:
        os.kill(int(pidf.read_text().strip()), 15)
    except Exception:
        pass
unit_dir = Path.home() / ".config" / "systemd" / "user"
unit = unit_dir / "grip-rendezvous.service"
systemd_ok = False
try:
    unit_dir.mkdir(parents=True, exist_ok=True)
    py = sys.executable
    unit.write_text(
        "[Unit]\nDescription=Grip rendezvous\n"
        "[Service]\n"
        f"ExecStart={py} {root / 'serve.py'}\n"
        "Restart=always\n"
        f"WorkingDirectory={root}\n"
        "[Install]\nWantedBy=default.target\n",
        encoding="utf-8",
    )
    subprocess.check_call(
        ["systemctl", "--user", "daemon-reload"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.check_call(
        ["systemctl", "--user", "enable", "grip-rendezvous"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.check_call(
        ["systemctl", "--user", "restart", "grip-rendezvous"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    systemd_ok = True
except Exception:
    systemd_ok = False
if not systemd_ok:
    proc = subprocess.Popen(
        [sys.executable, str(root / "serve.py")],
        stdout=open(log, "ab"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    pidf.write_text(str(proc.pid), encoding="utf-8")

def _local_ok(host, number):
    import socket
    target = "127.0.0.1" if host in {"0.0.0.0", "::", ""} else host
    req = b"GET /health HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n"
    try:
        sock = socket.create_connection((target, int(number)), timeout=1)
    except OSError:
        return False
    try:
        sock.settimeout(1)
        sock.sendall(req)
        data = b""
        while len(data) < 2000:
            part = sock.recv(256)
            if not part:
                break
            data += part
        head = data.split(b"\r\n", 1)[0]
        return head.startswith(b"HTTP/") and b" 200" in head and b'"ok"' in data
    except OSError:
        return False
    finally:
        sock.close()

ready = False
for _ in range(40):
    if _local_ok(listen_host, listen_port):
        ready = True
        break
    time.sleep(0.25)
print("GRIP_RENDEZVOUS_OK" if ready else "GRIP_RENDEZVOUS_FAIL")
'''


def _refused(exc: BaseException) -> bool:
    return isinstance(exc, ConnectionRefusedError) or getattr(exc, "errno", None) in {
        61,
        111,
        10061,
    }


def _tcp_exchange(host: str, port: int, payload: bytes, timeout: float) -> tuple[str, bytes]:
    """Return (status, payload). Status is open only after a completed read.

    A local proxy can finish the TCP handshake and then swallow the bytes.
    Handshake success alone is not an open port.
    """
    import socket

    chunks = b""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(payload)
            while len(chunks) < 4096:
                part = sock.recv(1024)
                if not part:
                    return "open", chunks
                chunks += part
            return "open", chunks
    except ConnectionRefusedError:
        return "refused", b""
    except TimeoutError:
        return ("open", chunks) if chunks else ("closed", b"")
    except OSError as exc:
        if _refused(exc):
            return "refused", b""
        return ("open", chunks) if chunks else ("closed", b"")


def _health_status(host: str, port: int, timeout: float) -> str:
    request = f"GET /health HTTP/1.0\r\nHost: {host}\r\n\r\n".encode("ascii")
    status, body = _tcp_exchange(host, port, request, timeout)
    if status != "open":
        return status
    text = body.decode("utf-8", errors="replace")
    head = text.split("\r\n", 1)[0]
    if head.startswith("HTTP/") and " 200" in head and '"ok"' in text:
        return "open"
    if head.startswith("HTTP/"):
        return "foreign"
    return "closed"


def _tls_health_status(host: str, port: int, timeout: float) -> str:
    """Health check through TLS. An existing nginx on 443 speaks HTTPS only."""
    import socket
    import ssl

    request = f"GET /health HTTP/1.0\r\nHost: {host}\r\n\r\n".encode("ascii")
    try:
        raw = socket.create_connection((host, int(port)), timeout=timeout)
        raw.settimeout(timeout)
    except ConnectionRefusedError:
        return "refused"
    except TimeoutError:
        return "closed"
    except OSError as exc:
        if _refused(exc):
            return "refused"
        return "closed"
    try:
        ctx = ssl._create_unverified_context()
        sock = ctx.wrap_socket(raw, server_hostname=host)
        sock.settimeout(timeout)
        sock.sendall(request)
        chunks = b""
        while len(chunks) < 4096:
            part = sock.recv(1024)
            if not part:
                break
            chunks += part
    except (TimeoutError, ssl.SSLError, OSError):
        return "closed"
    finally:
        try:
            raw.close()
        except OSError:
            pass
    text = chunks.decode("utf-8", errors="replace")
    head = text.split("\r\n", 1)[0]
    if head.startswith("HTTP/") and " 200" in head and '"ok"' in text:
        return "open"
    if head.startswith("HTTP/"):
        return "foreign"
    return "closed"


def choose_base_url(host: str, port: int = RENDEZVOUS_PORT) -> str:
    """Use https when 443 is an existing TLS website, otherwise plain HTTP."""
    if _tls_health_status(host, int(port), 3.0) == "open":
        return f"https://{host}:{int(port)}"
    if _health_status(host, int(port), 3.0) == "open":
        return f"http://{host}:{int(port)}"
    return ""


def probe_rendezvous_ports(
    host: str, port: int = RENDEZVOUS_PORT, timeout: float = 3.0
) -> list[dict[str, Any]]:
    """443/TCP is the only port the user depends on.

    Relay shares that HTTP port. When nginx already owns 443, health is HTTPS.
    UDP 443 is tried later for direct connect and is not a firewall task.
    ``reason`` is ``refused`` when nothing is listening, ``foreign`` when some
    other site answered, and ``closed`` when no reply comes back.
    """
    tls = _tls_health_status(host, int(port), timeout)
    plain = _health_status(host, int(port), timeout)
    if tls == "open" or plain == "open":
        return []
    if tls == "foreign" or plain == "foreign":
        reason = "foreign"
    elif tls == "refused" or plain == "refused":
        reason = "refused"
    else:
        reason = "closed"
    return [{"port": int(port), "proto": "TCP", "role": "汇合", "reason": reason}]


def people_enroll_hint(name: str, detail: str) -> str:
    """One next step for a device that did not finish receiving the tunnel."""
    text = (detail or "").lower()
    if "python_not_found" in text or ("python" in text and "not found" in text):
        return f"下一步：在 {name} 上安装 python3，然后点重试。"
    if "banner" in text or "error reading ssh" in text:
        return f"下一步：打开 {name}，确认 SSH 能连上，然后点重试。刚才传到一半时，这台机器没有正常回应。"
    if "timed out" in text or "timeout" in text:
        return f"下一步：打开 {name}，确认它开着并且 SSH 能连上，然后点重试。刚才这台机器没有回应。"
    if "permission denied" in text or "authentication failed" in text:
        return f"下一步：检查 {name} 的登录账号或密钥，然后点重试。现在登不上这台机器。"
    return f"下一步：点重试，再把隧道程序传到 {name}。刚才没有传完。"


def _machine_label(host_name: str, host: str) -> str:
    name = (host_name or "").strip() or "这台汇合点"
    addr = (host or "").strip()
    if addr and addr not in name:
        return f"「{name}」（{addr}）"
    return f"「{name}」"


def port_gap_hint(
    gaps: list[dict[str, Any]],
    *,
    port: int = RENDEZVOUS_PORT,
    host_name: str = "",
    host: str = "",
) -> str:
    if not gaps:
        return ""
    where = _machine_label(host_name, host)
    reach_port = int(port)
    if any(
        int(item.get("port") or 0) == reach_port and item.get("reason") == "refused"
        for item in gaps
    ):
        return f"下一步：在 {where} 上重新启动汇合点。它没在 443 上听着。点重试。"
    if any(
        int(item.get("port") or 0) == reach_port and item.get("reason") == "foreign"
        for item in gaps
    ):
        return f"下一步：点重试。{where} 的 443 上是原来的网站，汇合点还没挂上去。"
    return f"下一步：点重试。{where} 的 443 没有回应。"


def _host_of(meta: dict[str, Any]) -> str:
    return str(meta.get("lan_host") or meta.get("host") or "").strip()


def _ssh_resources(resources: ResourceStore) -> list[Any]:
    return [r for r in resources.list() if getattr(r, "kind", "") == "ssh"]


def rendezvous_plan(
    resources: ResourceStore, store: TunnelStore | None = None
) -> dict[str, Any]:
    store = store or TunnelStore()
    settings = store.settings()
    candidates: list[dict[str, str]] = []
    lan: list[str] = []
    for r in _ssh_resources(resources):
        host = _host_of(r.meta or {})
        if not host:
            continue
        if is_lan_host(host):
            lan.append(r.name)
        else:
            candidates.append({"name": r.name, "host": host})
    candidates.sort(key=lambda c: c["name"])
    lan.sort()
    enrolled = {
        r.name
        for r in _ssh_resources(resources)
        if isinstance((r.meta or {}).get("tunnel"), dict)
        and (r.meta or {}).get("tunnel", {}).get("enabled")
    }
    pending = [name for name in lan if name not in enrolled]
    url = str(settings.get("rendezvous_url") or "").strip()
    dismissed = bool(settings.get("rendezvous_prompt_dismissed"))
    raw_gaps = settings.get("rendezvous_port_gaps") or []
    gaps = [item for item in raw_gaps if isinstance(item, dict) and item.get("port")]
    host_name = str(settings.get("rendezvous_host") or "")
    public_host = next((c["host"] for c in candidates if c["name"] == host_name), "")
    active = None
    if url:
        active = {
            "url": url,
            "host_name": host_name,
            "host": public_host,
        }
    offer = bool(candidates) and bool(lan) and not url
    # Only devices that have never been enrolled belong on the strip.
    # A device that has already been enrolled may show「支持中继」or「支持多通道」,
    # and only after a rendezvous SSH banner has been confirmed.
    show_ports = bool(gaps) and not dismissed
    show_retry = bool(url) and bool(pending) and not dismissed and not show_ports
    return {
        "offer": offer,
        "show_prompt": offer and not dismissed and not show_ports,
        "show_retry": show_retry,
        "show_ports": show_ports,
        "dismissed": dismissed,
        "candidates": candidates,
        "lan_devices": lan,
        "pending_lan": pending,
        "active": active,
        "host_name": host_name,
        "port_gaps": gaps,
        "port_hint": port_gap_hint(gaps, host_name=host_name, host=public_host),
    }


def dismiss_rendezvous_prompt(
    store: TunnelStore | None = None, *, dismissed: bool = True
) -> dict[str, Any]:
    store = store or TunnelStore()
    store.set_settings({"rendezvous_prompt_dismissed": bool(dismissed)})
    return {"ok": True, "dismissed": bool(dismissed)}


def _posix(meta: dict[str, Any]) -> bool:
    os_name = str(meta.get("os") or "unix").lower()
    return os_name not in {"windows", "win", "win32"}


def _remote_files(token: str, port: int) -> dict[str, str]:
    root = Path(__file__).resolve().parent
    raw = {
        "grip/__init__.py": b"",
        "grip/tunnel/__init__.py": b"",
        "grip/tunnel/stun.py": (root / "stun.py").read_bytes(),
        "grip/tunnel/rendezvous.py": (root / "rendezvous.py").read_bytes(),
        "config.json": json.dumps({"token": token, "port": int(port)}).encode("utf-8"),
        "serve.py": _SERVE_PY.encode("utf-8"),
    }
    return {rel: base64.b64encode(data).decode("ascii") for rel, data in raw.items()}


def _install_command(token: str, port: int) -> str:
    blob = base64.b64encode(
        json.dumps({"files": _remote_files(token, port), "port": int(port)}).encode(
            "utf-8"
        )
    ).decode("ascii")
    return (
        f"# grip rendezvous listen {int(port)}\n"
        "if command -v python3 >/dev/null 2>&1; then PY=python3; "
        "elif command -v python >/dev/null 2>&1; then PY=python; "
        "else echo python_not_found; exit 1; fi\n"
        f"export GRIP_RV_BLOB='{blob}'\n"
        '"$PY" - <<\'PY\'\n' + _BOOT_PY + "\nPY\n"
    )


def _say(progress: Any, text: str) -> None:
    if progress is not None:
        progress(text)


def _relay_proven(resources: ResourceStore, name: str) -> bool:
    resource = resources.get(name)
    tun = (resource.meta if resource else {}) or {}
    tun = tun.get("tunnel") if isinstance(tun, dict) else None
    return isinstance(tun, dict) and tun.get("relay_proven") is True


def setup_rendezvous(
    resources: ResourceStore,
    vault: Vault,
    name: str,
    *,
    store: TunnelStore | None = None,
    port: int = RENDEZVOUS_PORT,
    reenroll: bool = False,
    progress: Any = None,
) -> dict[str, Any]:
    """SSH-install rendezvous on one public host, then enroll private-IP devices."""
    store = store or TunnelStore()
    resource = resources.get(name)
    if not resource or resource.kind != "ssh":
        return {"ok": False, "error": "not_found", "hint": "找不到这台设备。"}
    meta = resource.meta or {}
    host = _host_of(meta)
    if is_lan_host(host):
        return {
            "ok": False,
            "error": "not_public_host",
            "hint": "这台是局域网地址，不能当汇合点。请改选有公网地址的机器。",
        }
    if not _posix(meta):
        return {
            "ok": False,
            "error": "rendezvous_needs_posix",
            "hint": "汇合点需要 Linux 或 macOS。请改选一台这样的公网机器。",
        }
    _say(progress, f"正在从这台电脑确认「{name}」（{host}）的 443…")
    settings = store.settings()
    token = str(settings.get("rendezvous_token") or "").strip() or secrets.token_urlsafe(24)
    url = f"http://{host}:{int(port)}"
    https_url = f"https://{host}:{int(port)}"
    same_host = str(settings.get("rendezvous_host") or "") == name and bool(
        str(settings.get("rendezvous_token") or "").strip()
    )
    installed = bool(settings.get("rendezvous_installed"))
    stored_url = str(settings.get("rendezvous_url") or "").strip()
    # An older :7700 install must be replaced. An empty URL means the last
    # probe failed and should not reinstall on every retry.
    skip_install = same_host and installed and stored_url in {url, https_url, ""}

    def deploy() -> dict[str, Any] | None:
        store.set_settings({"rendezvous_token": token, "rendezvous_host": name})
        result = run_ssh(
            resource,
            vault,
            _install_command(token, port),
            timeout_sec=90,
            os_wrap=True,
        )
        text = (result.stdout or "") + "\n" + (result.stderr or "")
        if not result.ok or "GRIP_RENDEZVOUS_OK" not in text:
            hint = "SSH 没能在这台机器上启动汇合点。"
            if "python_not_found" in text:
                hint = "这台机器上没有 python3，装上之后再试。"
            else:
                hint += "请检查 SSH 是否允许执行命令。"
            return {
                "ok": False,
                "error": "deploy_failed",
                "host_name": name,
                "detail": text[-800:],
                "hint": hint,
            }
        store.set_settings(
            {
                "rendezvous_token": token,
                "rendezvous_host": name,
                "rendezvous_installed": True,
            }
        )
        return None

    if not skip_install:
        failed_deploy = deploy()
        if failed_deploy:
            return failed_deploy
    gaps = probe_rendezvous_ports(host, int(port))
    restarted = False
    if skip_install and any(
        int(item.get("port") or 0) == int(port)
        and item.get("reason") in {"refused", "foreign"}
        for item in gaps
    ):
        failed_deploy = deploy()
        if failed_deploy:
            return failed_deploy
        restarted = True
        gaps = probe_rendezvous_ports(host, int(port))
    reach_down = any(int(item.get("port") or 0) == int(port) for item in gaps)
    if reach_down:
        guide = [
            {"port": int(port), "proto": "TCP", "role": "汇合", "reason": "closed"},
        ]
        for item in gaps:
            if int(item.get("port") or 0) == int(port) and item.get("reason"):
                guide[0]["reason"] = str(item.get("reason"))
        store.set_settings(
            {
                "rendezvous_token": token,
                "rendezvous_host": name,
                "rendezvous_installed": True,
                "rendezvous_url": "",
                "rendezvous_port_gaps": guide,
                "rendezvous_prompt_dismissed": False,
            }
        )
        return {
            "ok": False,
            "error": (
                "rendezvous_not_listening"
                if guide[0].get("reason") == "refused"
                else "rendezvous_port_closed"
            ),
            "host": host,
            "host_name": name,
            "ports": [int(port)],
            "port_gaps": guide,
            "hint": port_gap_hint(guide, port=int(port), host_name=name, host=host),
        }
    picked = choose_base_url(host, int(port))
    if picked:
        url = picked
    store.set_settings(
        {
            "rendezvous_url": url,
            "rendezvous_token": token,
            "rendezvous_host": name,
            "rendezvous_installed": True,
            "rendezvous_port_gaps": gaps,
            "rendezvous_prompt_dismissed": False,
        }
    )
    enrolled: list[str] = []
    failed: list[dict[str, Any]] = []
    plan_now = rendezvous_plan(resources, store=store)
    targets = plan_now["lan_devices"] if reenroll else plan_now["pending_lan"]
    for lan_name in targets:
        _say(progress, f"正在把隧道程序传到 {lan_name}…")
        try:
            out = enroll_resource(
                resources,
                vault,
                lan_name,
                store=store,
                rendezvous_url=url,
            )
        except Exception as exc:  # noqa: BLE001
            out = {"ok": False, "name": lan_name, "error": "enroll_failed", "detail": str(exc)}
        if out.get("ok"):
            enrolled.append(lan_name)
        else:
            detail = str(out.get("detail") or out.get("error") or "")
            hint = people_enroll_hint(lan_name, detail)
            failed.append(
                {
                    "name": lan_name,
                    "error": out.get("error") or "enroll_failed",
                    "detail": detail[-400:],
                    "hint": hint,
                }
            )
    if gaps:
        hint = port_gap_hint(gaps, port=int(port), host_name=name, host=host)
        extra = " ".join(str(item.get("hint") or "") for item in failed if item.get("hint"))
        if extra:
            hint = f"{hint} {extra}"
        return {
            "ok": False,
            "error": "rendezvous_ports_incomplete",
            "url": url,
            "host_name": name,
            "ports": [int(item["port"]) for item in gaps],
            "port_gaps": gaps,
            "enrolled": enrolled,
            "failed": failed,
            "hint": hint,
        }
    if failed:
        hints = [str(item.get("hint") or "") for item in failed if item.get("hint")]
        return {
            "ok": False,
            "error": "enroll_incomplete",
            "url": url,
            "host_name": name,
            "enrolled": enrolled,
            "failed": failed,
            "hint": " ".join(hints) if hints else "下一步：点重试，再把隧道程序传到局域网设备。",
        }
    store.set_settings({"rendezvous_port_gaps": []})
    unproven = [
        lan_name
        for lan_name in enrolled
        if not _relay_proven(resources, lan_name)
    ]
    if unproven:
        hint = "完成。这些设备还没从汇合点证实能连：" + "、".join(unproven)
    else:
        hint = "完成。在家走局域网，离开后自动改走隧道。"
    return {
        "ok": True,
        "url": url,
        "host_name": name,
        "enrolled": enrolled,
        "reused": skip_install and not restarted,
        "hint": hint,
    }


_job_lock = __import__("threading").Lock()
_job: dict[str, Any] = {"running": False, "hint": "", "result": None}


def tunnel_job_view() -> dict[str, Any]:
    with _job_lock:
        return {
            "running": bool(_job["running"]),
            "hint": str(_job["hint"] or ""),
            "result": _job["result"],
        }


def start_tunnel_job(
    resources: ResourceStore,
    vault: Vault,
    name: str,
    *,
    reenroll: bool = False,
) -> dict[str, Any]:
    """Run setup off the panel request so a slow copy is not shown as Load failed."""
    import threading

    with _job_lock:
        if _job["running"]:
            return {"ok": True, "running": True, "hint": str(_job["hint"] or "")}
        _job["running"] = True
        _job["hint"] = "正在从这台电脑确认汇合点端口…"
        _job["result"] = None

    def run() -> None:
        def progress(text: str) -> None:
            with _job_lock:
                _job["hint"] = text

        try:
            result = setup_rendezvous(
                resources, vault, name, reenroll=reenroll, progress=progress
            )
        except Exception as exc:  # noqa: BLE001
            result = {
                "ok": False,
                "host_name": name,
                "error": "setup_failed",
                "hint": people_enroll_hint(name, str(exc)),
            }
        with _job_lock:
            _job["running"] = False
            _job["result"] = result
            _job["hint"] = str(result.get("hint") or "")

    threading.Thread(target=run, daemon=True).start()
    with _job_lock:
        return {"ok": True, "running": True, "hint": str(_job["hint"] or "")}
