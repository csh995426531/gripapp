from __future__ import annotations

"""Home Gateway: register with rendezvous, expose LAN device forwards.

Also serves local TCP listeners that map listen_port → lan_host:lan_port so a
WireGuard peer can SSH to gateway_tunnel_ip:listen_port.
"""

import json
import socket
import threading
import time
from typing import Any

from grip.tunnel.rendezvous import RendezvousClient
from grip.tunnel.store import TunnelStore
from grip.tunnel.wgconfig import gateway_conf


def _forward(client: socket.socket, lan_host: str, lan_port: int) -> None:
    import select

    try:
        remote = socket.create_connection((lan_host, int(lan_port)), timeout=10)
    except OSError:
        try:
            client.close()
        except OSError:
            pass
        return
    sockets = [client, remote]
    try:
        while True:
            r, _w, _x = select.select(sockets, [], [], 300)
            if not r:
                break
            for s in r:
                other = remote if s is client else client
                data = s.recv(65536)
                if not data:
                    return
                other.sendall(data)
    finally:
        for s in (client, remote):
            try:
                s.close()
            except OSError:
                pass


def start_device_listeners(
    device_map: dict[str, Any],
    *,
    bind_host: str = "0.0.0.0",
    stop: threading.Event | None = None,
) -> list[threading.Thread]:
    stop = stop or threading.Event()
    threads: list[threading.Thread] = []

    for name, info in device_map.items():
        if not isinstance(info, dict):
            continue
        listen_port = int(info.get("listen_port") or 0)
        lan_host = str(info.get("lan_host") or "")
        lan_port = int(info.get("lan_port") or 22)
        if not listen_port or not lan_host:
            continue

        def _listen(
            lp: int = listen_port,
            lh: str = lan_host,
            lport: int = lan_port,
            _name: str = name,
        ) -> None:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind((bind_host, lp))
            srv.listen(20)
            srv.settimeout(0.5)
            while not stop.is_set():
                try:
                    conn, _ = srv.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                threading.Thread(
                    target=_forward, args=(conn, lh, lport), daemon=True
                ).start()
            try:
                srv.close()
            except OSError:
                pass

        th = threading.Thread(target=_listen, daemon=True, name=f"gw-fwd-{name}")
        th.start()
        threads.append(th)
    return threads


def _watch_relay_requests(
    rendezvous_url: str,
    *,
    device_map: dict[str, Any],
    stop: threading.Event,
    seen: set[str],
) -> None:
    """P1: when a client advertises relay session_id, gateway dials and forwards."""
    from grip.tunnel.relay import gateway_dial_relay

    client = RendezvousClient(rendezvous_url)
    while not stop.is_set():
        try:
            listing = client.list_peers()
            peers = listing.get("peers") if isinstance(listing, dict) else {}
            if not isinstance(peers, dict):
                peers = {}
            for _nid, peer in peers.items():
                if not isinstance(peer, dict) or peer.get("role") == "gateway":
                    continue
                devices = peer.get("devices") if isinstance(peer.get("devices"), dict) else {}
                for res_name, info in devices.items():
                    if not isinstance(info, dict):
                        continue
                    sid = str(info.get("session_id") or "").strip()
                    if not sid or sid in seen:
                        continue
                    # Prefer live device_map; fall back to client hint.
                    local = device_map.get(res_name) if isinstance(device_map.get(res_name), dict) else {}
                    lan_host = str(
                        (local or {}).get("lan_host")
                        or info.get("lan_host")
                        or ""
                    )
                    lan_port = int(
                        (local or {}).get("lan_port")
                        or info.get("lan_port")
                        or 22
                    )
                    if not lan_host:
                        continue
                    seen.add(sid)

                    def _dial(
                        _sid: str = sid,
                        _lh: str = lan_host,
                        _lp: int = lan_port,
                    ) -> None:
                        try:
                            gateway_dial_relay(
                                rendezvous_url,
                                session_id=_sid,
                                lan_host=_lh,
                                lan_port=_lp,
                            )
                        except OSError:
                            pass
                        finally:
                            seen.discard(_sid)

                    threading.Thread(target=_dial, daemon=True).start()
        except Exception:
            pass
        if stop.wait(1.0):
            break


