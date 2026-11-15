from __future__ import annotations

from pathlib import Path

from grip.remote_agent import (
    build_detached_launch,
    parse_agent_markers,
    remote_agent_enabled,
)
from grip.service import GripService
from grip.ssh_exec import ExecResult


def test_parse_agent_markers() -> None:
    import base64

    payload = base64.b64encode(b"hello-out\n").decode("ascii")
    out = (
        "__GRIP_STARTED__ abc-123 4242\n"
        "hello\n"
        "__GRIP_FINISHED__ abc-123 0\n"
        f"__GRIP_OUT_B64__ abc-123 10 {payload}\n"
    )
    started, tid, pid, finished, ec, tail = parse_agent_markers(out)
    assert started and finished
    assert tid == "abc-123"
    assert pid == 4242
    assert ec == 0
    assert "hello-out" in tail


def test_unix_launch_contains_nohup_and_task_id(
    service: GripService, sample_key: Path
) -> None:
    r = service.resources.add_ssh(
        name="u1", host="10.0.0.1", user="u", key_file=sample_key, os_name="linux"
    )
    script = build_detached_launch(
        r, task_id="tid-1", command="echo hi", wait_timeout_sec=10
    )
    assert "nohup" in script
    assert "GRIP_TASK_ID" in script
    assert "tid-1" in script
    assert "__GRIP_STARTED__" in script
    assert "__GRIP_FINISHED__" in script


def test_macos_launch_also_uses_nohup(
    service: GripService, sample_key: Path
) -> None:
    r = service.resources.add_ssh(
        name="mac1", host="10.0.0.3", user="u", key_file=sample_key, os_name="macos"
    )
    script = build_detached_launch(
        r, task_id="tid-mac", command="echo hi", wait_timeout_sec=10
    )
    assert "nohup" in script
    assert "Start-Process" not in script


def test_windows_launch_uses_start_process(
    service: GripService, sample_key: Path
) -> None:
    r = service.resources.add_ssh(
        name="w1",
        host="10.0.0.2",
        user="Administrator",
        key_file=sample_key,
        os_name="windows",
    )
    script = build_detached_launch(
        r, task_id="tid-w", command="Write-Output hi", wait_timeout_sec=10
    )
    assert "Start-Process" in script
    assert "GRIP_TASK_ID" in script
    assert "__GRIP_STARTED__" in script
    assert "-EncodedCommand" in script
    # Nested JSON inside "..." breaks PowerShell; start line must use concat.
    assert "\"{\"id\"" not in script
    assert "'{\"id\":\"'+$Tid" in script or "'{\"id\":\"'+$Tid+'\"'" in script


def test_worker_orphaned_when_started_then_no_finish(
    service: GripService, sample_key: Path, monkeypatch
) -> None:
    monkeypatch.setenv("GRIP_REMOTE_AGENT", "1")
    assert remote_agent_enabled()
    service.resources.add_ssh(
        name="box", host="127.0.0.1", user="u", key_file=sample_key, os_name="linux"
    )
    service.resources.set_status("box", "reachable")

    def fake_run(resource, vault, command, timeout_sec=60, **kwargs):
        # Launcher saw start but wait window ended without finish
        return ExecResult(0, "__GRIP_STARTED__ fake-id 99\n", "")

    monkeypatch.setattr("grip.service.run_ssh", fake_run)

    def fake_rec(*a, **k):
        from grip.remote_agent import ReconcileResult

        return ReconcileResult(False, False, None, False, "UNKNOWN")

    monkeypatch.setattr("grip.service.reconcile_task_on_host", fake_rec)
    # task id is generated — markers use whatever; parse uses stdout id
    # Worker parses STARTED regardless of id match
    out = service.run_command("box", "sleep 1", background=False)
    assert out["status"] == "orphaned"
    t = service.tasks.get(out["task_id"])
    assert t is not None
    assert t.status == "orphaned"


