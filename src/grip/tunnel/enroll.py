from __future__ import annotations

"""Enroll a LAN SSH device: install + start per-device Node via SSH."""

import base64
import gzip
import json
import shlex
import textwrap
from typing import Any

from grip.resources import ResourceStore
from grip.ssh_exec import run_ssh
from grip.tunnel.node import new_node_config
from grip.tunnel.ops import set_rendezvous_url
from grip.tunnel.store import TunnelStore
from grip.vault import Vault

# Minimal remote runner (stdlib only) — kept in sync with node relay protocol.
_REMOTE_NODE_PY = r'''#!/usr/bin/env python3
"""Grip tunnel node (stdlib). Bridges rendezvous relay → local SSH."""
from __future__ import annotations
import json, os, select, socket, ssl, threading, time, urllib.request
from pathlib import Path

CFG = Path.home() / ".grip-node" / "config.json"

def load():
    return json.loads(CFG.read_text(encoding="utf-8"))

def http_json(method, url, payload=None, timeout=8):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    ctx = ssl._create_unverified_context() if str(url).startswith("https://") else None
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return json.loads(resp.read().decode("utf-8"))

def relay_host_port(base):
    from urllib.parse import urlparse
    p = urlparse(base)
    host = p.hostname or "127.0.0.1"
    port = int(p.port or (443 if p.scheme == "https" else 80))
    return host, port

def pipe(a, b):
    sockets = [a, b]
    try:
        while True:
            r, _, _ = select.select(sockets, [], [], 300)
            if not r:
                break
            for s in r:
                other = b if s is a else a
                data = s.recv(65536)
                if not data:
                    return
                other.sendall(data)
    finally:
        for s in (a, b):
            try: s.close()
            except OSError: pass

def dial_relay(base, session_id, lan_host, lan_port, token=""):
    rh, rp = relay_host_port(base)
    remote = socket.create_connection((rh, rp), timeout=15)
    if str(base).startswith("https://"):
        remote = ssl._create_unverified_context().wrap_socket(remote, server_hostname=rh)
    lines = [
        "POST /v1/relay HTTP/1.1",
        "Host: " + rh,
        "Connection: Upgrade",
        "Upgrade: grip-relay",
        "X-Grip-Session: " + session_id,
        "X-Grip-Role: gateway",
    ]
    if token:
        lines.append("X-Grip-Token: " + token)
    crlf = chr(13) + chr(10)
    remote.sendall((crlf.join(lines) + crlf + crlf).encode("ascii"))
    buf = b""
    remote.settimeout(15)
    blank = crlf.encode("ascii")
    while (blank + blank) not in buf and len(buf) < 4096:
        chunk = remote.recv(256)
        if not chunk:
            break
        buf += chunk
    if b" 101 " not in buf.split(blank, 1)[0]:
        remote.close()
        return
    remote.settimeout(None)
    lan = socket.create_connection((lan_host, int(lan_port)), timeout=15)
    pipe(remote, lan)

def main():
    cfg = load()
    url = cfg["rendezvous_url"].rstrip("/")
    node_id = cfg["node_id"]
    resource = cfg["resource_name"]
    token = str(cfg.get("rendezvous_token") or "")
    ssh_host = cfg.get("ssh_host") or "127.0.0.1"
    ssh_port = int(cfg.get("ssh_port") or 22)
    seen = set()
    last = 0.0
    def http_json_auth(method, path, payload=None, timeout=8):
        data = None
        headers = {}
        if token:
            headers["Authorization"] = "Bearer " + token
            headers["X-Grip-Token"] = token
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url + path, data=data, headers=headers, method=method)
        ctx = ssl._create_unverified_context() if str(url).startswith("https://") else None
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return json.loads(resp.read().decode("utf-8"))
    def register():
        payload = {
            "node_id": node_id,
            "public_key": cfg.get("public_key"),
            "role": "node",
            "tunnel_ip": cfg.get("tunnel_ip"),
            "endpoint": cfg.get("endpoint"),
            "devices": {resource: {
                "ssh_host": ssh_host, "ssh_port": ssh_port, "online": True,
                "tunnel_ip": cfg.get("tunnel_ip"),
            }},
        }
        resp = http_json_auth("POST", "/v1/register", payload)
        sug = (resp or {}).get("suggested_endpoint") if isinstance(resp, dict) else None
        if sug and not cfg.get("endpoint"):
            cfg["endpoint"] = sug
            try:
                CFG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
            except Exception:
                pass
        return resp
    def try_wg():
        tip = cfg.get("tunnel_ip")
        priv = cfg.get("private_key")
        if not tip or not priv:
            return
        try:
            import subprocess, sys
            peers_raw = http_json_auth("GET", "/v1/peers").get("peers") or {}
            peer_lines = []
            for p in peers_raw.values():
                if not isinstance(p, dict):
                    continue
                if str(p.get("role") or "") not in ("client", "operator", "gateway"):
                    continue
                pub = p.get("public_key"); pip = p.get("tunnel_ip")
                if not pub or not pip:
                    continue
                peer_lines.append("[Peer]")
                peer_lines.append(f"PublicKey = {pub}")
                peer_lines.append(f"AllowedIPs = {pip}/32")
                if p.get("endpoint"):
                    peer_lines.append(f"Endpoint = {p['endpoint']}")
                peer_lines.append("PersistentKeepalive = 25")
            if not peer_lines:
                return
            listen = int(cfg.get("wg_listen_port") or 51820)
            conf = "\n".join([
                "[Interface]", f"PrivateKey = {priv}", f"Address = {tip}/32",
                f"ListenPort = {listen}", "",
            ] + peer_lines) + "\n"
            root = Path.home() / ".grip-node"
            wdir = root / "wg"
            wdir.mkdir(parents=True, exist_ok=True)
            cpath = wdir / "grip0.conf"
            cpath.write_text(conf, encoding="utf-8")
            boot = root / "remote_wg.py"
            if not boot.is_file():
                return
            subprocess.run(
                [sys.executable, str(boot), str(cpath)],
                capture_output=True,
                timeout=30,
            )
        except Exception:
            return
    # First contact often fails (firewall, rendezvous still coming up).
    # Stay alive and retry from the loop; do not exit the process.
    try:
        register()
    except Exception:
        pass
    last = time.time()
    last_wg = 0.0
    try:
        try_wg()
    except Exception:
        pass
    while True:
        if time.time() - last > 15:
            try: register()
            except Exception: pass
            last = time.time()
        if time.time() - last_wg > 30:
            try_wg()
            last_wg = time.time()
        try:
            peers = http_json_auth("GET", "/v1/peers").get("peers") or {}
            for peer in peers.values():
                if not isinstance(peer, dict):
                    continue
                devices = peer.get("devices") or {}
                info = devices.get(resource)
                if not isinstance(info, dict):
                    continue
                sid = str(info.get("session_id") or "").strip()
                if not sid or sid in seen:
                    continue
                seen.add(sid)
                def serve(_sid=sid):
                    try:
                        dial_relay(url, _sid, ssh_host, ssh_port, token)
                    except OSError:
                        pass
                    finally:
                        seen.discard(_sid)
                threading.Thread(target=serve, daemon=True).start()
        except Exception:
            pass
        time.sleep(1.0)

if __name__ == "__main__":
    main()
'''


