from __future__ import annotations

"""WireGuard UAPI helpers — configure userspace wireguard-go without wg-quick.

Key format on the UAPI socket is hex (not base64). Conf files we generate are
wg-quick style (base64); this module converts.
"""

import base64
import re
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_UAPI_DIR = Path("/tmp/grip-wireguard")


@dataclass
class WgPeer:
    public_key_b64: str
    allowed_ips: list[str] = field(default_factory=list)
    endpoint: str | None = None
    keepalive: int | None = None


@dataclass
class WgConf:
    private_key_b64: str
    address: str | None = None  # e.g. 10.77.0.1/32
    listen_port: int | None = None
    peers: list[WgPeer] = field(default_factory=list)


def b64_to_hex(key_b64: str) -> str:
    raw = base64.b64decode(key_b64.strip())
    if len(raw) != 32:
        raise ValueError("wireguard_key_must_be_32_bytes")
    return raw.hex()


def parse_wg_quick_conf(text: str) -> WgConf:
    """Parse a minimal wg-quick conf (Interface + Peer sections)."""
    section = ""
    priv = ""
    address = None
    listen_port = None
    peers: list[WgPeer] = []
    cur: dict[str, Any] | None = None

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            if cur is not None and section == "peer":
                peers.append(
                    WgPeer(
                        public_key_b64=str(cur["public_key"]),
                        allowed_ips=list(cur.get("allowed_ips") or []),
                        endpoint=cur.get("endpoint"),
                        keepalive=cur.get("keepalive"),
                    )
                )
            name = line[1:-1].strip().lower()
            section = "interface" if name == "interface" else "peer"
            cur = {} if section == "peer" else None
            continue
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip().lower()
        val = val.strip()
        if section == "interface":
            if key == "privatekey":
                priv = val
            elif key == "address":
                address = val.split(",")[0].strip()
            elif key == "listenport":
                listen_port = int(val)
        elif section == "peer" and cur is not None:
            if key == "publickey":
                cur["public_key"] = val
            elif key == "allowedips":
                cur["allowed_ips"] = [p.strip() for p in val.split(",") if p.strip()]
            elif key == "endpoint":
                cur["endpoint"] = val
            elif key == "persistentkeepalive":
                cur["keepalive"] = int(val)

    if cur is not None and section == "peer" and cur.get("public_key"):
        peers.append(
            WgPeer(
                public_key_b64=str(cur["public_key"]),
                allowed_ips=list(cur.get("allowed_ips") or []),
                endpoint=cur.get("endpoint"),
                keepalive=cur.get("keepalive"),
            )
        )

    if not priv:
        raise ValueError("wg_conf_missing_private_key")
    return WgConf(
        private_key_b64=priv,
        address=address,
        listen_port=listen_port,
        peers=peers,
    )


def conf_to_uapi(conf: WgConf) -> str:
    """Build a UAPI set operation body (hex keys)."""
    lines = [
        f"private_key={b64_to_hex(conf.private_key_b64)}",
        "replace_peers=true",
    ]
    if conf.listen_port is not None:
        lines.append(f"listen_port={int(conf.listen_port)}")
    for peer in conf.peers:
        lines.append(f"public_key={b64_to_hex(peer.public_key_b64)}")
        if peer.endpoint:
            # endpoint is host:port — UAPI wants that string
            lines.append(f"endpoint={peer.endpoint}")
        if peer.keepalive is not None:
            lines.append(f"persistent_keepalive_interval={int(peer.keepalive)}")
        lines.append("replace_allowed_ips=true")
        for ip in peer.allowed_ips:
            lines.append(f"allowed_ip={ip}")
    lines.append("")
    return "\n".join(lines)


def uapi_sock_path(iface: str, uapi_dir: Path | None = None) -> Path:
    root = uapi_dir or DEFAULT_UAPI_DIR
    return root / f"{iface}.sock"


def apply_uapi(
    iface: str,
    conf: WgConf,
    *,
    uapi_dir: Path | None = None,
    timeout_sec: float = 5.0,
) -> None:
    path = uapi_sock_path(iface, uapi_dir)
    body = conf_to_uapi(conf)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout_sec)
    try:
        sock.connect(str(path))
        sock.sendall(body.encode("utf-8"))
        # Read until EOF / err= line
        chunks: list[bytes] = []
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
        if re.search(r"(?m)^errno=", resp) and not re.search(r"(?m)^errno=0\s*$", resp):
            raise RuntimeError(f"uapi_error:{resp.strip()[:300]}")
    finally:
        try:
            sock.close()
        except OSError:
            pass
