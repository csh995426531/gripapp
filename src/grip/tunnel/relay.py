from __future__ import annotations

"""P1: client/gateway TCP relay via self-hosted rendezvous.

When WireGuard direct path is down, both sides dial the rendezvous relay port
and Grip SSH talks to a local listener that is piped through.
"""

import secrets
import socket
import ssl
import threading
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from grip.tunnel.rendezvous import RendezvousClient


@dataclass
class LocalRelay:
    session_id: str
    listen_host: str
    listen_port: int
    thread: threading.Thread
    stop: threading.Event


def _relay_host_port(rendezvous_url: str) -> tuple[str, int]:
    """Relay shares the rendezvous HTTP port (443), not a second port."""
    parsed = urlparse(rendezvous_url)
    host = parsed.hostname or "127.0.0.1"
    http_port = int(parsed.port or (443 if parsed.scheme == "https" else 80))
    return host, http_port


def _dial_relay_upgrade(
    host: str,
    port: int,
    session_id: str,
    role: str,
    token: str = "",
    *,
    tls: bool = False,
) -> socket.socket:
    """Open the relay on the same port as HTTPS, so no extra firewall rule."""
    remote = socket.create_connection((host, int(port)), timeout=15)
    if tls:
        remote = ssl._create_unverified_context().wrap_socket(
            remote, server_hostname=host
        )
    lines = [
        "POST /v1/relay HTTP/1.1",
        f"Host: {host}",
        "Connection: Upgrade",
        "Upgrade: grip-relay",
        f"X-Grip-Session: {session_id}",
        f"X-Grip-Role: {role}",
    ]
    if token:
        lines.append(f"X-Grip-Token: {token}")
    remote.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("ascii"))
    remote.settimeout(15)
    buf = b""
    while b"\r\n\r\n" not in buf and len(buf) < 4096:
        chunk = remote.recv(256)
        if not chunk:
            break
        buf += chunk
    status = buf.split(b"\r\n", 1)[0]
    if b" 101 " not in status:
        try:
            remote.close()
        except OSError:
            pass
        raise OSError("relay_upgrade_failed")
    remote.settimeout(None)
    return remote


def open_local_relay_to_rendezvous(
    rendezvous_url: str,
    *,
    session_id: str | None = None,
    role: str = "client",
    resource: str = "",
    gateway_id: str = "",
    bind_host: str = "127.0.0.1",
    token: str | None = None,
) -> LocalRelay:
    """Listen locally; each accept is piped to rendezvous RELAY session."""
    sid = session_id or secrets.token_hex(8)
    client = RendezvousClient(rendezvous_url, token=token)
    client.open_relay_session(sid, resource=resource, gateway_id=gateway_id)
    rh, rp = _relay_host_port(rendezvous_url)
    tok = (token or "").strip()
    use_tls = urlparse(rendezvous_url).scheme == "https"

    stop = threading.Event()
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((bind_host, 0))
    srv.listen(5)
    listen_port = int(srv.getsockname()[1])

    def _serve() -> None:
        srv.settimeout(0.5)
        while not stop.is_set():
            try:
                local, _ = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(
                target=_bridge_local_to_relay,
                args=(local, rh, rp, sid, role, tok, use_tls),
                daemon=True,
            ).start()
        try:
            srv.close()
        except OSError:
            pass

    th = threading.Thread(target=_serve, daemon=True)
    th.start()
    return LocalRelay(
        session_id=sid,
        listen_host=bind_host,
        listen_port=listen_port,
        thread=th,
        stop=stop,
    )


def gateway_dial_relay(
    rendezvous_url: str,
    *,
    session_id: str,
    lan_host: str,
    lan_port: int,
    role: str = "gateway",
    token: str | None = None,
) -> None:
    """Gateway side: connect to rendezvous and forward to LAN SSH."""
    rh, rp = _relay_host_port(rendezvous_url)
    remote = _dial_relay_upgrade(
        rh,
        rp,
        session_id,
        role,
        (token or "").strip(),
        tls=urlparse(rendezvous_url).scheme == "https",
    )
    lan = socket.create_connection((lan_host, int(lan_port)), timeout=15)
    _pipe(remote, lan)


def gateway_accept_sessions_loop(
    rendezvous_url: str,
    *,
    device_map: dict[str, Any],
    gateway_id: str,
    stop: threading.Event | None = None,
    poll_sec: float = 2.0,
) -> None:
    """Poll rendezvous peers is not enough; clients open sessions.

    Gateway listens for outbound client sessions by registering a well-known
    pattern: client uses session_id = f\"{gateway_id}:{resource}:{nonce}\".
    For v1, the client includes resource in open_relay and gateway runs a
    long-lived acceptor thread per resource via ``expect_resource_relay``.
    """
    # Placeholder loop — concrete accept is driven by expect_resource_relay.
    stop = stop or threading.Event()
    while not stop.wait(poll_sec):
        if stop.is_set():
            break


def expect_resource_relay(
    rendezvous_url: str,
    *,
    session_id: str,
    lan_host: str,
    lan_port: int,
    stop: threading.Event | None = None,
) -> threading.Thread:
    stop = stop or threading.Event()

    def _run() -> None:
        while not stop.is_set():
            try:
                gateway_dial_relay(
                    rendezvous_url,
                    session_id=session_id,
                    lan_host=lan_host,
                    lan_port=lan_port,
                )
            except OSError:
                if stop.wait(1.0):
                    break

    th = threading.Thread(target=_run, daemon=True)
    th.start()
    return th


def _bridge_local_to_relay(
    local: socket.socket,
    relay_host: str,
    relay_port: int,
    session_id: str,
    role: str,
    token: str = "",
    tls: bool = False,
) -> None:
    try:
        remote = _dial_relay_upgrade(
            relay_host, relay_port, session_id, role, token, tls=tls
        )
        _pipe(local, remote)
    except OSError:
        try:
            local.close()
        except OSError:
            pass


def _pipe(a: socket.socket, b: socket.socket) -> None:
    import select

    sockets = [a, b]
    try:
        while True:
            r, _w, _x = select.select(sockets, [], [], 300)
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
            try:
                s.close()
            except OSError:
                pass
