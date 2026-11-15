from __future__ import annotations

"""Build RendezvousClient from store settings (URL + optional token)."""

from typing import Any

from grip.tunnel.rendezvous import RendezvousClient
from grip.tunnel.store import TunnelStore


def rendezvous_client(
    store: TunnelStore | None = None,
    *,
    url: str | None = None,
    token: str | None = None,
) -> RendezvousClient:
    store = store or TunnelStore()
    settings = store.settings()
    base = (url or settings.get("rendezvous_url") or "").strip()
    tok = (
        token
        if token is not None
        else str(settings.get("rendezvous_token") or "").strip()
    )
    return RendezvousClient(base, token=tok or None)


def resolve_endpoint_hint(
    store: TunnelStore | None = None,
    *,
    url: str | None = None,
    listen_port: int | None = None,
    existing: str | None = None,
) -> str | None:
    """Prefer STUN, then whoami/register suggested endpoint, then existing."""
    if existing and str(existing).strip():
        # Still try STUN to refresh host when possible.
        pass
    client = rendezvous_client(store, url=url)
    base = client.base
    if not base:
        return existing
    try:
        from grip.tunnel.stun import discover_endpoint

        stun_ep = discover_endpoint(base, listen_port=listen_port)
        if stun_ep:
            return stun_ep
    except Exception:
        pass
    try:
        who = client.whoami()
        sug = str((who or {}).get("suggested_endpoint") or "").strip()
        if sug:
            return sug
    except Exception:
        pass
    cur = str(existing or "").strip()
    return cur or None
