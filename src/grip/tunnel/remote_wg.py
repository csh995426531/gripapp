#!/usr/bin/env python3
"""Stdlib-only WireGuard bring-up for enrolled Nodes (embedded wireguard-go).

Usage:
  python3 remote_wg.py /path/to/grip0.conf
Looks for wireguard-go next to this file: ../bin/wireguard-go or ./bin/wireguard-go
"""
from __future__ import annotations

import base64
import os
import platform
import re
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

UAPI_DIR = Path("/tmp/grip-wireguard")


def b64_to_hex(key_b64: str) -> str:
    raw = base64.b64decode(key_b64.strip())
    if len(raw) != 32:
        raise ValueError("bad_key")
    return raw.hex()


def parse_conf(text: str) -> dict:
    section = ""
    priv = ""
    address = None
    listen_port = None
    peers = []
    cur = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            if cur and section == "peer":
                peers.append(cur)
            name = line[1:-1].strip().lower()
            section = "interface" if name == "interface" else "peer"
            cur = {} if section == "peer" else None
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip().lower(), v.strip()
        if section == "interface":
            if k == "privatekey":
                priv = v
            elif k == "address":
                address = v.split(",")[0].strip()
            elif k == "listenport":
                listen_port = int(v)
        elif section == "peer" and cur is not None:
            if k == "publickey":
                cur["public_key"] = v
            elif k == "allowedips":
                cur["allowed_ips"] = [p.strip() for p in v.split(",") if p.strip()]
            elif k == "endpoint":
                cur["endpoint"] = v
            elif k == "persistentkeepalive":
                cur["keepalive"] = int(v)
    if cur and section == "peer" and cur.get("public_key"):
        peers.append(cur)
    return {
        "private_key": priv,
        "address": address,
        "listen_port": listen_port,
        "peers": peers,
    }


def to_uapi(conf: dict) -> str:
    lines = [
        f"private_key={b64_to_hex(conf['private_key'])}",
        "replace_peers=true",
    ]
    if conf.get("listen_port") is not None:
        lines.append(f"listen_port={int(conf['listen_port'])}")
    for peer in conf.get("peers") or []:
        lines.append(f"public_key={b64_to_hex(peer['public_key'])}")
        if peer.get("endpoint"):
            lines.append(f"endpoint={peer['endpoint']}")
        if peer.get("keepalive") is not None:
            lines.append(f"persistent_keepalive_interval={int(peer['keepalive'])}")
        lines.append("replace_allowed_ips=true")
        for ip in peer.get("allowed_ips") or []:
            lines.append(f"allowed_ip={ip}")
    lines.append("")
    return "\n".join(lines)


def apply_uapi(iface: str, body: str) -> None:
    path = UAPI_DIR / f"{iface}.sock"
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(5)
    try:
        sock.connect(str(path))
        sock.sendall(body.encode("utf-8"))
        chunks = []
        while True:
            try:
                data = sock.recv(4096)
            except socket.timeout:
                break
            if not data:
                break
            chunks.append(data)
            if b"\n\n" in b"".join(chunks):
                break
        resp = b"".join(chunks).decode("utf-8", errors="replace")
        if re.search(r"(?m)^errno=", resp) and not re.search(
            r"(?m)^errno=0\s*$", resp
        ):
            raise RuntimeError(resp.strip()[:300])
    finally:
        sock.close()


def find_binary() -> Path:
    here = Path(__file__).resolve().parent
    for cand in (
        here / "bin" / "wireguard-go",
        here.parent / "bin" / "wireguard-go",
        Path.home() / ".grip-node" / "bin" / "wireguard-go",
    ):
        if cand.is_file():
            return cand
    raise FileNotFoundError("wireguard-go_missing")


def start_go(binary: Path, iface: str) -> int:
    UAPI_DIR.mkdir(parents=True, exist_ok=True)
    sock = UAPI_DIR / f"{iface}.sock"
    pid_path = Path.home() / ".grip-node" / "wg.pid"
    if sock.exists() and pid_path.is_file():
        try:
            pid = int(pid_path.read_text().strip())
            os.kill(pid, 0)
            return pid
        except Exception:
            pass
    log = Path.home() / ".grip-node" / "wg.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["WG_PROCESS_FOREGROUND"] = "1"
    with open(log, "ab") as lf:
        proc = subprocess.Popen(
            [str(binary), "-f", iface],
            stdout=lf,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
    pid_path.write_text(str(proc.pid) + "\n")
    deadline = time.time() + 3
    while time.time() < deadline and not sock.exists():
        time.sleep(0.05)
    if not sock.exists():
        raise RuntimeError("uapi_sock_missing")
    return proc.pid


def configure_net(iface: str, conf: dict) -> None:
    addr = (conf.get("address") or "").split("/")[0].strip()
    system = platform.system().lower()

    def run(cmd):
        return subprocess.run(cmd, capture_output=True, text=True)

    if addr:
        if system == "darwin":
            run(["ifconfig", iface, "inet", addr, addr, "alias"])
        else:
            run(["ip", "link", "set", "dev", iface, "up"])
            run(["ip", "address", "replace", f"{addr}/32", "dev", iface])
    for peer in conf.get("peers") or []:
        for allowed in peer.get("allowed_ips") or []:
            host = allowed.split("/")[0]
            if system == "darwin":
                run(["route", "-n", "delete", "-host", host])
                run(["route", "-n", "add", "-host", host, "-interface", iface])
            else:
                run(["ip", "route", "replace", allowed, "dev", iface])


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: remote_wg.py CONF [--apply-only]", file=sys.stderr)
        return 2
    conf_path = Path(argv[1])
    apply_only = "--apply-only" in argv[2:]
    conf = parse_conf(conf_path.read_text(encoding="utf-8"))
    if not conf.get("private_key"):
        print("missing private key", file=sys.stderr)
        return 1
    iface = "utun" if platform.system().lower() == "darwin" else "grip0"
    real = iface
    if not apply_only:
        binary = find_binary()
        start_go(binary, iface if iface != "utun" else "utun")
    if iface == "utun":
        deadline = time.time() + 3
        real = None
        while time.time() < deadline:
            socks = sorted(UAPI_DIR.glob("utun*.sock"))
            if socks:
                real = socks[0].stem
                try:
                    os.chmod(socks[0], 0o666)
                except OSError:
                    pass
                break
            time.sleep(0.05)
        if not real:
            print("utun_missing", file=sys.stderr)
            return 1
    apply_uapi(real, to_uapi(conf))
    configure_net(real, conf)
    print(f"OK iface={real}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except Exception as exc:
        print(f"ERR {exc}", file=sys.stderr)
        raise SystemExit(1)