# One SSH argv must stay small. Windows EncodedCommand budget is ~7500 chars
# (~1.8k of script). POSIX can take a larger chunk, still far under ARG_MAX,
# so a multi-megabyte wireguard-go binary is uploaded in pieces.
_POSIX_CHUNK = 100_000
_WIN_CHUNK = 1600


def _b64_chunks(data: bytes, size: int) -> list[str]:
    blob = base64.b64encode(data).decode("ascii")
    return [blob[i : i + size] for i in range(0, len(blob), size)] or [""]


def _posix_stage_commands(filename: str, data: bytes) -> list[str]:
    cmds: list[str] = []
    for i, part in enumerate(_b64_chunks(data, _POSIX_CHUNK)):
        op = ">" if i == 0 else ">>"
        cmds.append(
            "mkdir -p \"$HOME/.grip-node\" && "
            f"printf '%s' '{part}' {op} \"$HOME/.grip-node/{filename}\""
        )
    return cmds


def _windows_stage_commands(filename: str, data: bytes) -> list[str]:
    cmds: list[str] = []
    for i, part in enumerate(_b64_chunks(data, _WIN_CHUNK)):
        write = "WriteAllText" if i == 0 else "AppendAllText"
        cmds.append(
            "$d = Join-Path $env:USERPROFILE '.grip-node'; "
            "New-Item -ItemType Directory -Force -Path $d | Out-Null; "
            f"[IO.File]::{write}((Join-Path $d '{filename}'), '{part}')"
        )
    return cmds


