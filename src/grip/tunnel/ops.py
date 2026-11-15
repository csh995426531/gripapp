from __future__ import annotations

"""High-level tunnel ops — per-device Node model (no Gateway jump host)."""

import secrets
import threading
from datetime import datetime, timezone
from typing import Any

from grip.monitor import ssh_responds
from grip.monitor import ssh_responds as tcp_open
from grip.resources import ResourceStore
from grip.tunnel.path import PathDecision, resolve_ssh_endpoint
from grip.tunnel.relay import open_local_relay_to_rendezvous
from grip.tunnel.store import TunnelStore

_confirm_lock = threading.Lock()


def tunnel_status(store: TunnelStore | None = None) -> dict[str, Any]:
    store = store or TunnelStore()
    ident = store.identity()
    settings = store.settings()
    rendezvous_url = str(settings.get("rendezvous_url") or "").strip()
    health: dict[str, Any] | None = None
    peers: dict[str, Any] = {}
    if rendezvous_url:
        from grip.tunnel.clientutil import rendezvous_client

        client = rendezvous_client(store, url=rendezvous_url)
        health = client.health()
        listing = client.list_peers()
        raw = listing.get("peers") if isinstance(listing, dict) else {}
        if isinstance(raw, dict):
            peers = {
                k: {
                    "node_id": v.get("node_id"),
                    "role": v.get("role"),
                    "endpoint": v.get("endpoint"),
                    "devices": list((v.get("devices") or {}).keys())
                    if isinstance(v.get("devices"), dict)
                    else [],
                }
                for k, v in raw.items()
                if isinstance(v, dict) and k != "_server"
            }
    return {
        "model": "node",
        "identity": (
            {
                "node_id": (ident or {}).get("node_id"),
                "role": (ident or {}).get("role"),
                "public_key": (ident or {}).get("public_key"),
            }
            if ident
            else None
        ),
        "rendezvous_url": rendezvous_url or None,
        "rendezvous_health": health,
        "peers": peers,
    }


def set_rendezvous_url(
    url: str,
    store: TunnelStore | None = None,
    *,
    token: str | None = None,
) -> dict[str, Any]:
    store = store or TunnelStore()
    patch: dict[str, Any] = {"rendezvous_url": url.strip()}
    if token is not None:
        patch["rendezvous_token"] = token.strip()
    store.set_settings(patch)
    return tunnel_status(store)


def disable_resource_tunnel(resources: ResourceStore, name: str) -> dict[str, Any]:
    r = resources.get(name)
    if not r or r.kind != "ssh":
        raise KeyError(name)
    resources.patch_meta(
        name, {"tunnel": {"enabled": False, "path": "lan", "model": "node"}}
    )
    return {"ok": True, "name": name}


def decide_path_for_resource(
    resource_meta: dict[str, Any],
    *,
    store: TunnelStore | None = None,
    tcp_timeout_sec: float = 1.5,
    lan_cooldown_sec: float | None = None,
) -> PathDecision:
    store = store or TunnelStore()
    settings = store.settings()
    tun = (
        resource_meta.get("tunnel")
        if isinstance(resource_meta.get("tunnel"), dict)
        else {}
    )
    url = str(
        (tun or {}).get("rendezvous_url") or settings.get("rendezvous_url") or ""
    ).strip()
    kwargs: dict[str, Any] = {
        "tcp_open": tcp_open,
        "tcp_timeout_sec": tcp_timeout_sec,
        "rendezvous_url": url or None,
    }
    if lan_cooldown_sec is not None:
        kwargs["lan_cooldown_sec"] = float(lan_cooldown_sec)
    return resolve_ssh_endpoint(resource_meta, **kwargs)


_HEARTBEAT_KEYS = ("channels", "direct_proven", "relay_proven", "relay_proven_at")


