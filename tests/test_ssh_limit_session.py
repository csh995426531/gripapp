from __future__ import annotations

import threading
import time
from pathlib import Path

from grip.ssh_exec import ExecResult, run_ssh
from grip.ssh_limit import HostSSHGate, max_per_host
from grip.ssh_session import control_path_for, key_ssh_mux_opts
from grip.service import GripService


def test_max_per_host_env(monkeypatch) -> None:
    monkeypatch.setenv("GRIP_SSH_MAX_PER_HOST", "5")
    assert max_per_host() == 5
    monkeypatch.setenv("GRIP_SSH_MAX_PER_HOST", "99")
    assert max_per_host() == 16


def test_host_gate_queues() -> None:
    gate = HostSSHGate(limit=1)
    phase = threading.Event()
    entered_second = threading.Event()
    order: list[str] = []

    def first() -> None:
        with gate.slot("box"):
            order.append("a-in")
            phase.wait(timeout=2)
            order.append("a-out")

    def second() -> None:
        with gate.slot("box", timeout_sec=2):
            order.append("b-in")
            entered_second.set()
            order.append("b-out")

    t1 = threading.Thread(target=first)
    t2 = threading.Thread(target=second)
    t1.start()
    time.sleep(0.05)
    t2.start()
    time.sleep(0.05)
    assert "b-in" not in order  # queued behind a
    phase.set()
    t1.join(timeout=2)
    t2.join(timeout=2)
    assert order == ["a-in", "a-out", "b-in", "b-out"]
    assert entered_second.is_set()


def test_host_gate_timeout() -> None:
    gate = HostSSHGate(limit=1)
    with gate.slot("h"):
        try:
            with gate.slot("h", timeout_sec=0.2):
                raise AssertionError("should not acquire")
        except TimeoutError as e:
            assert "ssh_host_busy" in str(e)


def test_key_mux_opts(service: GripService, sample_key: Path) -> None:
    r = service.resources.add_ssh(
        name="mux", host="10.0.0.9", user="u", key_file=sample_key
    )
    opts = key_ssh_mux_opts(r)
    assert "ControlMaster=auto" in opts
    path = control_path_for(r)
    assert "mux" in path or path.endswith(".sock")


def test_run_ssh_uses_gate(
    service: GripService, sample_key: Path, monkeypatch
) -> None:
    r = service.resources.add_ssh(
        name="g1", host="127.0.0.1", user="u", key_file=sample_key
    )
    seen: list[str] = []

    def fake_key(*a, **k):
        seen.append("run")
        return ExecResult(0, "ok", "")

    monkeypatch.setattr("grip.ssh_exec._run_ssh_key", fake_key)
    # Force key path
    service.resources.patch_meta("g1", {"auth": "key"})
    r2 = service.resources.get("g1")
    assert r2 is not None
    out = run_ssh(r2, service.vault, "echo ok", timeout_sec=5)
    assert out.exit_code == 0
    assert seen == ["run"]