def _posix_finish_script(node_id: str) -> str:
    return textwrap.dedent(
        f"""
        set -e
        DIR="$HOME/.grip-node"
        python3 - <<'PY'
        import base64, gzip, pathlib
        root = pathlib.Path.home() / ".grip-node"
        def take(name):
            p = root / name
            if not p.is_file():
                return None
            data = base64.b64decode(p.read_text(encoding="ascii"))
            p.unlink()
            return data
        node = take("node.py.b64")
        cfg = take("config.json.b64")
        if not node or not cfg:
            raise SystemExit("staged_files_missing")
        (root / "node.py").write_bytes(node)
        (root / "node.py").chmod(0o700)
        (root / "config.json").write_bytes(cfg)
        (root / "config.json").chmod(0o600)
        wgpy = take("remote_wg.py.b64")
        if wgpy:
            (root / "remote_wg.py").write_bytes(wgpy)
            (root / "remote_wg.py").chmod(0o700)
        blob = take("wg.gz.b64")
        if blob:
            bindir = root / "bin"
            bindir.mkdir(parents=True, exist_ok=True)
            (bindir / "wireguard-go").write_bytes(gzip.decompress(blob))
            (bindir / "wireguard-go").chmod(0o755)
        PY
        if [ -x "$DIR/bin/wireguard-go" ]; then
          setcap cap_net_admin,cap_net_bind_service+ep "$DIR/bin/wireguard-go" 2>/dev/null \\
            || sudo -n setcap cap_net_admin,cap_net_bind_service+ep "$DIR/bin/wireguard-go" 2>/dev/null \\
            || true
        fi
        if [ -f "$DIR/node.pid" ]; then
          kill "$(cat "$DIR/node.pid")" 2>/dev/null || true
          rm -f "$DIR/node.pid"
        fi
        nohup python3 "$DIR/node.py" >"$DIR/node.log" 2>&1 &
        echo $! >"$DIR/node.pid"
        sleep 0.4
        kill -0 "$(cat "$DIR/node.pid")"
        echo GRIP_NODE_OK node_id={shlex.quote(str(node_id))}
        """
    ).strip()


