from __future__ import annotations

"""WireGuard direct path: prefer over rendezvous TCP relay when peers are up."""

from typing import Any

from grip.monitor import ssh_responds as tcp_open
from grip.resources import ResourceStore
from grip.tunnel.path import PathDecision
from grip.tunnel.store import TunnelStore
from grip.tunnel.wgconfig import mesh_conf
from grip.tunnel.wgruntime import (
    OPERATOR_TUNNEL_IP,
    WG_IFACE,
    WG_LISTEN_PORT,
    apply_wg_conf,
)


def _peer_from_listing(listing: dict[str, Any], node_id: str) -> dict[str, Any] | None:
    raw = listing.get("peers") if isinstance(listing, dict) else None
    if not isinstance(raw, dict):
        return None
    peer = raw.get(node_id)
    return peer if isinstance(peer, dict) else None


def collect_node_peers(
    store: TunnelStore,
    client: RendezvousClient,
    *,
    prefer_node_id: str | None = None,
) -> list[dict[str, Any]]:
    """Build WG peer list from rendezvous (all role=node with keys + tunnel_ip)."""
    listing = client.list_peers()
    raw = listing.get("peers") if isinstance(listing, dict) else {}
    if not isinstance(raw, dict):
        return []
    peers: list[dict[str, Any]] = []
    for nid, peer in raw.items():
        if nid == "_server" or not isinstance(peer, dict):
            continue
        if str(peer.get("role") or "") not in {"node", "gateway"}:
            continue
        pub = str(peer.get("public_key") or "").strip()
        tip = str(peer.get("tunnel_ip") or "").strip()
        if not pub or not tip:
            continue
        entry = {
            "node_id": peer.get("node_id") or nid,
            "public_key": pub,
            "tunnel_ip": tip,
            "endpoint": peer.get("endpoint"),
        }
        peers.append(entry)
    if prefer_node_id:
        peers.sort(key=lambda p: 0 if p.get("node_id") == prefer_node_id else 1)
    # Also fold locally remembered device IPs from store map
    for name, info in store.device_map().items():
        if not isinstance(info, dict):
            continue
        tip = str(info.get("tunnel_ip") or "").strip()
        pub = str(info.get("public_key") or "").strip()
        if not tip or not pub:
            continue
        if any(p.get("tunnel_ip") == tip for p in peers):
            continue
        peers.append(
            {
                "node_id": info.get("node_id") or name,
                "public_key": pub,
                "tunnel_ip": tip,
                "endpoint": info.get("endpoint"),
            }
        )
    return peers


