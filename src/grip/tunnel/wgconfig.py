from __future__ import annotations

from typing import Any


def render_interface_conf(
    *,
    private_key: str,
    address: str,
    listen_port: int | None = None,
    peers: list[dict[str, Any]] | None = None,
) -> str:
    """Render a wg-quick style config (no secrets logged by caller)."""
    lines = ["[Interface]", f"PrivateKey = {private_key}", f"Address = {address}/32"]
    if listen_port:
        lines.append(f"ListenPort = {int(listen_port)}")
    for peer in peers or []:
        lines.extend(
            [
                "",
                "[Peer]",
                f"PublicKey = {peer['public_key']}",
            ]
        )
        if peer.get("allowed_ips"):
            lines.append(f"AllowedIPs = {peer['allowed_ips']}")
        if peer.get("endpoint"):
            lines.append(f"Endpoint = {peer['endpoint']}")
        if peer.get("persistent_keepalive"):
            lines.append(f"PersistentKeepalive = {int(peer['persistent_keepalive'])}")
    lines.append("")
    return "\n".join(lines)


def client_conf(
    *,
    private_key: str,
    address: str,
    gateway_public_key: str,
    gateway_tunnel_ip: str,
    gateway_endpoint: str | None,
    keepalive: int = 25,
) -> str:
    peer: dict[str, Any] = {
        "public_key": gateway_public_key,
        "allowed_ips": f"{gateway_tunnel_ip}/32",
        "persistent_keepalive": keepalive,
    }
    if gateway_endpoint:
        peer["endpoint"] = gateway_endpoint
    return render_interface_conf(
        private_key=private_key, address=address, peers=[peer]
    )


def gateway_conf(
    *,
    private_key: str,
    address: str,
    listen_port: int,
    clients: list[dict[str, Any]],
) -> str:
    peers = [
        {
            "public_key": c["public_key"],
            "allowed_ips": f"{c['tunnel_ip']}/32",
            "persistent_keepalive": int(c.get("persistent_keepalive") or 25),
        }
        for c in clients
    ]
    return render_interface_conf(
        private_key=private_key,
        address=address,
        listen_port=listen_port,
        peers=peers,
    )


def mesh_conf(
    *,
    private_key: str,
    address: str,
    listen_port: int | None,
    peers: list[dict[str, Any]],
    keepalive: int = 25,
) -> str:
    """Operator or Node conf: one interface, many peers (direct path)."""
    rendered: list[dict[str, Any]] = []
    for p in peers:
        pub = str(p.get("public_key") or "").strip()
        tip = str(p.get("tunnel_ip") or "").strip()
        if not pub or not tip:
            continue
        peer: dict[str, Any] = {
            "public_key": pub,
            "allowed_ips": f"{tip}/32",
            "persistent_keepalive": int(p.get("persistent_keepalive") or keepalive),
        }
        ep = str(p.get("endpoint") or "").strip()
        if ep:
            peer["endpoint"] = ep
        rendered.append(peer)
    return render_interface_conf(
        private_key=private_key,
        address=address,
        listen_port=listen_port,
        peers=rendered,
    )