def _windows_finish_script(node_id: str) -> str:
    safe = str(node_id).replace("'", "")
    # Password SSH retries a channel drop by running this script again. The
    # first run already consumed the staged files and started the process,
    # so a second run must report success instead of staged_files_missing.
    return (
        "$ErrorActionPreference = 'Stop'; "
        "$d = Join-Path $env:USERPROFILE '.grip-node'; "
        "function Take([string]$n){ "
        "$p = Join-Path $d $n; if (-not (Test-Path $p)) { return $null }; "
        "$t = [IO.File]::ReadAllText($p); Remove-Item $p -Force; "
        "return [Convert]::FromBase64String($t) }; "
        "$node = Take 'node.py.b64'; $cfg = Take 'config.json.b64'; "
        "if (-not $node -or -not $cfg) { "
        "$pidPath = Join-Path $d 'node.pid'; $cfgPath = Join-Path $d 'config.json'; "
        "$alive = $false; "
        "if ((Test-Path $pidPath) -and (Test-Path $cfgPath)) { "
        "$procId = (Get-Content $pidPath | Out-String).Trim(); "
        "if ($procId) { $have = Get-Content $cfgPath -Raw | ConvertFrom-Json; "
        f"if ($have.node_id -eq '{safe}' -and (Get-Process -Id $procId -ErrorAction SilentlyContinue)) "
        "{ $alive = $true } } }; "
        f"if ($alive) {{ Write-Output 'GRIP_NODE_OK node_id={safe}'; exit 0 }}; "
        "throw 'staged_files_missing' }; "
        "[IO.File]::WriteAllBytes((Join-Path $d 'node.py'), $node); "
        "[IO.File]::WriteAllBytes((Join-Path $d 'config.json'), $cfg); "
        "$py = (Get-Command python -ErrorAction SilentlyContinue).Source; "
        "if (-not $py) { $py = (Get-Command python3 -ErrorAction SilentlyContinue).Source }; "
        "if (-not $py) { throw 'python_not_found' }; "
        "$proc = Start-Process -FilePath $py -ArgumentList (Join-Path $d 'node.py') "
        "-WindowStyle Hidden -PassThru "
        "-RedirectStandardOutput (Join-Path $d 'node.log') "
        "-RedirectStandardError (Join-Path $d 'node.err'); "
        "Set-Content -Path (Join-Path $d 'node.pid') -Value $proc.Id; "
        f"Write-Output 'GRIP_NODE_OK node_id={safe}'"
    )


def enroll_push_commands(
    cfg: dict[str, Any],
    *,
    os_name: str,
    remote_wg: bytes = b"",
    wg_gz: bytes = b"",
) -> list[str]:
    """SSH commands that stage Node files in small pieces, then start it."""
    node = _REMOTE_NODE_PY.encode("utf-8")
    config = json.dumps(cfg, ensure_ascii=False).encode("utf-8")
    windows = os_name in {"windows", "win", "win32"}
    stage = _windows_stage_commands if windows else _posix_stage_commands
    cmds = stage("node.py.b64", node) + stage("config.json.b64", config)
    if remote_wg and not windows:
        cmds += stage("remote_wg.py.b64", remote_wg)
    if wg_gz and not windows:
        cmds += stage("wg.gz.b64", wg_gz)
    finish = (
        _windows_finish_script(str(cfg.get("node_id") or ""))
        if windows
        else _posix_finish_script(str(cfg.get("node_id") or ""))
    )
    cmds.append(finish)
    return cmds


def _remote_platform_tag(resources: ResourceStore, vault: Vault, name: str) -> str | None:
    """Best-effort uname → wireguard-go tag (linux-amd64 / linux-arm64 / …)."""
    r = resources.get(name)
    if not r:
        return None
    try:
        result = run_ssh(
            r,
            vault,
            "uname -s; uname -m",
            timeout_sec=20,
            os_wrap=True,
        )
        out = (result.stdout or "").strip().splitlines()
        if len(out) < 2:
            return None
        sys_name = out[0].strip().lower()
        mach = out[1].strip().lower()
        from grip.tunnel.wgembed import platform_tag

        return platform_tag(sys_name, mach)
    except Exception:
        # Default common server arch when probe fails.
        return "linux-amd64"


