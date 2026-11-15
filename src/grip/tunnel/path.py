from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

TcpProbe = Callable[[str, int, float], bool]

# After LAN probe fails, skip re-probing that host:port for this long and
# fall through to the device Node (tunnel / relay) instead.
DEFAULT_LAN_COOLDOWN_SEC = 60.0
# Soft LAN upgrade while already on Node — fail fast so Node path is not delayed.
LAN_UPGRADE_TIMEOUT_SEC = 0.4

_lan_fail_until: dict[str, float] = {}
_lan_lock = threading.Lock()


@dataclass(frozen=True)
class PathDecision:
    path: str  # lan | tunnel | relay | down
    host: str
    port: int
    detail: str
    via_gateway: bool = False  # legacy field; always False in node model


def _tunnel_meta(resource_meta: dict[str, Any]) -> dict[str, Any]:
    t = resource_meta.get("tunnel")
    return t if isinstance(t, dict) else {}


def _lan_key(host: str, port: int) -> str:
    return f"{host.strip()}:{int(port)}"


def reset_lan_cooldown_for_tests() -> None:
    with _lan_lock:
        _lan_fail_until.clear()


def is_lan_silenced(
    host: str, port: int, *, now: float | None = None
) -> bool:
    if not host:
        return False
    ts = time.monotonic() if now is None else float(now)
    with _lan_lock:
        until = _lan_fail_until.get(_lan_key(host, port), 0.0)
    return until > ts


def mark_lan_failed(
    host: str,
    port: int,
    cooldown_sec: float = DEFAULT_LAN_COOLDOWN_SEC,
    *,
    now: float | None = None,
) -> None:
    if not host:
        return
    ts = time.monotonic() if now is None else float(now)
    with _lan_lock:
        _lan_fail_until[_lan_key(host, port)] = ts + float(cooldown_sec)


def clear_lan_failed(host: str, port: int) -> None:
    if not host:
        return
    with _lan_lock:
        _lan_fail_until.pop(_lan_key(host, port), None)


def resolve_node_endpoint(
    resource_meta: dict[str, Any],
    *,
    tcp_open: TcpProbe,
    tcp_timeout_sec: float = 1.5,
    rendezvous_url: str | None = None,
    prefer_relay: bool = False,
    detail_prefix: str = "",
) -> PathDecision | None:
    """Resolve per-device Node path only (tunnel WG or rendezvous relay).

    Returns None when tunnel is disabled or no Node path is configured.
    Independent of LAN — used so LAN re-probe failure cannot poison Node.
    """
    tun = _tunnel_meta(resource_meta)
    if not tun.get("enabled"):
        return None

    lan_host = str(
        resource_meta.get("lan_host") or resource_meta.get("host") or ""
    ).strip()
    lan_port = int(resource_meta.get("port") or 22)
    rendezvous = (rendezvous_url or str(tun.get("rendezvous_url") or "")).strip()
    tunnel_host = str(tun.get("tunnel_host") or "").strip()
    tunnel_port = int(tun.get("tunnel_port") or 0)
    prefix = detail_prefix

    if tunnel_host and tunnel_port > 0 and not prefer_relay:
        if tcp_open(tunnel_host, tunnel_port, float(tcp_timeout_sec)):
            return PathDecision(
                path="tunnel",
                host=tunnel_host,
                port=tunnel_port,
                detail=f"{prefix}tunnel ok {tunnel_host}:{tunnel_port}",
            )
        # Direct WG down — still try relay if rendezvous exists.
        if not rendezvous:
            return PathDecision(
                path="down",
                host=lan_host,
                port=lan_port,
                detail=f"{prefix}tunnel_unavailable",
            )

    if not rendezvous:
        return PathDecision(
            path="down",
            host=lan_host,
            port=lan_port,
            detail="tunnel_no_rendezvous",
        )

    return PathDecision(
        path="relay",
        host=str(tun.get("relay_local_host") or "127.0.0.1"),
        port=int(tun.get("relay_local_port") or 0),
        detail=f"{prefix}use rendezvous relay to device node",
    )


