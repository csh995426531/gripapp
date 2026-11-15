"""Three connection channels, each with its own heartbeat.

Priority when choosing the path used right now:
1. LAN IP
2. Rendezvous direct (WireGuard)
3. Rendezvous relay

A channel stays up through one missed heartbeat, and drops on the second
consecutive miss. One success brings it back, so LAN is taken again as soon
as it answers. The multi-channel tag follows only the direct and relay
states: both down removes it, and a later success puts it back.

LAN and direct heartbeats never wait on a relay check. Relay runs one
device at a time on its own lock.
"""

from __future__ import annotations

import threading
import zlib
from datetime import datetime, timezone
from typing import Any

from grip.resources import ResourceStore

# Higher priority is checked more often. Relay is the heaviest check.
LAN_INTERVAL_SEC = 20.0
DIRECT_INTERVAL_SEC = 60.0
RELAY_INTERVAL_SEC = 180.0
FAILS_TO_DROP = 2
STALE_FACTOR = 2.5

_fast_lock = threading.Lock()
_relay_lock = threading.Lock()
_state_lock = threading.Lock()


def _now(now: datetime | None = None) -> datetime:
    if now is not None:
        return now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def _iso(now: datetime) -> str:
    return now.astimezone(timezone.utc).isoformat()


def _parse(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _channels(tun: dict[str, Any]) -> dict[str, Any]:
    raw = tun.get("channels")
    return dict(raw) if isinstance(raw, dict) else {}


def fold_sample(record: Any, ok: bool, *, now: datetime | None = None) -> dict[str, Any]:
    """One success restores the channel. The second consecutive miss drops it."""
    moment = _now(now)
    prev_ok = isinstance(record, dict) and record.get("ok") is True
    prev_fail = int(record.get("fail") or 0) if isinstance(record, dict) else 0
    if ok:
        return {"ok": True, "fail": 0, "at": _iso(moment)}
    fail = prev_fail + 1
    if prev_ok and fail < FAILS_TO_DROP:
        return {"ok": True, "fail": fail, "at": _iso(moment)}
    return {"ok": False, "fail": fail, "at": _iso(moment)}


def alive(
    record: Any,
    interval_sec: float,
    *,
    now: datetime | None = None,
) -> bool:
    """Up until two intervals and a half pass with no fresh heartbeat."""
    if not isinstance(record, dict) or record.get("ok") is not True:
        return False
    checked = _parse(record.get("at"))
    if checked is None:
        return False
    age = (_now(now) - checked).total_seconds()
    return age <= float(interval_sec) * STALE_FACTOR


def choose_channel(
    tun: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Pick the current path from stored heartbeats."""
    moment = _now(now)
    ch = _channels(tun)
    lan_up = alive(ch.get("lan"), LAN_INTERVAL_SEC, now=moment)
    direct_up = alive(ch.get("direct"), DIRECT_INTERVAL_SEC, now=moment)
    relay_up = alive(ch.get("relay"), RELAY_INTERVAL_SEC, now=moment)
    if lan_up:
        path, status = "lan", "reachable"
    elif direct_up:
        path, status = "tunnel", "reachable"
    elif relay_up:
        path, status = "relay", "reachable"
    else:
        path, status = "down", "unreachable"
    return {
        "path": path,
        "status": status,
        "multi": direct_up or relay_up,
        "direct": direct_up,
        "relay": relay_up,
    }


def _gap(kind: str, name: str) -> float:
    base = {
        "lan": LAN_INTERVAL_SEC,
        "direct": DIRECT_INTERVAL_SEC,
        "relay": RELAY_INTERVAL_SEC,
    }[kind]
    if kind == "lan":
        return base
    # Spread devices so their heavy checks do not land on the same second.
    return base + (zlib.adler32(name.encode()) % 20)


def _due(record: Any, gap_sec: float, *, now: datetime, force: bool) -> bool:
    if force:
        return True
    if not isinstance(record, dict):
        return True
    checked = _parse(record.get("at"))
    if checked is None:
        return True
    return (_now(now) - checked).total_seconds() >= float(gap_sec)


def record_sample(
    resources: ResourceStore,
    name: str,
    kind: str,
    ok: bool,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Fold one sample into the channel and recompute path, status, and the tag."""
    moment = _now(now)
    with _state_lock:
        return _record_sample_locked(resources, name, kind, ok, now=moment)


def _record_sample_locked(
    resources: ResourceStore,
    name: str,
    kind: str,
    ok: bool,
    *,
    now: datetime,
) -> dict[str, Any]:
    moment = now
    current = resources.get(name)
    tun = dict(((current.meta if current else {}) or {}).get("tunnel") or {})
    ch = _channels(tun)
    ch[kind] = fold_sample(ch.get(kind), ok, now=moment)
    tun["channels"] = ch
    decision = choose_channel(tun, now=moment)
    tun["path"] = decision["path"]
    tun["direct_proven"] = decision["direct"]
    tun["relay_proven"] = decision["relay"]
    if decision["relay"]:
        tun["relay_proven_at"] = _iso(moment)
    else:
        tun.pop("relay_proven_at", None)
    resources.patch_meta(name, {"tunnel": tun})
    resources.set_status(name, decision["status"])
    return decision


def _enrolled(resources: ResourceStore, names: set[str] | None) -> list[Any]:
    picked = []
    for resource in resources.list():
        if names is not None and resource.name not in names:
            continue
        if getattr(resource, "kind", "") != "ssh":
            continue
        tun = (resource.meta or {}).get("tunnel")
        if isinstance(tun, dict) and tun.get("enabled"):
            picked.append(resource)
    return picked


def beat_fast(
    resources: ResourceStore,
    *,
    force: bool = False,
    now: datetime | None = None,
    names: set[str] | None = None,
) -> None:
    """LAN and direct heartbeats. They do not wait for a relay check."""
    if not _fast_lock.acquire(blocking=False):
        return
    try:
        moment = _now(now)
        from grip.monitor import ssh_responds

        for resource in _enrolled(resources, names):
            tun = (resource.meta or {}).get("tunnel") or {}
            ch = _channels(tun)
            meta = resource.meta or {}
            lan_host = str(meta.get("lan_host") or meta.get("host") or "").strip()
            lan_port = int(meta.get("port") or 22)
            if lan_host and _due(
                ch.get("lan"), _gap("lan", resource.name), now=moment, force=force
            ):
                record_sample(
                    resources,
                    resource.name,
                    "lan",
                    ssh_responds(lan_host, lan_port, 2.0),
                    now=moment,
                )
            current = resources.get(resource.name)
            tun = dict(((current.meta if current else {}) or {}).get("tunnel") or {})
            ch = _channels(tun)
            if not _due(
                ch.get("direct"), _gap("direct", resource.name), now=moment, force=force
            ):
                continue
            tunnel_host = str(tun.get("tunnel_host") or "").strip()
            tunnel_port = int(tun.get("tunnel_port") or 0)
            if not tunnel_host or tunnel_port <= 0:
                continue
            record_sample(
                resources,
                resource.name,
                "direct",
                ssh_responds(tunnel_host, tunnel_port, 2.0),
                now=moment,
            )
    finally:
        _fast_lock.release()


def beat_relay_one(
    resources: ResourceStore,
    *,
    store: Any = None,
    force: bool = False,
    now: datetime | None = None,
    names: set[str] | None = None,
) -> None:
    """Check the single most overdue relay. A slow check cannot block LAN."""
    if not _relay_lock.acquire(blocking=False):
        return
    try:
        moment = _now(now)
        from grip.tunnel.ops import confirm_off_lan
        from grip.tunnel.store import TunnelStore

        store = store or TunnelStore()
        overdue: list[tuple[float, Any]] = []
        for resource in _enrolled(resources, names):
            tun = (resource.meta or {}).get("tunnel") or {}
            record = _channels(tun).get("relay")
            if not _due(
                record, _gap("relay", resource.name), now=moment, force=force
            ):
                continue
            checked = _parse(record.get("at")) if isinstance(record, dict) else None
            age = (moment - checked).total_seconds() if checked else 10**9
            overdue.append((age, resource))
        if not overdue:
            return
        overdue.sort(key=lambda item: item[0], reverse=True)
        resource = overdue[0][1]
        current = resources.get(resource.name)
        proven = confirm_off_lan(
            resource.name,
            (current.meta if current else {}) or {},
            store=store,
            resources=resources,
            timeout=20.0,
            persist=False,
        )
        record_sample(resources, resource.name, "relay", proven is True, now=moment)
    finally:
        _relay_lock.release()


def beat_channels(
    resources: ResourceStore,
    *,
    store: Any = None,
    force: bool = False,
    now: datetime | None = None,
    names: set[str] | None = None,
) -> None:
    """Fast heartbeats first, then at most one relay."""
    beat_fast(resources, force=force, now=now, names=names)
    beat_relay_one(resources, store=store, force=force, now=now, names=names)
