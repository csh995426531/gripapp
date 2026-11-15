from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import Context, MCPServer

from grip.audit import resolve_mcp_actor
from grip.service import GripService

_AGENT_INSTRUCTIONS = """\
Grip is a local secure resource gateway. Follow these rules:

- Operate devices only through these MCP tools (name-based). Do not read or modify \
~/.grip or $GRIP_HOME (vault.enc, master.key, grip.db, audit data).
- Never request, echo, or dump SSH passwords, private keys, or cloud AccessKeys. \
Humans enter secrets in the Grip menubar / CLI vault.
- Do not delete, truncate, or tamper with Grip audit/task history.
- Do not reverse-engineer Grip or decrypt the vault to obtain credentials.
"""

mcp = MCPServer(
    "grip",
    title="Grip",
    description="Local secure MCP resource gateway — secrets stay in vault, never in tool results.",
    instructions=_AGENT_INSTRUCTIONS,
)
_service = GripService()


def get_service() -> GripService:
    return _service


def _mcp_client_name(ctx: Context | None) -> str | None:
    """Best-effort MCP initialize clientInfo.name (not always present)."""
    if ctx is None:
        return None
    try:
        params = ctx.session.client_params
    except Exception:  # noqa: BLE001
        return None
    if not params:
        return None
    info = getattr(params, "client_info", None)
    if info is None:
        return None
    name = getattr(info, "name", None)
    return str(name) if name else None


@mcp.tool()
def list_resources(kind: str = "all") -> dict[str, Any]:
    """List resources. kind: all|ssh|oracle|gcp|aliyun|tencent. Never returns secrets."""
    return get_service().list_resources(kind=kind)


@mcp.tool()
def list_cloud_accounts(cloud: str = "") -> dict[str, Any]:
    """List configured cloud accounts (labels/meta only — never secrets)."""
    return get_service().list_cloud_accounts(cloud=cloud or None)


@mcp.tool()
def get_resource(name: str) -> dict[str, Any]:
    """Get one resource by logical name. Secrets are never included."""
    return get_service().get_resource(name)


