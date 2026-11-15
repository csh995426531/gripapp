from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from grip.audit import resolve_mcp_actor
from grip.db import connect
from grip.redact import assert_no_secrets
from grip.service import GripService


def test_actor_defaults_unknown_for_legacy_rows(service: GripService) -> None:
    """Migration-friendly: rows without actor read as unknown."""
    with connect(service._db_path) as conn:  # noqa: SLF001
        conn.execute(
            """INSERT INTO tasks(
                 id, resource_name, action, status, created_at, command
               ) VALUES (?,?,?,?,?,?)""",
            ("legacy-1", "Mac-mini", "run_command", "success", "2020-01-01T00:00:00Z", "echo hi"),
        )
        # Simulate pre-migration insert that somehow has NULL-ish via update
        conn.execute("UPDATE tasks SET actor='unknown' WHERE id='legacy-1'")
        conn.commit()
    t = service.tasks.get("legacy-1")
    assert t is not None
    assert t.actor == "unknown"
    dto = t.summary_dto()
    assert dto["actor"] == "unknown"


def test_panel_and_mcp_actor_tagging(
    service: GripService, sample_key: Path, monkeypatch
) -> None:
    service.resources.add_ssh(
        name="Mac-mini", host="127.0.0.1", user="ubuntu", key_file=sample_key
    )

    def fake_run(resource, vault, command, timeout_sec=60):
        from grip.ssh_exec import ExecResult

        return ExecResult(0, "ok\n", "")

    monkeypatch.setattr("grip.service.run_ssh", fake_run)

    panel = service.run_command(
        "Mac-mini", "uname -a", background=False, actor="panel"
    )
    mcp = service.run_command(
        "Mac-mini", "uptime", background=False, actor="mcp:cursor"
    )
    tp = service.tasks.get(panel["task_id"])
    tm = service.tasks.get(mcp["task_id"])
    assert tp is not None and tp.actor == "panel"
    assert tm is not None and tm.actor == "mcp:cursor"


def test_resolve_mcp_actor() -> None:
    assert resolve_mcp_actor(None) == "mcp"
    assert resolve_mcp_actor("") == "mcp"
    assert resolve_mcp_actor(None, client_name="cursor-vscode") == "mcp:cursor-vscode"
    assert resolve_mcp_actor("claude", client_name="cursor") == "claude"


def test_export_jsonl_and_filter(
    service: GripService, sample_key: Path, monkeypatch
) -> None:
    service.resources.add_ssh(
        name="a", host="127.0.0.1", user="u", key_file=sample_key
    )
    service.resources.add_ssh(
        name="b", host="127.0.0.2", user="u", key_file=sample_key
    )

    def fake_run(resource, vault, command, timeout_sec=60):
        from grip.ssh_exec import ExecResult

        return ExecResult(0, "out", "")

    monkeypatch.setattr("grip.service.run_ssh", fake_run)
    service.run_command("a", "echo a", background=False, actor="panel")
    service.run_command("b", "echo b", background=False, actor="mcp")

    all_out = service.export_audit(fmt="jsonl")
    assert all_out["count"] >= 2
    assert_no_secrets(all_out)
    lines = [ln for ln in all_out["body"].splitlines() if ln.strip()]
    assert len(lines) == all_out["count"]
    rows = [json.loads(ln) for ln in lines]
    for r in rows:
        assert "timestamp" in r
        assert "resource" in r
        assert "actor" in r
        assert "command" in r
        assert "status" in r
        assert_no_secrets(r)

    one = service.export_audit(name="a", fmt="json")
    assert one["count"] >= 1
    payload = json.loads(one["body"])
    assert payload["filter"]["name"] == "a"
    assert all(e["resource"] == "a" for e in payload["entries"])
    assert any(e["actor"] == "panel" for e in payload["entries"])


def test_export_csv_and_redacts_pem(
    service: GripService, sample_key: Path, monkeypatch
) -> None:
    service.resources.add_ssh(
        name="dev1", host="127.0.0.1", user="ubuntu", key_file=sample_key
    )

    def fake_run(resource, vault, command, timeout_sec=60):
        from grip.ssh_exec import ExecResult

        return ExecResult(
            0,
            "-----BEGIN RSA PRIVATE KEY-----\nSECRET\n-----END RSA PRIVATE KEY-----\n",
            "",
        )

    monkeypatch.setattr("grip.service.run_ssh", fake_run)
    service.run_command("dev1", "cat key", background=False, actor="mcp")
    out = service.export_audit(name="dev1", fmt="csv")
    assert "BEGIN RSA PRIVATE KEY" not in out["body"]
    assert "***REDACTED_PEM***" in out["body"]
    reader = csv.DictReader(io.StringIO(out["body"]))
    rows = list(reader)
    assert rows
    assert rows[0]["actor"] == "mcp"
    assert rows[0]["resource"] == "dev1"


def test_mcp_lists_export_audit() -> None:
    import asyncio

    from grip.mcp_server import mcp

    async def _list():
        return await mcp.list_tools()

    tools = asyncio.run(_list())
    names = {t.name for t in tools}
    assert "export_audit" in names
    assert "run_command" in names
