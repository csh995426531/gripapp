from __future__ import annotations

import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from grip.resources import Resource, ResourceStore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ProbeResult:
    name: str
    kind: str
    previous_status: str
    status: str
    detail: str
    checked_at: str
    path: str = ""


def tcp_open(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def ssh_responds(host: str, port: int, timeout: float = 2.0) -> bool:
    """True only after the peer sends an SSH banner.

    A local proxy can finish the TCP handshake and then swallow the bytes.
    That handshake is not a reachable machine.
    """
    try:
        with socket.create_connection((host, int(port)), timeout=timeout) as sock:
            sock.settimeout(timeout)
            data = b""
            while len(data) < 64 and b"SSH-" not in data:
                part = sock.recv(32)
                if not part:
                    break
                data += part
            return b"SSH-" in data
    except OSError:
        return False


def _probe_targets(resource: Resource) -> list[tuple[str, int]]:
    meta = resource.meta or {}
    targets: list[tuple[str, int]] = []
    if resource.kind == "ssh":
        host = meta.get("host")
        port = int(meta.get("port") or 22)
        if host:
            targets.append((str(host), port))
        return targets

    port = int(meta.get("probe_port") or 22)
    for key in ("public_ip", "private_ip"):
        ip = meta.get(key)
        if ip:
            targets.append((str(ip), port))
    return targets


def probe_one(resource: Resource, *, timeout: float = 2.0) -> ProbeResult:
    prev = resource.status or "unknown"
    targets = _probe_targets(resource)
    checked = _now()
    if not targets:
        return ProbeResult(
            name=resource.name,
            kind=resource.kind,
            previous_status=prev,
            status=prev,
            detail="no probe target (no host/ip)",
            checked_at=checked,
        )

    ctrl = prev.lower()
    if resource.kind.endswith("_instance") and ctrl in {
        "stopped",
        "stopping",
        "terminated",
        "terminating",
        "deallocated",
    }:
        return ProbeResult(
            name=resource.name,
            kind=resource.kind,
            previous_status=prev,
            status=prev,
            detail="skip L2: control-plane not running",
            checked_at=checked,
        )

    # Enrolled SSH: LAN IP first. Direct and relay are backups, checked
    # only after the LAN address does not answer. A later LAN answer
    # switches the saved path back, even if the last path was a backup.
    tun = (resource.meta or {}).get("tunnel")
    if resource.kind == "ssh" and isinstance(tun, dict) and tun.get("enabled"):
        from grip.tunnel.path import clear_lan_failed, mark_lan_failed

        meta = resource.meta or {}
        lan_host = str(meta.get("lan_host") or meta.get("host") or "").strip()
        lan_port = int(meta.get("port") or 22)
        lan_ok = bool(lan_host) and ssh_responds(lan_host, lan_port, float(timeout))
        if lan_ok:
            clear_lan_failed(lan_host, lan_port)
        elif lan_host:
            mark_lan_failed(lan_host, lan_port)
        from datetime import datetime, timezone

        from grip.tunnel.channels import _channels, choose_channel, fold_sample

        view = dict(tun)
        ch = _channels(view)
        ch["lan"] = fold_sample(ch.get("lan"), lan_ok, now=datetime.now(timezone.utc))
        view["channels"] = ch
        decision = choose_channel(view)
        if decision["path"] == "lan":
            detail = f"lan ok {lan_host}:{lan_port}"
        elif decision["path"] == "tunnel":
            detail = "direct heartbeat ok"
        elif decision["path"] == "relay":
            detail = "relay heartbeat ok"
        else:
            detail = f"{lan_host}:{lan_port} 没有回应" if lan_host else "没有回应"
        return ProbeResult(
            name=resource.name,
            kind=resource.kind,
            previous_status=prev,
            status=decision["status"],
            detail=detail,
            checked_at=checked,
            path=decision["path"],
        )

    errors: list[str] = []
    for host, port in targets:
        answered = (
            ssh_responds(host, port, timeout=timeout)
            if resource.kind == "ssh"
            else tcp_open(host, port, timeout=timeout)
        )
        if answered:
            if resource.kind == "ssh":
                status = "reachable"
            else:
                # Keep cloud control-plane lifecycle when already running
                status = prev if ctrl == "running" else "running"
            return ProbeResult(
                name=resource.name,
                kind=resource.kind,
                previous_status=prev,
                status=status,
                detail=f"tcp ok {host}:{port}",
                checked_at=checked,
            )
        errors.append(f"{host}:{port}")

    return ProbeResult(
        name=resource.name,
        kind=resource.kind,
        previous_status=prev,
        status="unreachable",
        detail="tcp fail " + ",".join(errors),
        checked_at=checked,
    )


def _remember_channel(store: ResourceStore, name: str, path: str) -> None:
    """Save the channel this probe actually used. LAN wins when it answered."""
    if path not in {"lan", "tunnel", "relay", "down"}:
        return
    current = store.get(name)
    tun = dict(((current.meta if current else {}) or {}).get("tunnel") or {})
    if not tun.get("enabled"):
        return
    tun["path"] = path
    store.patch_meta(name, {"tunnel": tun})


def probe_all(
    store: ResourceStore,
    *,
    kind: str | None = None,
    timeout: float = 2.0,
) -> dict[str, Any]:
    resources = store.list(kind=kind)
    results: list[dict[str, Any]] = []
    for r in resources:
        tun = (r.meta or {}).get("tunnel") if r.kind == "ssh" else None
        if isinstance(tun, dict) and tun.get("enabled"):
            from grip.tunnel.channels import record_sample

            meta = r.meta or {}
            lan_host = str(meta.get("lan_host") or meta.get("host") or "").strip()
            lan_port = int(meta.get("port") or 22)
            lan_ok = bool(lan_host) and ssh_responds(lan_host, lan_port, timeout)
            decision = record_sample(store, r.name, "lan", lan_ok)
            if decision["path"] == "lan":
                detail = f"lan ok {lan_host}:{lan_port}"
            elif decision["path"] == "tunnel":
                detail = "direct heartbeat ok"
            elif decision["path"] == "relay":
                detail = "relay heartbeat ok"
            else:
                detail = f"{lan_host}:{lan_port} 没有回应" if lan_host else "没有回应"
            checked = _now()
            store.apply_probe(r.name, decision["status"], detail, checked)
            results.append(
                {
                    "name": r.name,
                    "kind": r.kind,
                    "status": decision["status"],
                    "previous_status": r.status or "unknown",
                    "detail": detail,
                    "checked_at": checked,
                }
            )
            continue
        pr = probe_one(r, timeout=timeout)
        store.apply_probe(pr.name, pr.status, pr.detail, pr.checked_at)
        _remember_channel(store, pr.name, pr.path)
        results.append(
            {
                "name": pr.name,
                "kind": pr.kind,
                "status": pr.status,
                "previous_status": pr.previous_status,
                "detail": pr.detail,
                "checked_at": pr.checked_at,
            }
        )
    return {
        "checked": len(results),
        "unreachable": sum(1 for x in results if x["status"] == "unreachable"),
        "results": results,
    }