def enroll_resource(
    resources: ResourceStore,
    vault: Vault,
    name: str,
    *,
    rendezvous_url: str | None = None,
    store: TunnelStore | None = None,
    timeout_sec: int = 60,
) -> dict[str, Any]:
    """SSH to LAN device, install stdlib Node, mark tunnel enabled on resource."""
    store = store or TunnelStore()
    r = resources.get(name)
    if not r or r.kind != "ssh":
        raise KeyError(name)

    settings = store.settings()
    url = (rendezvous_url or settings.get("rendezvous_url") or "").strip()
    if not url:
        raise ValueError("rendezvous_url_required")
    store.set_settings({"rendezvous_url": url})

    lan_host = str(r.meta.get("lan_host") or r.meta.get("host") or "")
    lan_port = int(r.meta.get("port") or 22)
    tunnel_ip = store.allocate_device_tunnel_ip(name)
    cfg = new_node_config(
        rendezvous_url=url,
        resource_name=name,
        ssh_port=lan_port,
    )
    # Node listens on localhost for SSH once relay arrives;
    # tunnel_ip is the WireGuard address for direct path.
    cfg["ssh_host"] = "127.0.0.1"
    cfg["ssh_port"] = lan_port
    cfg["tunnel_ip"] = tunnel_ip
    cfg["wg_listen_port"] = 51820
    tok = str(settings.get("rendezvous_token") or "").strip()
    if tok:
        cfg["rendezvous_token"] = tok

    remote_wg = b""
    wg_gz = b""
    os_name = str(r.meta.get("os") or "unix").lower()
    try:
        from pathlib import Path as _Path

        from grip.tunnel.wgembed import bundled_binary_path

        if os_name not in {"windows", "win", "win32"}:
            remote_wg = (_Path(__file__).resolve().parent / "remote_wg.py").read_bytes()
            tag = _remote_platform_tag(resources, vault, name)
            bin_path = bundled_binary_path(tag) if tag else None
            if bin_path is not None:
                wg_gz = gzip.compress(bin_path.read_bytes(), compresslevel=9)
    except Exception:
        remote_wg = b""
        wg_gz = b""

    commands = enroll_push_commands(
        cfg, os_name=os_name, remote_wg=remote_wg, wg_gz=wg_gz
    )
    result = None
    for remote in commands:
        try:
            result = run_ssh(r, vault, remote, timeout_sec=timeout_sec, os_wrap=True)
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "error": "enroll_failed",
                "detail": str(exc)[:1500],
                "exit_code": None,
            }
        if not result.ok:
            break
    text = ((result.stderr if result else "") or "") + "\n" + (
        (result.stdout if result else "") or ""
    )
    ok = bool(result and result.ok) and "GRIP_NODE_OK" in text
    if not ok:
        return {
            "ok": False,
            "error": "enroll_failed",
            "detail": text[-1500:],
            "exit_code": result.exit_code if result else None,
        }

    resources.patch_meta(
        name,
        {
            "lan_host": lan_host,
            "tunnel": {
                "enabled": True,
                "node_id": cfg["node_id"],
                "public_key": cfg["public_key"],
                "rendezvous_url": url,
                "tunnel_host": tunnel_ip,
                "tunnel_ip": tunnel_ip,
                "tunnel_port": lan_port,
                "path": "lan",
                "model": "node",
            },
        },
    )
    m = store.device_map()
    ent = dict(m.get(name) or {})
    ent.update(
        {
            "lan_host": lan_host,
            "lan_port": lan_port,
            "tunnel_ip": tunnel_ip,
            "public_key": cfg["public_key"],
            "node_id": cfg["node_id"],
        }
    )
    m[name] = ent
    store.save_device_map(m)
    # Remember local operator rendezvous
    set_rendezvous_url(url, store)
    # The tag waits for an SSH banner through the rendezvous, not for enroll.
    try:
        from grip.tunnel.ops import confirm_off_lan

        saved = resources.get(name)
        confirm_off_lan(
            name,
            (saved.meta if saved else {}) or {},
            store=store,
            resources=resources,
        )
    except Exception:
        pass
    # Best-effort: build operator WG conf now (direct preferred later off-LAN).
    try:
        from grip.tunnel.direct import try_wg_direct

        try_wg_direct(
            name,
            resources.get(name).meta if resources.get(name) else {},
            store=store,
            resources=resources,
        )
    except Exception:
        pass
    return {
        "ok": True,
        "name": name,
        "node_id": cfg["node_id"],
        "tunnel_ip": tunnel_ip,
        "rendezvous_url": url,
        "model": "node",
    }
