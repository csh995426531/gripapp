"""LAN-first self-hosted tunnel: WireGuard direct + rendezvous relay.

See docs/superpowers/specs/2026-11-09-grip-lan-tunnel-design.md.
"""

from __future__ import annotations

from grip.tunnel.path import PathDecision, resolve_ssh_endpoint
from grip.tunnel.store import TunnelStore

__all__ = [
    "PathDecision",
    "TunnelStore",
    "resolve_ssh_endpoint",
]
