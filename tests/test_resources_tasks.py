from __future__ import annotations

from pathlib import Path

from grip.redact import assert_no_secrets
from grip.service import GripService


def test_add_ssh_stores_secret_separately(service: GripService, sample_key: Path) -> None:
    r = service.resources.add_ssh(
        name="dev1", host="127.0.0.1", user="ubuntu", key_file=sample_key
    )
    summary = r.summary()
    assert_no_secrets(summary)
    assert "private" not in str(summary).lower()
    assert service.vault.has("ssh:dev1")
    # DB meta must not contain key material
    assert "BEGIN" not in str(r.meta)


def test_resource_summary_safe_for_mcp(service: GripService, sample_key: Path) -> None:
    service.resources.add_ssh(
        name="dev1", host="10.0.0.2", user="root", key_file=sample_key
    )
    payload = service.list_resources()
    assert_no_secrets(payload)


def test_task_lifecycle_and_write_denied(service: GripService, sample_key: Path, monkeypatch) -> None:
    service.resources.add_ssh(
        name="dev1", host="127.0.0.1", user="ubuntu", key_file=sample_key
    )

    denied = service.run_command("dev1", "rm -rf /tmp/x")
    assert denied["status"] == "denied"
    t = service.tasks.get(denied["task_id"])
    assert t is not None
    assert t.status == "denied"

    # Force executor to avoid real ssh
    def fake_run(resource, vault, command, timeout_sec=60):
        from grip.ssh_exec import ExecResult

        return ExecResult(0, "Linux\n-----BEGIN RSA PRIVATE KEY-----\nABC\n-----END RSA PRIVATE KEY-----\n", "")

    monkeypatch.setattr("grip.service.run_ssh", fake_run)
    out = service.run_command("dev1", "uname -a", background=False)
    assert out["status"] == "success"
    task = service.get_task(out["task_id"])["task"]
    assert_no_secrets(task)
    assert "BEGIN RSA PRIVATE KEY" not in (task.get("summary") or "")
    assert "***REDACTED_PEM***" in (task.get("summary") or "")


def test_write_allowed_with_confirm(service: GripService, sample_key: Path, monkeypatch) -> None:
    service.resources.add_ssh(
        name="dev1", host="127.0.0.1", user="ubuntu", key_file=sample_key
    )

    def fake_run(resource, vault, command, timeout_sec=60):
        from grip.ssh_exec import ExecResult

        return ExecResult(0, "ok", "")

    monkeypatch.setattr("grip.service.run_ssh", fake_run)
    out = service.run_command(
        "dev1", "rm -rf /tmp/x", confirm_write=True, background=False
    )
    assert out["status"] == "success"


def test_status_snapshot_redacted(service: GripService, sample_key: Path, monkeypatch) -> None:
    service.resources.add_ssh(
        name="dev1", host="127.0.0.1", user="ubuntu", key_file=sample_key
    )

    def fake_run(resource, vault, command, timeout_sec=60):
        from grip.ssh_exec import ExecResult

        return ExecResult(0, "ok", "")

    monkeypatch.setattr("grip.service.run_ssh", fake_run)
    service.run_command("dev1", "echo hi", background=False)
    from grip.status import build_snapshot

    snap = build_snapshot(service.resources, service.tasks)
    assert_no_secrets(snap)
    assert "by_resource" in snap


def test_list_order_drag_persist(service: GripService, sample_key: Path) -> None:
    """Default is name order; set_list_order sticks across list() / board()."""
    service.resources.add_ssh(
        name="alpha", host="127.0.0.1", user="u", key_file=sample_key
    )
    service.resources.add_ssh(
        name="bravo", host="127.0.0.1", user="u", key_file=sample_key
    )
    service.resources.add_ssh(
        name="charlie", host="127.0.0.1", user="u", key_file=sample_key
    )
    names = [r.name for r in service.resources.list(kind="ssh")]
    assert names == ["alpha", "bravo", "charlie"]

    out = service.reorder_resources(["charlie", "alpha", "bravo"])
    assert out["ok"] is True
    assert out["names"][:3] == ["charlie", "alpha", "bravo"]
    assert [r.name for r in service.resources.list(kind="ssh")] == [
        "charlie",
        "alpha",
        "bravo",
    ]
    board_names = [r["name"] for r in service.board()["resources"] if r["kind"] == "ssh"]
    assert board_names == ["charlie", "alpha", "bravo"]

    service.resources.delete("alpha")
    assert "alpha" not in service.resources.list_order()
    assert [r.name for r in service.resources.list(kind="ssh")] == [
        "charlie",
        "bravo",
    ]
