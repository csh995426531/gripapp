from __future__ import annotations

"""Per-device Grip Node: register with rendezvous and bridge relay → local SSH.

Runs on each enrolled host (not a LAN Gateway jump box).
"""

import json
import socket
import threading
import time
from pathlib import Path
from typing import Any

from grip.tunnel.keys import generate_wg_keypair, new_node_id
from grip.tunnel.relay import gateway_dial_relay
from grip.tunnel.rendezvous import RendezvousClient


def default_node_dir() -> Path:
    return Path.home() / ".grip-node"


def load_node_config(path: Path | None = None) -> dict[str, Any]:
    cfg_path = path or (default_node_dir() / "config.json")
    return json.loads(cfg_path.read_text(encoding="utf-8"))


def write_node_config(cfg: dict[str, Any], path: Path | None = None) -> Path:
    root = (path or default_node_dir())
    if path is None:
        root.mkdir(parents=True, mode=0o700, exist_ok=True)
        cfg_path = root / "config.json"
    else:
        cfg_path = path
        cfg_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        cfg_path.chmod(0o600)
    except OSError:
        pass
    return cfg_path


def new_node_config(
    *,
    rendezvous_url: str,
    resource_name: str,
    ssh_port: int = 22,
    node_id: str | None = None,
    tunnel_ip: str | None = None,
) -> dict[str, Any]:
    priv, pub = generate_wg_keypair()
    return {
        "node_id": node_id or new_node_id("node"),
        "role": "node",
        "resource_name": resource_name,
        "rendezvous_url": rendezvous_url.rstrip("/"),
        "ssh_host": "127.0.0.1",
        "ssh_port": int(ssh_port),
        "private_key": priv,
        "public_key": pub,
        "tunnel_ip": tunnel_ip,
        "wg_listen_port": 51820,
    }


def _try_node_wg(cfg: dict[str, Any], client: RendezvousClient) -> None:
    """Best-effort WireGuard mesh toward operator clients (direct path)."""
    tip = str(cfg.get("tunnel_ip") or "").strip()
    priv = str(cfg.get("private_key") or "").strip()
    if not tip or not priv:
        return
    try:
        from pathlib import Path

        from grip.tunnel.wgconfig import mesh_conf
        from grip.tunnel.wgruntime import WG_IFACE, WG_LISTEN_PORT, apply_wg_conf

        listing = client.list_peers()
        raw = listing.get("peers") if isinstance(listing, dict) else {}
        if not isinstance(raw, dict):
            return
        peers: list[dict[str, Any]] = []
        for nid, peer in raw.items():
            if nid == "_server" or not isinstance(peer, dict):
                continue
            if str(peer.get("role") or "") not in {"client", "operator", "gateway"}:
                continue
            pub = str(peer.get("public_key") or "").strip()
            peer_ip = str(peer.get("tunnel_ip") or "").strip()
            if not pub or not peer_ip:
                continue
            peers.append(
                {
                    "public_key": pub,
                    "tunnel_ip": peer_ip,
                    "endpoint": peer.get("endpoint"),
                }
            )
        if not peers:
            return
        conf = mesh_conf(
            private_key=priv,
            address=tip,
            listen_port=int(cfg.get("wg_listen_port") or WG_LISTEN_PORT),
            peers=peers,
        )
        root = default_node_dir() / "wg"
        root.mkdir(parents=True, mode=0o700, exist_ok=True)
        path = root / f"{WG_IFACE}.conf"
        path.write_text(conf, encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass
        # Prefer Grip-embedded wireguard-go (no system wg-quick).
        apply_wg_conf(path, iface=WG_IFACE)
    except Exception:
        return


def run_node(
    cfg: dict[str, Any],
    *,
    stop: threading.Event | None = None,
    register_interval_sec: float = 15.0,
    poll_interval_sec: float = 1.0,
) -> None:
    """Blocking loop: register + WG direct peers + accept relay sessions."""
    stop = stop or threading.Event()
    url = str(cfg["rendezvous_url"]).rstrip("/")
    node_id = str(cfg["node_id"])
    resource = str(cfg["resource_name"])
    ssh_host = str(cfg.get("ssh_host") or "127.0.0.1")
    ssh_port = int(cfg.get("ssh_port") or 22)
    token = str(cfg.get("rendezvous_token") or "").strip() or None
    client = RendezvousClient(url, token=token)
    seen: set[str] = set()
    last_register = 0.0
    last_wg = 0.0

    def _refresh_endpoint() -> None:
        try:
            from grip.tunnel.stun import discover_endpoint
            from grip.tunnel.wgruntime import WG_LISTEN_PORT

            ep = discover_endpoint(
                url,
                listen_port=int(cfg.get("wg_listen_port") or WG_LISTEN_PORT),
            )
            if ep:
                cfg["endpoint"] = ep
                return
        except Exception:
            pass
        try:
            who = client.whoami()
            sug = str((who or {}).get("suggested_endpoint") or "").strip()
            if sug:
                cfg["endpoint"] = sug
        except Exception:
            pass

    def _register() -> None:
        if not cfg.get("endpoint"):
            _refresh_endpoint()
        resp = client.register(
            {
                "node_id": node_id,
                "public_key": cfg.get("public_key"),
                "role": "node",
                "tunnel_ip": cfg.get("tunnel_ip"),
                "endpoint": cfg.get("endpoint"),
                "devices": {
                    resource: {
                        "ssh_host": ssh_host,
                        "ssh_port": ssh_port,
                        "online": True,
                        "tunnel_ip": cfg.get("tunnel_ip"),
                    }
                },
            }
        )
        if isinstance(resp, dict):
            suggested = str(resp.get("suggested_endpoint") or "").strip()
            if suggested and not cfg.get("endpoint"):
                cfg["endpoint"] = suggested

    _register()
    last_register = time.time()
    _try_node_wg(cfg, client)
    last_wg = time.time()

    while not stop.is_set():
        now = time.time()
        if now - last_register >= register_interval_sec:
            try:
                _register()
            except Exception:
                pass
            last_register = now
        if now - last_wg >= max(register_interval_sec, 30.0):
            _try_node_wg(cfg, client)
            last_wg = now

        try:
            listing = client.list_peers()
            peers = listing.get("peers") if isinstance(listing, dict) else {}
            if not isinstance(peers, dict):
                peers = {}
            for _pid, peer in peers.items():
                if not isinstance(peer, dict):
                    continue
                if peer.get("role") == "node" and peer.get("node_id") == node_id:
                    continue
                devices = peer.get("devices") if isinstance(peer.get("devices"), dict) else {}
                info = devices.get(resource)
                if not isinstance(info, dict):
                    continue
                sid = str(info.get("session_id") or "").strip()
                if not sid or sid in seen:
                    continue
                seen.add(sid)

                def _serve(_sid: str = sid) -> None:
                    try:
                        gateway_dial_relay(
                            url,
                            session_id=_sid,
                            lan_host=ssh_host,
                            lan_port=ssh_port,
                            role="gateway",
                            token=token,
                        )
                    except OSError:
                        pass
                    finally:
                        seen.discard(_sid)

                threading.Thread(target=_serve, daemon=True).start()
        except Exception:
            pass

        if stop.wait(poll_interval_sec):
            break


def run_node_from_config_file(path: Path | None = None) -> None:
    cfg = load_node_config(path)
    run_node(cfg)
