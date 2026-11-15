from __future__ import annotations

"""Tiny Grip STUN-like UDP helper for WireGuard endpoint discovery.

Protocol (text, one datagram):
  client → server: ``GRIPSTUN 1\\n``
  server → client: ``GRIPSTUN 1 <ip> <port>\\n``

Rendezvous listens on HTTP port + 2 by convention.
"""

import ipaddress
import os
import socket
import subprocess
import sys
from typing import Any


MAGIC = b"GRIPSTUN 1"


def stun_reply(data: bytes, addr: tuple[str, int]) -> bytes | None:
    if not data.startswith(MAGIC):
        return None
    host, port = addr[0], int(addr[1])
    return f"GRIPSTUN 1 {host} {port}\n".encode("ascii")


def serve_stun_udp(host: str, port: int, *, stop: Any | None = None) -> None:
    """Blocking UDP loop (run in a daemon thread)."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, int(port)))
    srv.settimeout(0.5)
    while True:
        if stop is not None and getattr(stop, "is_set", lambda: False)():
            break
        try:
            data, addr = srv.recvfrom(256)
        except socket.timeout:
            continue
        except OSError:
            break
        reply = stun_reply(data, addr)
        if reply:
            try:
                srv.sendto(reply, addr)
            except OSError:
                pass
    try:
        srv.close()
    except OSError:
        pass


# Clash and other TUN proxies sit on utun and use the benchmarking range.
_PROXY_NETS = (ipaddress.ip_network("198.18.0.0/15"),)
_SKIP_IFACE_PREFIXES = (
    "lo",
    "utun",
    "awdl",
    "llw",
    "bridge",
    "gif",
    "stf",
    "anpi",
    "ap",
    "ipsec",
    "pktap",
    "vmnet",
    "vboxnet",
)


def physical_ipv4_from_ifconfig(text: str) -> list[tuple[str, str]]:
    """IPv4 addresses on real interfaces, skipping tunnels and the proxy range."""
    current = ""
    found: list[tuple[str, str]] = []
    for line in text.splitlines():
        if line and not line[:1].isspace() and ":" in line:
            current = line.split(":", 1)[0].strip()
            continue
        if not current or current.startswith(_SKIP_IFACE_PREFIXES):
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "inet" and ":" not in parts[1]:
            ip = parts[1]
            if _usable_probe_ipv4(ip):
                found.append((current, ip))
    return found


def route_text_is_tunnel(text: str) -> bool:
    """True when ``route -n get`` shows a TUN/proxy path, not a physical NIC."""
    iface = ""
    gateway = ""
    for line in text.splitlines():
        key, sep, val = line.strip().partition(":")
        if not sep:
            continue
        key = key.strip()
        val = val.strip()
        if key == "interface":
            iface = val
        elif key == "gateway":
            gateway = val
    if iface.startswith(("utun", "ipsec")):
        return True
    try:
        gw = ipaddress.ip_address(gateway) if gateway else None
    except ValueError:
        gw = None
    if isinstance(gw, ipaddress.IPv4Address) and any(gw in net for net in _PROXY_NETS):
        return True
    return False


def _usable_probe_ipv4(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if not isinstance(addr, ipaddress.IPv4Address):
        return False
    if addr.is_loopback or addr.is_link_local or addr.is_unspecified or addr.is_multicast:
        return False
    return not any(addr in net for net in _PROXY_NETS)


def _darwin_bin(name: str) -> str:
    path = f"/sbin/{name}"
    return path if os.path.isfile(path) else name


def _route_is_tunnel(host: str) -> bool:
    if sys.platform != "darwin" or not host:
        return False
    try:
        text = subprocess.check_output(
            [_darwin_bin("route"), "-n", "get", host],
            text=True,
            timeout=2,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return route_text_is_tunnel(text)


def _physical_ipv4() -> list[tuple[str, str]]:
    if sys.platform != "darwin":
        return []
    try:
        text = subprocess.check_output(
            [_darwin_bin("ifconfig")],
            text=True,
            timeout=2,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return physical_ipv4_from_ifconfig(text)


def _stun_exchange(
    host: str,
    port: int,
    timeout: float,
    iface: str | None = None,
    bind_ip: str | None = None,
) -> bytes | None:
    """One STUN datagram. ``iface`` binds a physical NIC so a TUN proxy is skipped."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        if iface and sys.platform == "darwin":
            # IP_BOUND_IF: source must leave this interface, not the default utun.
            sock.setsockopt(socket.IPPROTO_IP, 25, socket.if_nametoindex(iface))
        if bind_ip:
            sock.bind((bind_ip, 0))
        sock.sendto(MAGIC + b"\n", (host, int(port)))
        data, _addr = sock.recvfrom(256)
    except OSError:
        return None
    finally:
        try:
            sock.close()
        except OSError:
            pass
    if data.startswith(MAGIC):
        return data
    return None


def _stun_query(host: str, port: int, timeout: float) -> bytes | None:
    """STUN reply, using a physical NIC when the default route is a local proxy."""
    if _route_is_tunnel(host):
        for iface, ip in _physical_ipv4():
            data = _stun_exchange(host, port, timeout, iface=iface, bind_ip=ip)
            if data:
                return data
    return _stun_exchange(host, port, timeout)


def stun_reachable(host: str, port: int, timeout: float = 2.0) -> bool:
    """True when a Grip STUN reply comes back from ``host:port``."""
    return _stun_query(host, int(port), timeout) is not None


def discover_endpoint(
    rendezvous_url: str,
    *,
    timeout_sec: float = 2.0,
    listen_port: int | None = None,
) -> str | None:
    """Ask rendezvous STUN UDP what public IP:port it sees for us.

    ``listen_port`` is unused for the mapping itself (server reports the UDP
    source port). Callers typically still advertise WG ListenPort separately;
    when NAT is cone-friendly, the returned host is the valuable part and we
    combine with the configured WG listen port.
    """
    from urllib.parse import urlparse

    from grip.tunnel.wgruntime import WG_LISTEN_PORT

    parsed = urlparse(rendezvous_url)
    host = parsed.hostname or ""
    if not host:
        return None
    http_port = int(parsed.port or (443 if parsed.scheme == "https" else 80))
    stun_port = http_port
    data = _stun_query(host, stun_port, timeout_sec)
    if not data:
        return None
    try:
        text = data.decode("ascii", errors="replace").strip()
        parts = text.split()
        # GRIPSTUN 1 <ip> <port>
        if len(parts) >= 4 and parts[0] == "GRIPSTUN" and parts[1] == "1":
            ip = parts[2]
            # Prefer configured WG listen port for Endpoint= (not the ephemeral
            # STUN source port), unless caller wants raw mapping.
            wg_port = int(listen_port or WG_LISTEN_PORT)
            if ip:
                return f"{ip}:{wg_port}"
    except Exception:
        return None
    return None
