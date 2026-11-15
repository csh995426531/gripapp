from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from grip.metrics import METRICS_HOST_UNREACHABLE
from grip.service import GripService
from grip.ssh_exec import ExecResult


def test_run_command_refuses_unreachable_status(
    service: GripService, sample_key: Path, monkeypatch
) -> None:
    service.resources.add_ssh(
        name="off", host="192.0.2.1", user="u", key_file=sample_key
    )
    service.resources.set_status("off", "unreachable")
    monkeypatch.setattr("grip.service.tcp_open", lambda *a, **k: False)

    called = {"n": 0}

    def boom(*a, **k):
        called["n"] += 1
        raise AssertionError("run_ssh must not be called")

    monkeypatch.setattr("grip.service.run_ssh", boom)
    out = service.run_command("off", "echo hi", background=False)
    assert out["status"] == "failed"
    assert METRICS_HOST_UNREACHABLE in (out.get("error") or "")
    assert called["n"] == 0
    t = service.tasks.get(out["task_id"])
    assert t is not None
    assert t.status == "failed"
    assert t.error == METRICS_HOST_UNREACHABLE


def test_run_command_tcp_fail_fast(
    service: GripService, sample_key: Path, monkeypatch
) -> None:
    service.resources.add_ssh(
        name="dead", host="192.0.2.2", user="u", key_file=sample_key
    )
    service.resources.set_status("dead", "reachable")
    monkeypatch.setattr("grip.service.tcp_open", lambda *a, **k: False)
    monkeypatch.setattr(
        "grip.service.run_ssh",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no ssh")),
    )
    out = service.run_command("dead", "uptime", background=False)
    assert out["status"] == "failed"
    assert out["error"] == METRICS_HOST_UNREACHABLE
    r = service.resources.get("dead")
    assert r is not None
    assert r.status == "unreachable"
    t = service.tasks.get(out["task_id"])
    assert t is not None
    assert t.status == "failed"


def test_run_command_ok_when_reachable(
    service: GripService, sample_key: Path, monkeypatch
) -> None:
    service.resources.add_ssh(
        name="ok", host="127.0.0.1", user="u", key_file=sample_key
    )
    service.resources.set_status("ok", "reachable")
    monkeypatch.setattr(
        "grip.service.run_ssh",
        lambda *a, **k: ExecResult(0, "hi\n", ""),
    )
    out = service.run_command("ok", "echo hi", background=False)
    assert out["status"] == "success"


def test_reap_stale_running(service: GripService, sample_key: Path) -> None:
    service.resources.add_ssh(
        name="x", host="127.0.0.1", user="u", key_file=sample_key
    )
    task = service.tasks.create(
        resource_name="x", action="run_command", command="sleep 999"
    )
    service.tasks.set_running(task.id)
    old = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
    with service.tasks._conn() as conn:
        conn.execute(
            "UPDATE tasks SET last_heartbeat_at=? WHERE id=?",
            (old, task.id),
        )
        conn.commit()
    reaped = service.tasks.reap_stale_running(max_age_sec=90)
    assert task.id in reaped
    t = service.tasks.get(task.id)
    assert t is not None
    assert t.status == "failed"
    assert t.error == "stale_timeout"


def test_refresh_status_reaps_stale(service: GripService, sample_key: Path) -> None:
    service.resources.add_ssh(
        name="y", host="127.0.0.1", user="u", key_file=sample_key
    )
    task = service.tasks.create(
        resource_name="y", action="run_command", command="hang"
    )
    service.tasks.set_running(task.id)
    old = (datetime.now(timezone.utc) - timedelta(seconds=200)).isoformat()
    with service.tasks._conn() as conn:
        conn.execute(
            "UPDATE tasks SET last_heartbeat_at=? WHERE id=?",
            (old, task.id),
        )
        conn.commit()
    service.refresh_status()
    t = service.tasks.get(task.id)
    assert t is not None
    assert t.status == "failed"
    assert t.error == "stale_timeout"