def run_gateway(
    *,
    rendezvous_url: str,
    store: TunnelStore | None = None,
    wg_listen_port: int = 51820,
    advertise_endpoint: str | None = None,
    register_interval_sec: float = 20.0,
    stop: threading.Event | None = None,
) -> dict[str, Any]:
    """Blocking gateway loop (register + local forwards + relay accept)."""
    store = store or TunnelStore()
    stop = stop or threading.Event()
    ident = store.ensure_identity(role="gateway")
    # Force gateway role/ip if identity was created as client earlier.
    if ident.get("role") != "gateway":
        ident["role"] = "gateway"
        if not str(ident.get("tunnel_ip") or "").startswith("10.77.0."):
            ident["tunnel_ip"] = "10.77.0.1"
        store._write_json(store._identity_path(), ident)  # noqa: SLF001

    device_map = store.device_map()
    start_device_listeners(device_map, stop=stop)

    conf = gateway_conf(
        private_key=str(ident["private_key"]),
        address=str(ident.get("tunnel_ip") or "10.77.0.1"),
        listen_port=wg_listen_port,
        clients=[],  # peers added after pair files land in store.gateways / settings
    )
    # Include paired clients from settings.clients if present
    clients = list((store.settings().get("clients") or []))
    if clients:
        conf = gateway_conf(
            private_key=str(ident["private_key"]),
            address=str(ident.get("tunnel_ip") or "10.77.0.1"),
            listen_port=wg_listen_port,
            clients=clients,
        )
    conf_path = store.write_wg_conf("gateway", conf)

    client = RendezvousClient(rendezvous_url)
    endpoint = advertise_endpoint or f"{_guess_ip()}:{wg_listen_port}"
    seen_sessions: set[str] = set()
    threading.Thread(
        target=_watch_relay_requests,
        kwargs={
            "rendezvous_url": rendezvous_url,
            "device_map": device_map,
            "stop": stop,
            "seen": seen_sessions,
        },
        daemon=True,
    ).start()

    def _register_once() -> dict[str, Any]:
        return client.register(
            {
                "node_id": ident["node_id"],
                "public_key": ident["public_key"],
                "role": "gateway",
                "endpoint": endpoint,
                "tunnel_ip": ident.get("tunnel_ip"),
                "devices": store.device_map(),
            }
        )

    first = _register_once()
    while not stop.wait(register_interval_sec):
        _register_once()

    return {
        "ok": True,
        "node_id": ident["node_id"],
        "wg_conf": str(conf_path),
        "last_register": first,
    }


def _guess_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def pair_exchange_payload(store: TunnelStore) -> dict[str, Any]:
    """Public pairing blob to share on LAN (no private key)."""
    return store.export_public_identity()


def apply_pair_file(store: TunnelStore, peer: dict[str, Any]) -> dict[str, Any]:
    """Client accepts gateway public identity (or vice versa)."""
    role = str(peer.get("role") or "")
    if role == "gateway" or str(peer.get("node_id") or "").startswith("gw-"):
        store.save_gateway_peer(peer)
        return {"ok": True, "saved": "gateway", "node_id": peer.get("node_id")}
    # Gateway storing a client
    settings = store.settings()
    clients = list(settings.get("clients") or [])
    clients = [c for c in clients if c.get("node_id") != peer.get("node_id")]
    clients.append(
        {
            "node_id": peer.get("node_id"),
            "public_key": peer.get("public_key"),
            "tunnel_ip": peer.get("tunnel_ip") or "10.77.0.2",
            "persistent_keepalive": 25,
        }
    )
    store.set_settings({"clients": clients})
    return {"ok": True, "saved": "client", "node_id": peer.get("node_id")}


def write_client_wg(
    store: TunnelStore,
    *,
    gateway_id: str,
    gateway_endpoint: str | None,
) -> str:
    from grip.tunnel.wgconfig import client_conf

    ident = store.ensure_identity(role="client")
    gws = store.gateways()
    gw = gws.get(gateway_id) or next(iter(gws.values()), None)
    if not gw:
        raise KeyError("gateway_not_paired")
    body = client_conf(
        private_key=str(ident["private_key"]),
        address=str(ident.get("tunnel_ip") or "10.77.0.2"),
        gateway_public_key=str(gw["public_key"]),
        gateway_tunnel_ip=str(gw.get("tunnel_ip") or "10.77.0.1"),
        gateway_endpoint=gateway_endpoint or gw.get("endpoint"),
    )
    path = store.write_wg_conf("client", body)
    return str(path)
