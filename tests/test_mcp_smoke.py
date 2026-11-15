from __future__ import annotations

import asyncio


def test_mcp_lists_expected_tools() -> None:
    from grip.mcp_server import mcp

    async def _list():
        return await mcp.list_tools()

    tools = asyncio.run(_list())
    names = sorted(t.name for t in tools)
    assert "list_resources" in names
    assert "run_command" in names
    assert "reconcile_orphaned" in names
    assert "sync_cloud" in names
    assert "sync_all" in names
    assert "list_tasks" in names
    assert "get_task" in names
    assert "board" in names
    assert "sample_metrics" in names
    assert "sample_cloud_metrics" in names
    assert "update_ssh" in names
    assert "autostart_status" in names
    assert "set_autostart" in names
    assert "export_audit" in names
    # Aligned-agent policy surfaces via MCP initialize instructions
    instr = getattr(mcp, "instructions", None) or ""
    assert "~/.grip" in instr or "GRIP_HOME" in instr
    assert "vault" in instr.lower() or "secret" in instr.lower()
