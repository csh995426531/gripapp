from __future__ import annotations

import socket
import threading
import time
from pathlib import Path

from grip.probes import normalize_probe, wait_probe
from grip.service import GripService


def test_normalize_probe() -> None:
    p = normalize_probe({"type": "tcp", "target": "127.0.0.1:9", "timeout_sec": 3})
    assert p["type"] == "tcp"


def test_tcp_probe_success() -> None:
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    srv.listen(1)

    def accept():
        conn, _ = srv.accept()
        conn.close()

    threading.Thread(target=accept, daemon=True).start()
    ok, msg = wait_probe(
        {"type": "tcp", "target": f"127.0.0.1:{port}", "timeout_sec": 5}
    )
    srv.close()
    assert ok, msg


def test_run_with_probe_and_events(
    service: GripService, sample_key: Path, monkeypatch
) -> None:
    service.resources.add_ssh(
        name="app", host="127.0.0.1", user="ubuntu", key_file=sample_key
    )

    def fake_run(resource, vault, command, timeout_sec=60):
        from grip.ssh_exec import ExecResult

        return ExecResult(0, "started", "")

    monkeypatch.setattr("grip.service.run_ssh", fake_run)

    # local tcp server for probe
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    srv.listen(1)

    def accept_loop():
        try:
            conn, _ = srv.accept()
            conn.close()
        except OSError:
            pass

    threading.Thread(target=accept_loop, daemon=True).start()

    out = service.run_command(
        "app",
        "echo start",
        background=False,
        await_probe={"type": "tcp", "target": f"127.0.0.1:{port}", "timeout_sec": 5},
    )
    srv.close()
    assert out["status"] == "success"
    task = service.get_task(out["task_id"])["task"]
    assert task["phase"] == "done"
    kinds = [e["kind"] for e in task["events"]]
    assert "created" in kinds
    assert "running" in kinds
    assert "probing" in kinds
    assert "success" in kinds


def test_windows_meta_stored(service: GripService, sample_key: Path) -> None:
    r = service.resources.add_ssh(
        name="win1",
        host="10.0.0.8",
        user="Administrator",
        key_file=sample_key,
        os_name="windows",
    )
    assert r.meta["os"] == "windows"