def write_tunnel_fields(
    resources: ResourceStore,
    name: str,
    updates: dict[str, Any],
) -> None:
    """Update tunnel fields without rolling back the channel heartbeat."""
    from grip.tunnel.channels import _state_lock

    with _state_lock:
        current = resources.get(name)
        tun = dict(((current.meta if current else {}) or {}).get("tunnel") or {})
        kept = {key: tun[key] for key in _HEARTBEAT_KEYS if key in tun}
        tun.update(updates)
        tun.update(kept)
        resources.patch_meta(name, {"tunnel": tun})


def ensure_tunnel_ready(
    name: str,
    resource_meta: dict[str, Any],
    *,
    store: TunnelStore | None = None,
    resources: ResourceStore | None = None,
) -> PathDecision:
    """Resolve path: LAN → WireGuard direct → rendezvous TCP relay."""
    store = store or TunnelStore()
    decision = decide_path_for_resource(resource_meta, store=store)

    if decision.path == "lan" or (
        decision.path == "tunnel" and decision.host and decision.port > 0
    ):
        if resources:
            write_tunnel_fields(resources, name, {"path": decision.path})
        return decision

    if decision.path == "down":
        if resources:
            write_tunnel_fields(
                resources,
                name,
                {"path": "down", "last_error": decision.detail},
            )
        return decision

    # Prefer WireGuard direct before opening a relay session.
    try:
        from grip.tunnel.direct import try_wg_direct

        direct = try_wg_direct(
            name, resource_meta, store=store, resources=resources
        )
        if direct is not None:
            return direct
    except Exception:
        pass

    # relay fallback
    tun = dict(resource_meta.get("tunnel") or {})
    url = str(
        tun.get("rendezvous_url") or store.settings().get("rendezvous_url") or ""
    ).strip()
    if not url:
        return PathDecision(
            path="down",
            host=str(resource_meta.get("lan_host") or resource_meta.get("host") or ""),
            port=int(resource_meta.get("port") or 22),
            detail="tunnel_no_rendezvous",
        )

    node_id = str(tun.get("node_id") or "")
    session_id = relay_session_id(node_id)
    token = str(store.settings().get("rendezvous_token") or "").strip() or None
    local = open_local_relay_to_rendezvous(
        url,
        session_id=session_id,
        role="client",
        resource=name,
        gateway_id=node_id,
        token=token,
    )

    try:
        from grip.tunnel.clientutil import rendezvous_client

        ident = store.ensure_identity(role="client")
        rendezvous_client(store, url=url).register(
            {
                "node_id": ident["node_id"],
                "public_key": ident["public_key"],
                "role": "client",
                "devices": {
                    name: {
                        "session_id": session_id,
                        "target_node_id": node_id,
                    }
                },
            }
        )
    except Exception:
        pass

    patched = {
        **tun,
        "path": "relay",
        "model": "node",
        "relay_session_id": session_id,
        "relay_local_host": local.listen_host,
        "relay_local_port": local.listen_port,
        "rendezvous_url": url,
        # Keep any prior wg failure reason for UI; clear only on successful direct.
        "transport": "rendezvous-relay",
    }
    if resources:
        write_tunnel_fields(resources, name, patched)
    return PathDecision(
        path="relay",
        host=local.listen_host,
        port=local.listen_port,
        detail=f"relay to node {node_id or name}",
    )


def relay_session_id(node_id: str) -> str:
    """Session ids travel in an ASCII HTTP header. Device names must stay out."""
    raw = node_id or "node"
    safe = "".join(
        ch for ch in raw if ch.isascii() and (ch.isalnum() or ch in "-_")
    )
    return f"{safe or 'node'}:{secrets.token_hex(4)}"


def _write_relay_proven(resources: ResourceStore, name: str, proven: bool) -> None:
    current = resources.get(name)
    tun = dict(((current.meta if current else {}) or {}).get("tunnel") or {})
    tun["relay_proven"] = bool(proven)
    if proven:
        tun["relay_proven_at"] = datetime.now(timezone.utc).isoformat()
    else:
        tun.pop("relay_proven_at", None)
    resources.patch_meta(name, {"tunnel": tun})