@mcp.tool()
def run_command(
    name: str,
    command: str,
    timeout_sec: int = 60,
    confirm_write: bool = False,
    background: bool = True,
    await_probe_json: str = "",
    actor: str = "",
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Run command on SSH resource (Linux/macOS/Windows via OpenSSH).

    Grip injects credentials from the local vault — do NOT read ~/.grip or paste keys.
    Status tracks the executor SESSION (and optional probe), NOT business outcomes.
    Default background=True returns task_id immediately for parallel A/B/C.
    The result always says which path this call took:
    path=lan route=局域网, usually a few seconds (expect_sec 15);
    path=tunnel route=汇合点直连, allow about 30 seconds;
    path=relay route=汇合点中继, slow, a short command often takes tens of seconds —
    wait at least expect_sec (90) before treating it as failed. Read `note`.
    path=down means no channel was usable and the command was not sent.
    The same path/route/expect_sec/note is on get_task.
    await_probe_json example: {"type":"tcp","target":"10.0.0.1:8080","timeout_sec":90}
    types: file (remote path), tcp (host:port from Grip), http (URL).
    Optional actor: caller label (e.g. cursor). If empty, uses MCP clientInfo.name
    when the client sent it on initialize; otherwise actor=mcp.
    """
    probe = json.loads(await_probe_json) if await_probe_json.strip() else None
    who = resolve_mcp_actor(actor or None, client_name=_mcp_client_name(ctx))
    # Prefer the live menubar process when present so password SSH sessions
    # are not opened a second time (AutoDL etc. → Authentication failed).
    if probe is None:
        from grip.panel_proxy import try_panel_run_command

        proxied = try_panel_run_command(
            name,
            command,
            timeout_sec=timeout_sec,
            confirm_write=confirm_write,
            background=background,
            actor=who,
        )
        if proxied is not None:
            return proxied
    return get_service().run_command(
        name,
        command,
        timeout_sec=timeout_sec,
        confirm_write=confirm_write,
        background=background,
        await_probe=probe,
        actor=who,
    )


@mcp.tool()
def get_task(task_id: str) -> dict[str, Any]:
    """Fetch task status/phase/heartbeat/events (redacted)."""
    return get_service().get_task(task_id)


@mcp.tool()
def list_tasks(limit: int = 20, status: str = "") -> dict[str, Any]:
    """List recent tasks."""
    return get_service().list_tasks(limit=limit, status=status or None)


@mcp.tool()
def board() -> dict[str, Any]:
    """Board: resources (L1/L2 health) + tasks by host (session status)."""
    return get_service().board()


@mcp.tool()
def probe_resources(kind: str = "") -> dict[str, Any]:
    """L2 resource reachability: TCP probe SSH hosts and cloud instance IPs. Not task status."""
    return get_service().probe_resources(kind=kind or None)


@mcp.tool()
def watch_once(sync_clouds: bool = False, sample_metrics: bool = True) -> dict[str, Any]:
    """One monitor tick: optional cloud sync (L1) + probe (L2) + SSH metrics (L3)."""
    return get_service().watch_once(sync=sync_clouds, metrics=sample_metrics)


@mcp.tool()
def sample_metrics(name: str = "") -> dict[str, Any]:
    """L3 host metrics via SSH: cpu%/load / mem% / disk% / uptime / net rx·tx kbps.
    Empty name = all SSH hosts. No extra flags — network fields are always included when sampled.
    """
    return get_service().sample_metrics(name=name or None)


@mcp.tool()
def sample_cloud_metrics(cloud: str = "", name: str = "") -> dict[str, Any]:
    """Best-effort cloud vendor metrics for synced instances (no host agent).

    Aliyun: ECS DescribeInstanceMonitorData → cpu_used_pct (no memory %).
    Aliyun/Oracle/Tencent/GCP: vendor metrics when available (SSH-linked prefer host).
    """
    return get_service().sample_cloud_metrics(
        cloud=cloud.strip() or None,
        name=name.strip() or None,
    )


@mcp.tool()
def update_ssh(
    name: str,
    host: str = "",
    user: str = "",
    port: int = 0,
    os_name: str = "",
    password: str = "",
    key_file: str = "",
) -> dict[str, Any]:
    """Update SSH resource fields (name immutable). Optional password/key_file replace vault secret.
    Response is public summary only — secrets are never echoed.
    """
    kw: dict[str, Any] = {}
    if host.strip():
        kw["host"] = host.strip()
    if user.strip():
        kw["user"] = user.strip()
    if port > 0:
        kw["port"] = int(port)
    if os_name.strip():
        kw["os_name"] = os_name.strip()
    if password:
        kw["password"] = password
    if key_file.strip():
        kw["key_file"] = Path(key_file.strip())
    try:
        r = get_service().resources.update_ssh(name, **kw)
    except KeyError:
        return {"error": "resource_not_found", "name": name}
    except (ValueError, FileNotFoundError, OSError) as exc:
        return {"error": str(exc), "name": name}
    get_service().refresh_status()
    return r.summary()


@mcp.tool()
def autostart_status() -> dict[str, Any]:
    """Login autostart status (macOS LaunchAgent / Windows Startup folder)."""
    from grip import autostart

    return autostart.status()


@mcp.tool()
def set_autostart(enabled: bool) -> dict[str, Any]:
    """Enable or disable login autostart (LaunchAgent / Startup folder)."""
    from grip import autostart

    try:
        return autostart.enable() if enabled else autostart.disable()
    except FileNotFoundError as exc:
        return {"error": str(exc), "enabled": False}


@mcp.tool()
def sync_cloud(cloud: str) -> dict[str, Any]:
    """Sync one cloud inventory via bundled SDK: oracle|gcp|aliyun|tencent."""
    return get_service().sync_cloud(cloud)


@mcp.tool()
def sync_all() -> dict[str, Any]:
    """Sync all configured clouds; soft-fails per cloud."""
    return get_service().sync_all()


@mcp.tool()
def sync_oracle(profile: str = "") -> dict[str, Any]:
    """Deprecated alias for sync_cloud('oracle')."""
    _ = profile
    return get_service().sync_cloud("oracle")


@mcp.tool()
def export_audit(
    name: str = "",
    format: str = "jsonl",
    limit: int = 2000,
) -> dict[str, Any]:
    """Export redacted command-run audit trail (one device or all).

    format: jsonl|json|csv. Never includes vault/secrets. Use name="" for all devices.
    """
    out = get_service().export_audit(
        name=name.strip() or None,
        fmt=format,
        limit=limit,
    )
    # Keep MCP payload structured; body is the downloadable text.
    return out


@mcp.tool()
def reconcile_orphaned(name: str = "") -> dict[str, Any]:
    """Re-check orphaned (待核对) tasks via remote JSONL / process markers.

    Empty name = all devices. Resolves to success|failed when the detached job
    finished; stays orphaned while still alive or unknown.
    """
    return get_service().reconcile_orphaned(name=name.strip() or None)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