def resolve_ssh_endpoint(
    resource_meta: dict[str, Any],
    *,
    tcp_open: TcpProbe,
    tcp_timeout_sec: float = 1.5,
    rendezvous_url: str | None = None,
    gateway_endpoint: str | None = None,
    prefer_relay: bool = False,
    lan_cooldown_sec: float = DEFAULT_LAN_COOLDOWN_SEC,
    now: float | None = None,
) -> PathDecision:
    """Choose LAN vs per-device Node relay/tunnel endpoint.

    LAN wins only when its probe succeeds. LAN failure (or silence) never
    overrides a working Node decision — re-probe fail only re-extends silence.
    """
    del gateway_endpoint  # unused; kept for call-site compatibility
    ts = time.monotonic() if now is None else float(now)
    lan_host = str(
        resource_meta.get("lan_host") or resource_meta.get("host") or ""
    ).strip()
    lan_port = int(resource_meta.get("port") or 22)
    tun = _tunnel_meta(resource_meta)
    enabled = bool(tun.get("enabled"))
    prev_path = str(tun.get("path") or "").strip()
    on_node = prev_path in {"relay", "tunnel"}

    lan_silenced = bool(lan_host) and is_lan_silenced(lan_host, lan_port, now=ts)

    # While already on Node (or after silence), resolve Node first so a slow/fail
    # LAN re-probe cannot delay or poison tunnel/relay reachability.
    node: PathDecision | None = None
    if enabled and (lan_silenced or on_node):
        node = resolve_node_endpoint(
            resource_meta,
            tcp_open=tcp_open,
            tcp_timeout_sec=tcp_timeout_sec,
            rendezvous_url=rendezvous_url,
            prefer_relay=prefer_relay,
            detail_prefix="lan silenced; " if lan_silenced else "",
        )

    if lan_host and not lan_silenced:
        # Soft upgrade when already on Node: short LAN timeout only.
        lan_timeout = (
            min(float(tcp_timeout_sec), LAN_UPGRADE_TIMEOUT_SEC)
            if on_node
            else float(tcp_timeout_sec)
        )
        if tcp_open(lan_host, lan_port, lan_timeout):
            clear_lan_failed(lan_host, lan_port)
            return PathDecision(
                path="lan",
                host=lan_host,
                port=lan_port,
                detail=f"lan ok {lan_host}:{lan_port}",
            )
        # LAN failed — silence and keep Node if available (do not degrade Node).
        if enabled:
            mark_lan_failed(
                lan_host, lan_port, float(lan_cooldown_sec), now=ts
            )
            if node is None:
                node = resolve_node_endpoint(
                    resource_meta,
                    tcp_open=tcp_open,
                    tcp_timeout_sec=tcp_timeout_sec,
                    rendezvous_url=rendezvous_url,
                    prefer_relay=prefer_relay,
                    detail_prefix="lan silenced; ",
                )
            if node is not None:
                return node
            return PathDecision(
                path="down",
                host=lan_host,
                port=lan_port,
                detail="lan unreachable; tunnel not enabled",
            )
        return PathDecision(
            path="down",
            host=lan_host,
            port=lan_port,
            detail="lan unreachable; tunnel not enabled",
        )

    if lan_silenced and enabled:
        if node is None:
            node = resolve_node_endpoint(
                resource_meta,
                tcp_open=tcp_open,
                tcp_timeout_sec=tcp_timeout_sec,
                rendezvous_url=rendezvous_url,
                prefer_relay=prefer_relay,
                detail_prefix="lan silenced; ",
            )
        if node is not None:
            return node

    if not enabled:
        return PathDecision(
            path="down",
            host=lan_host,
            port=lan_port,
            detail=(
                "lan silenced; tunnel not enabled"
                if lan_silenced
                else "lan unreachable; tunnel not enabled"
            ),
        )

    # First contact / no prior node path: resolve Node after LAN miss path above.
    node = resolve_node_endpoint(
        resource_meta,
        tcp_open=tcp_open,
        tcp_timeout_sec=tcp_timeout_sec,
        rendezvous_url=rendezvous_url,
        prefer_relay=prefer_relay,
        detail_prefix="lan silenced; " if lan_silenced else "lan unreachable; ",
    )
    if node is not None:
        return node
    return PathDecision(
        path="down",
        host=lan_host,
        port=lan_port,
        detail="tunnel_no_rendezvous",
    )