def try_wg_direct(
    name: str,
    resource_meta: dict[str, Any],
    *,
    store: TunnelStore | None = None,
    resources: ResourceStore | None = None,
    tcp_timeout_sec: float = 1.5,
    apply_conf: Any | None = None,
) -> PathDecision | None:
    """Register operator, sync WG peers, probe device tunnel_ip:ssh.

    Returns PathDecision(path=tunnel) on success, else None (caller → relay).
    """
    store = store or TunnelStore()
    tun = dict(resource_meta.get("tunnel") or {})
    if not tun.get("enabled"):
        return None
    url = str(
        tun.get("rendezvous_url") or store.settings().get("rendezvous_url") or ""
    ).strip()
    if not url:
        return None

    node_id = str(tun.get("node_id") or "").strip()
    ssh_port = int(resource_meta.get("port") or 22)
    apply = apply_conf or apply_wg_conf

    from grip.tunnel.clientutil import rendezvous_client, resolve_endpoint_hint

    ident = store.ensure_identity(role="client")
    client = rendezvous_client(store, url=url)

    operator_endpoint = resolve_endpoint_hint(
        store,
        url=url,
        listen_port=WG_LISTEN_PORT,
        existing=str(tun.get("operator_endpoint") or "").strip() or None,
    )

    # Advertise operator so Nodes can peer back (hole punch / keepalive).
    try:
        client.register(
            {
                "node_id": ident["node_id"],
                "public_key": ident["public_key"],
                "role": "client",
                "tunnel_ip": ident.get("tunnel_ip") or OPERATOR_TUNNEL_IP,
                "endpoint": operator_endpoint,
            }
        )
    except Exception:
        pass

    peers = collect_node_peers(store, client, prefer_node_id=node_id or None)
    # Ensure current resource peer is present even if rendezvous is briefly stale.
    tip = str(tun.get("tunnel_host") or tun.get("tunnel_ip") or "").strip()
    pub = str(tun.get("public_key") or "").strip()
    if tip and pub and not any(p.get("tunnel_ip") == tip for p in peers):
        peers.append(
            {
                "node_id": node_id or name,
                "public_key": pub,
                "tunnel_ip": tip,
                "endpoint": tun.get("endpoint"),
            }
        )

    # Lookup fresh peer record for this node
    target_ip = tip
    target_pub = pub
    target_endpoint = tun.get("endpoint")
    if node_id:
        got = client.get_peer(node_id)
        peer = got.get("peer") if isinstance(got, dict) else None
        if isinstance(peer, dict):
            target_ip = str(peer.get("tunnel_ip") or target_ip or "").strip()
            target_pub = str(peer.get("public_key") or target_pub or "").strip()
            target_endpoint = peer.get("endpoint") or target_endpoint
            if target_ip and target_pub:
                # refresh peers entry
                peers = [
                    p for p in peers if p.get("node_id") != node_id and p.get("tunnel_ip") != target_ip
                ]
                peers.insert(
                    0,
                    {
                        "node_id": node_id,
                        "public_key": target_pub,
                        "tunnel_ip": target_ip,
                        "endpoint": target_endpoint,
                    },
                )

    if not target_ip or not target_pub:
        return None

    conf = mesh_conf(
        private_key=str(ident["private_key"]),
        address=str(ident.get("tunnel_ip") or OPERATOR_TUNNEL_IP),
        listen_port=WG_LISTEN_PORT,
        peers=peers,
    )
    conf_path = store.write_wg_conf("grip", conf)
    # Also write iface-named copy for wg-quick
    store.write_wg_conf(WG_IFACE, conf)

    status = apply(conf_path, iface=WG_IFACE)
    # Only claim direct when apply succeeded *and* tunnel IP answers SSH.
    if status.get("ok") and tcp_open(target_ip, ssh_port, float(tcp_timeout_sec)):
        patched = {
            **tun,
            "path": "tunnel",
            "model": "node",
            "tunnel_host": target_ip,
            "tunnel_ip": target_ip,
            "tunnel_port": ssh_port,
            "public_key": target_pub,
            "endpoint": target_endpoint,
            "wg_iface": status.get("iface") or WG_IFACE,
            "wg_status": status,
            "rendezvous_url": url,
            "last_error": None,
        }
        store.upsert_device(
            name,
            lan_host=str(resource_meta.get("lan_host") or resource_meta.get("host") or ""),
            lan_port=ssh_port,
        )
        m = store.device_map()
        ent = dict(m.get(name) or {})
        ent.update(
            {
                "tunnel_ip": target_ip,
                "public_key": target_pub,
                "node_id": node_id,
                "endpoint": target_endpoint,
            }
        )
        m[name] = ent
        store.save_device_map(m)

        if resources:
            from grip.tunnel.ops import write_tunnel_fields

            write_tunnel_fields(resources, name, patched)
        return PathDecision(
            path="tunnel",
            host=target_ip,
            port=ssh_port,
            detail=f"wg direct {target_ip}:{ssh_port}",
        )

    # Honest: WG unavailable → caller falls back to relay.
    if resources:
        from grip.tunnel.ops import write_tunnel_fields

        write_tunnel_fields(
            resources,
            name,
            {
                "wg_status": status,
                "last_error": str(
                    status.get("error")
                    or status.get("detail")
                    or "wg_direct_unavailable"
                )[:240],
                # Do not set path=tunnel; relay step will set path=relay.
            },
        )
    return None