def test_worker_success_on_finished_marker(
    service: GripService, sample_key: Path, monkeypatch
) -> None:
    monkeypatch.setenv("GRIP_REMOTE_AGENT", "1")
    service.resources.add_ssh(
        name="box2", host="127.0.0.1", user="u", key_file=sample_key, os_name="linux"
    )
    service.resources.set_status("box2", "reachable")

    def fake_run(resource, vault, command, timeout_sec=60, **kwargs):
        return ExecResult(
            0,
            "__GRIP_STARTED__ x 1\n__GRIP_FINISHED__ x 0\n",
            "",
        )

    monkeypatch.setattr("grip.service.run_ssh", fake_run)
    out = service.run_command("box2", "echo hi", background=False)
    assert out["status"] == "success"


def test_transport_error_before_start_fails(
    service: GripService, sample_key: Path, monkeypatch
) -> None:
    monkeypatch.setenv("GRIP_REMOTE_AGENT", "1")
    service.resources.add_ssh(
        name="box3", host="127.0.0.1", user="u", key_file=sample_key, os_name="linux"
    )
    service.resources.set_status("box3", "reachable")

    def fake_run(resource, vault, command, timeout_sec=60, **kwargs):
        raise TimeoutError("timed out")

    monkeypatch.setattr("grip.service.run_ssh", fake_run)
    # Without seeing STARTED, should be failed not orphaned
    out = service.run_command("box3", "echo hi", background=False)
    assert out["status"] == "failed"


def test_reconcile_orphaned_finishes_from_jsonl(
    service: GripService, sample_key: Path, monkeypatch
) -> None:
    monkeypatch.setenv("GRIP_REMOTE_AGENT", "1")
    service.resources.add_ssh(
        name="box4", host="127.0.0.1", user="u", key_file=sample_key, os_name="linux"
    )
    service.resources.set_status("box4", "reachable")
    task = service.tasks.create(
        resource_name="box4", action="run_command", command="sleep 1"
    )
    service.tasks.set_running(task.id)
    service.tasks.set_orphaned(task.id, detail="test")

    def fake_rec(resource, vault, task_id, **kwargs):
        from grip.remote_agent import ReconcileResult

        return ReconcileResult(True, True, 0, False, "FINISHED 0")

    monkeypatch.setattr("grip.service.reconcile_task_on_host", fake_rec)
    out = service.reconcile_orphaned(name="box4")
    assert out["checked"] == 1
    assert out["results"][0]["status"] == "success"
    t = service.tasks.get(task.id)
    assert t is not None and t.status == "success"


def test_reconcile_unknown_closes_gone_task(
    service: GripService, sample_key: Path, monkeypatch
) -> None:
    service.resources.add_ssh(
        name="box5", host="127.0.0.1", user="u", key_file=sample_key, os_name="linux"
    )
    task = service.tasks.create(
        resource_name="box5", action="run_command", command="echo hi"
    )
    service.tasks.set_running(task.id)
    service.tasks.set_orphaned(task.id, detail="test")

    def fake_rec(resource, vault, task_id, **kwargs):
        from grip.remote_agent import ReconcileResult

        return ReconcileResult(False, False, None, False, "UNKNOWN")

    monkeypatch.setattr("grip.service.reconcile_task_on_host", fake_rec)
    out = service.reconcile_orphaned(name="box5")
    assert out["results"][0]["status"] == "failed"
    saved = service.tasks.get(task.id)
    assert saved is not None and saved.status == "failed"


def test_reconcile_keeps_orphaned_when_host_cannot_be_checked(
    service: GripService, sample_key: Path, monkeypatch
) -> None:
    service.resources.add_ssh(
        name="box6", host="127.0.0.1", user="u", key_file=sample_key, os_name="linux"
    )
    task = service.tasks.create(
        resource_name="box6", action="run_command", command="echo hi"
    )
    service.tasks.set_running(task.id)
    service.tasks.set_orphaned(task.id, detail="test")

    def fake_rec(resource, vault, task_id, **kwargs):
        from grip.remote_agent import ReconcileResult

        return ReconcileResult(False, False, None, False, "ssh_timeout")

    monkeypatch.setattr("grip.service.reconcile_task_on_host", fake_rec)
    out = service.reconcile_orphaned(name="box6")
    assert out["results"][0]["status"] == "orphaned"
    saved = service.tasks.get(task.id)
    assert saved is not None and saved.status == "orphaned"