def _rendezvous_reachable(url: str, store: TunnelStore) -> bool:
    try:
        from grip.tunnel.clientutil import rendezvous_client

        health = rendezvous_client(store, url=url).health()
    except Exception:
        return False
    return isinstance(health, dict) and bool(health.get("ok"))


def confirm_off_lan(
    name: str,
    resource_meta: dict[str, Any],
    *,
    store: TunnelStore | None = None,
    resources: ResourceStore | None = None,
    timeout: float = 8.0,
    preserve_if_unreachable: bool = False,
    persist: bool = True,
) -> bool | None:
    """Read an SSH banner through the rendezvous. Enrollment alone is not proof.

    Returns None when the rendezvous itself cannot be reached and the previous
    result should be left alone. A reached rendezvous with no banner is False.
    """
    store = store or TunnelStore()
    tun = dict(resource_meta.get("tunnel") or {})
    url = str(
        tun.get("rendezvous_url") or store.settings().get("rendezvous_url") or ""
    ).strip()
    node_id = str(tun.get("node_id") or "")
    if not tun.get("enabled") or not node_id or not url:
        if persist and resources is not None and not preserve_if_unreachable:
            _write_relay_proven(resources, name, False)
        return False
    if not _rendezvous_reachable(url, store):
        if preserve_if_unreachable:
            return None
        if persist and resources is not None:
            _write_relay_proven(resources, name, False)
        return False

    session_id = relay_session_id(node_id)
    token = str(store.settings().get("rendezvous_token") or "").strip() or None
    local = open_local_relay_to_rendezvous(
        url,
        session_id=session_id,
        role="client",
        resource=name,
        gateway_id=node_id,
        token=token,
    )
    ok = False
    try:
        from grip.tunnel.clientutil import rendezvous_client

        ident = store.ensure_identity(role="client")
        rendezvous_client(store, url=url).register(
            {
                "node_id": ident["node_id"],
                "public_key": ident["public_key"],
                "role": "client",
                "devices": {
                    name: {
                        "session_id": session_id,
                        "target_node_id": node_id,
                    }
                },
            }
        )
        ok = ssh_responds(local.listen_host, local.listen_port, timeout)
    except Exception:
        ok = False
    finally:
        local.stop.set()
    if persist and resources is not None:
        _write_relay_proven(resources, name, ok)
    return ok


def _apply_backup_reachability(
    resources: ResourceStore, name: str, proven: bool | None
) -> None:
    """Backup result updates the row only while LAN is not the current channel."""
    if proven is None:
        return
    current = resources.get(name)
    if current is None:
        return
    tun = dict((current.meta or {}).get("tunnel") or {})
    if not tun.get("enabled") or str(tun.get("path") or "") == "lan":
        return
    tun["path"] = "relay" if proven else "down"
    resources.patch_meta(name, {"tunnel": tun})
    resources.set_status(name, "reachable" if proven else "unreachable")


def confirm_enrolled_off_lan(
    resources: ResourceStore, *, store: TunnelStore | None = None
) -> None:
    """Prove each enrolled LAN device. Skip when a proof is already running."""
    if not _confirm_lock.acquire(blocking=False):
        return
    try:
        store = store or TunnelStore()
        for resource in resources.list():
            if getattr(resource, "kind", "") != "ssh":
                continue
            tun = (resource.meta or {}).get("tunnel")
            if not isinstance(tun, dict) or not tun.get("enabled"):
                continue
            try:
                proven = confirm_off_lan(
                    resource.name,
                    resource.meta or {},
                    store=store,
                    resources=resources,
                    preserve_if_unreachable=True,
                )
                _apply_backup_reachability(resources, resource.name, proven)
            except Exception:
                continue
    finally:
        _confirm_lock.release()
