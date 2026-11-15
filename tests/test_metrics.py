from __future__ import annotations

from datetime import datetime, timezone

from grip.metrics import (
    METRICS_HOST_UNREACHABLE,
    _remote_metrics_command,
    format_uptime,
    metrics_brief,
    parse_metrics_kv,
    sample_ssh_metrics,
)
from grip.resources import Resource
from grip.service import GripService


def _tcp_ok(monkeypatch) -> None:
    """Pretend SSH port is open so mocked run_ssh paths still exercise."""
    monkeypatch.setattr("grip.metrics.tcp_open", lambda *a, **k: True)


LINUX_SAMPLE = """
os_family=linux
cpu_used_pct=23
load1=0.42
mem_used_pct=37
mem_used_mb=3000
mem_total_mb=8192
disk_used_pct=55
disk_avail_gb=120
net_iface=eth0
net_rx_kbps=120
net_tx_kbps=40
uptime_sec=90061
"""

MAC_SAMPLE = """
os_family=macos
cpu_used_pct=41
load1=1.85
mem_used_pct=64
mem_used_mb=10240
mem_total_mb=16384
disk_used_pct=48
disk_avail_gb=400
uptime_sec=172800
"""

WINDOWS_SAMPLE = """
os_family=windows
cpu_used_pct=18
mem_used_pct=52
mem_used_mb=8320
mem_total_mb=16000
disk_used_pct=61
disk_avail_gb=180
net_iface=Ethernet
net_rx_kbps=120
net_tx_kbps=40
uptime_sec=86400
"""


def test_parse_linux_metrics() -> None:
    m = parse_metrics_kv(LINUX_SAMPLE)
    assert m["os_family"] == "linux"
    assert m["cpu_used_pct"] == 23
    assert m["load1"] == 0.42
    assert m["mem_used_pct"] == 37
    assert m["disk_used_pct"] == 55
    assert m["uptime_sec"] == 90061
    assert "CPU 23%" in metrics_brief(m)
    assert format_uptime(90061) == "1天 1时"


def test_parse_macos_metrics() -> None:
    m = parse_metrics_kv(MAC_SAMPLE)
    assert m["os_family"] == "macos"
    assert m["mem_total_mb"] == 16384
    assert format_uptime(172800) == "2天 0时"


def test_parse_windows_metrics() -> None:
    m = parse_metrics_kv(WINDOWS_SAMPLE)
    assert m["os_family"] == "windows"
    assert m["cpu_used_pct"] == 18
    assert m["mem_used_pct"] == 52
    assert m["disk_used_pct"] == 61
    assert m["net_rx_kbps"] == 120
    assert m["uptime_sec"] == 86400
    assert "load1" not in m
    assert "CPU 18%" in metrics_brief(m)
    assert format_uptime(86400) == "1天 0时"


def test_remote_metrics_command_unix_vs_windows() -> None:
    unix_cmd = _remote_metrics_command(windows=False)
    assert "GRIP_METRICS_EOF" in unix_cmd
    assert "/proc/loadavg" in unix_cmd
    # macOS path: ps CPU + 0.5s net window (not top -l 2, which stalls under load)
    assert "ps -A -o %cpu=" in unix_cmd
    assert "sleep 0.5" in unix_cmd
    assert "top -l 2" not in unix_cmd
    win_cmd = _remote_metrics_command(windows=True)
    assert "GRIP_METRICS_EOF" not in win_cmd
    assert "Get-CimInstance" in win_cmd
    assert "Win32_OperatingSystem" in win_cmd
    assert "'" not in win_cmd  # ssh_exec doubles single quotes


def test_metrics_timeout_policy() -> None:
    """Dead hosts fail via TCP preflight; script budget stays generous for SSH."""
    import inspect

    params = inspect.signature(sample_ssh_metrics).parameters
    assert params["timeout_sec"].default == 45
    assert params["connect_timeout_sec"].default == 10
    assert params["tcp_timeout_sec"].default == 2.0


def test_sample_metrics_persists(
    service: GripService, sample_key, monkeypatch
) -> None:
    service.resources.add_ssh(
        name="m1", host="127.0.0.1", user="u", key_file=sample_key, port=22
    )

    class Fake:
        ok = True
        metrics = parse_metrics_kv(LINUX_SAMPLE)
        detail = "ok"
        sampled_at = datetime.now(timezone.utc).isoformat()

    monkeypatch.setattr("grip.service.sample_ssh_metrics", lambda *a, **k: Fake())
    out = service.sample_metrics(name="m1")
    assert out["ok"] == 1
    r = service.resources.get("m1")
    assert r is not None
    assert r.meta["metrics"]["mem_used_pct"] == 37
    assert "metrics" in r.summary()["labels"]
    assert len(r.meta.get("metrics_series") or []) >= 1
    service.sample_metrics(name="m1")
    r2 = service.resources.get("m1")
    assert len(r2.meta.get("metrics_series") or []) >= 2


def test_metrics_tcp_fail_fast_skips_ssh(monkeypatch) -> None:
    """Dead host: cheap TCP fail — never touch SSH / hang on connect."""
    r = Resource(
        name="dead",
        kind="ssh",
        status="reachable",
        meta={"os": "macos", "host": "192.0.2.1", "user": "a", "port": 22},
        secret_id="ssh:dead",
    )
    monkeypatch.setattr("grip.metrics.tcp_open", lambda *a, **k: False)

    def boom(*_a, **_k):  # noqa: ANN001
        raise AssertionError("run_ssh must not be called when TCP is closed")

    monkeypatch.setattr("grip.metrics.run_ssh", boom)
    monkeypatch.setattr("grip.ssh_exec.run_ssh", boom)
    sample = sample_ssh_metrics(r, vault=None, tcp_timeout_sec=0.2)  # type: ignore[arg-type]
    assert sample.ok is False
    assert sample.detail == METRICS_HOST_UNREACHABLE
    assert sample.metrics == {}


def test_sample_metrics_marks_unreachable_on_tcp_fail(
    service: GripService, sample_key, monkeypatch
) -> None:
    service.resources.add_ssh(
        name="gone", host="192.0.2.9", user="u", key_file=sample_key, port=22
    )
    service.resources.set_status("gone", "reachable")
    last_ok = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    good = parse_metrics_kv(LINUX_SAMPLE)
    service.resources.apply_metrics("gone", good, last_ok, detail="ok")

    fail_at = datetime.now(timezone.utc).isoformat()

    class Dead:
        ok = False
        metrics: dict = {}
        detail = METRICS_HOST_UNREACHABLE
        sampled_at = fail_at

    monkeypatch.setattr("grip.service.sample_ssh_metrics", lambda *a, **k: Dead())
    out = service.sample_metrics(name="gone")
    assert out["ok"] == 0
    assert out["results"][0]["detail"] == METRICS_HOST_UNREACHABLE
    r = service.resources.get("gone")
    assert r is not None
    assert r.status == "unreachable"
    assert r.meta.get("metrics_detail") == METRICS_HOST_UNREACHABLE
    # Failure must not bump the last-success clock or wipe last good metrics.
    assert r.meta.get("metrics_at") == last_ok
    assert r.meta.get("metrics") == good
    assert r.meta.get("metrics_attempt_at") == fail_at
    series = r.meta.get("metrics_series") or []
    assert len(series) == 1
    assert series[0]["t"] == last_ok


def test_apply_metrics_records_disk_io_in_series(
    service: GripService, sample_key
) -> None:
    """Cloud vendor path needs disk_read/write_kbps in series for 盘IO sparks."""
    service.resources.add_ssh(
        name="diskio", host="127.0.0.1", user="u", key_file=sample_key, port=22
    )
    at = datetime.now(timezone.utc).isoformat()
    service.resources.apply_metrics(
        "diskio",
        {
            "cpu_used_pct": 1,
            "mem_used_pct": 7,
            "disk_read_kbps": 12,
            "disk_write_kbps": 640,
            "disk_iops_read": 0,
            "disk_iops_write": 2,
            "net_rx_kbps": 3,
            "net_tx_kbps": 4,
        },
        at,
        detail="ok",
    )
    series = (service.resources.get("diskio").meta.get("metrics_series") or [])
    assert series
    assert series[-1]["disk_read_kbps"] == 12
    assert series[-1]["disk_write_kbps"] == 640
    assert series[-1]["disk_iops_write"] == 2


def test_sample_metrics_success_updates_metrics_at(
    service: GripService, sample_key, monkeypatch
) -> None:
    service.resources.add_ssh(
        name="okhost", host="127.0.0.1", user="u", key_file=sample_key, port=22
    )
    old_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    service.resources.apply_metrics(
        "okhost", parse_metrics_kv(LINUX_SAMPLE), old_at, detail="ok"
    )
    new_at = datetime.now(timezone.utc).isoformat()

    class Ok:
        ok = True
        metrics = parse_metrics_kv(LINUX_SAMPLE)
        detail = "ok"
        sampled_at = new_at

    monkeypatch.setattr("grip.service.sample_ssh_metrics", lambda *a, **k: Ok())
    out = service.sample_metrics(name="okhost")
    assert out["ok"] == 1
    r = service.resources.get("okhost")
    assert r is not None
    assert r.meta.get("metrics_at") == new_at
    assert r.meta.get("metrics_detail") == "ok"


def test_sample_metrics_leaves_enrolled_status_alone(
    service: GripService, sample_key, monkeypatch
) -> None:
    """A metrics miss must not paint an enrolled device red while relay is up."""
    service.resources.add_ssh(
        name="win", host="192.168.5.120", user="u", key_file=sample_key, port=22
    )
    service.resources.set_status("win", "reachable")
    service.resources.patch_meta(
        "win",
        {"tunnel": {"enabled": True, "path": "relay", "relay_proven": True}},
    )

    class Dead:
        ok = False
        metrics: dict = {}
        detail = METRICS_HOST_UNREACHABLE
        sampled_at = datetime.now(timezone.utc).isoformat()

    monkeypatch.setattr("grip.service.sample_ssh_metrics", lambda *a, **k: Dead())
    service.sample_metrics(name="win")
    r = service.resources.get("win")
    assert r is not None
    assert r.status == "reachable"
    assert (r.meta.get("tunnel") or {}).get("relay_proven") is True
    assert r.meta.get("metrics_detail") == METRICS_HOST_UNREACHABLE

    class Ok:
        ok = True
        metrics = parse_metrics_kv(LINUX_SAMPLE)
        detail = "ok"
        sampled_at = datetime.now(timezone.utc).isoformat()

    service.resources.set_status("win", "unreachable")
    monkeypatch.setattr("grip.service.sample_ssh_metrics", lambda *a, **k: Ok())
    service.sample_metrics(name="win")
    r = service.resources.get("win")
    assert r is not None
    assert r.status == "unreachable"
    assert r.meta.get("metrics_detail") == "ok"


def test_sample_metrics_marks_reachable_again(
    service: GripService, sample_key, monkeypatch
) -> None:
    """After off-LAN unreachable, a successful sample flips status back to reachable."""
    service.resources.add_ssh(
        name="back", host="127.0.0.1", user="u", key_file=sample_key, port=22
    )
    service.resources.set_status("back", "unreachable")

    class Ok:
        ok = True
        metrics = parse_metrics_kv(LINUX_SAMPLE)
        detail = "ok"
        sampled_at = datetime.now(timezone.utc).isoformat()

    monkeypatch.setattr("grip.service.sample_ssh_metrics", lambda *a, **k: Ok())
    out = service.sample_metrics(name="back")
    assert out["ok"] == 1
    r = service.resources.get("back")
    assert r is not None
    assert r.status == "reachable"
    assert r.meta.get("metrics_at") == Ok.sampled_at
    series = r.meta.get("metrics_series") or []
    assert series
    assert series[-1].get("net_rx_kbps") == 120
    assert series[-1].get("net_tx_kbps") == 40


def test_sample_metrics_skips_when_busy(service: GripService, sample_key) -> None:
    service.resources.add_ssh(
        name="busy1", host="127.0.0.1", user="u", key_file=sample_key, port=22
    )
    assert service._metrics_lock.acquire(blocking=False)
    try:
        out = service.sample_metrics(name="busy1")
        assert out["status"] == "busy"
        assert out["detail"] == "metrics_in_flight"
        assert out["checked"] == 0
    finally:
        service._metrics_lock.release()


def test_sample_metrics_parallel_across_hosts(
    service: GripService, sample_key, monkeypatch
) -> None:
    """Three hosts each sleep 0.12s — parallel finish well under serial 0.36s."""
    import time

    for n in ("p1", "p2", "p3"):
        service.resources.add_ssh(
            name=n, host="127.0.0.1", user="u", key_file=sample_key, port=22
        )

    class Fake:
        ok = True
        metrics = parse_metrics_kv(LINUX_SAMPLE)
        detail = "ok"
        sampled_at = datetime.now(timezone.utc).isoformat()

    def slow(*_a, **_k):  # noqa: ANN001
        time.sleep(0.12)
        return Fake()

    monkeypatch.setattr("grip.service.sample_ssh_metrics", slow)
    monkeypatch.setattr(
        "grip.service.run_cloud_metrics_sample",
        lambda *a, **k: {
            "status": "success",
            "checked": 0,
            "ok": 0,
            "results": [],
        },
    )
    t0 = time.monotonic()
    out = service.sample_metrics()
    elapsed = time.monotonic() - t0
    assert out["ok"] == 3
    assert out["checked"] == 3
    assert {r["name"] for r in out["results"]} == {"p1", "p2", "p3"}
    # Serial would be ≥0.36s; parallel should land near one sleep (+overhead).
    assert elapsed < 0.30, f"expected parallel sample, took {elapsed:.3f}s"


def test_windows_metrics_via_mocked_ssh(monkeypatch) -> None:
    _tcp_ok(monkeypatch)
    r = Resource(
        name="win",
        kind="ssh",
        status="reachable",
        meta={"os": "windows", "host": "1.1.1.1", "user": "a", "port": 22},
        secret_id="ssh:win",
    )
    captured: dict[str, str] = {}

    class FakeResult:
        exit_code = 0
        stdout = WINDOWS_SAMPLE
        stderr = ""

    def fake_run(resource, vault, command, **kwargs):  # noqa: ANN001
        captured["command"] = command
        return FakeResult()

    monkeypatch.setattr("grip.metrics.run_ssh", fake_run)
    sample = sample_ssh_metrics(r, vault=None)  # type: ignore[arg-type]
    assert sample.ok is True
    assert sample.metrics["os_family"] == "windows"
    assert sample.metrics["cpu_used_pct"] == 18
    assert sample.metrics["mem_used_pct"] == 52
    assert sample.metrics["disk_used_pct"] == 61
    assert sample.metrics["net_tx_kbps"] == 40
    assert sample.metrics["uptime_sec"] == 86400
    assert sample.metrics["load1"] is None
    assert "Get-CimInstance" in captured["command"]
    assert "GRIP_METRICS_EOF" not in captured["command"]


def test_unix_metrics_still_uses_shell(monkeypatch) -> None:
    _tcp_ok(monkeypatch)
    r = Resource(
        name="ux",
        kind="ssh",
        status="reachable",
        meta={"os": "linux", "host": "1.1.1.1", "user": "a", "port": 22},
        secret_id="ssh:ux",
    )
    captured: dict[str, str] = {}

    class FakeResult:
        exit_code = 0
        stdout = LINUX_SAMPLE
        stderr = ""

    def fake_run(resource, vault, command, **kwargs):  # noqa: ANN001
        captured["command"] = command
        return FakeResult()

    monkeypatch.setattr("grip.metrics.run_ssh", fake_run)
    sample = sample_ssh_metrics(r, vault=None)  # type: ignore[arg-type]
    assert sample.ok is True
    assert sample.metrics["os_family"] == "linux"
    assert sample.metrics["load1"] == 0.42
    assert "GRIP_METRICS_EOF" in captured["command"]


def test_auto_detect_windows_persists_meta(monkeypatch) -> None:
    """Default meta.os=unix on a Windows host: probe then use METRICS_PS1."""
    _tcp_ok(monkeypatch)
    from grip.ssh_exec import ExecResult

    r = Resource(
        name="win-auto",
        kind="ssh",
        status="reachable",
        meta={
            "os": "unix",
            "host": "1.1.1.1",
            "user": "a",
            "port": 22,
            "keep_me": True,
        },
        secret_id="ssh:win-auto",
    )
    patched: dict[str, object] = {}

    class FakeStore:
        def patch_meta(self, name: str, patch: dict) -> None:
            patched["name"] = name
            patched["patch"] = dict(patch)
            r.meta.update(patch)

    calls: list[str] = []

    def fake_run(resource, vault, command, **kwargs):  # noqa: ANN001
        calls.append(command)
        os_wrap = kwargs.get("os_wrap", True)
        if "uname" in command:
            assert os_wrap is False
            return ExecResult(1, "", "uname: not found")
        if "powershell" in command.lower() and "$env:OS" in command:
            assert os_wrap is False
            return ExecResult(0, "Windows_NT\n", "")
        # metrics script
        assert "Get-CimInstance" in command
        return ExecResult(0, WINDOWS_SAMPLE, "")

    monkeypatch.setattr("grip.metrics.run_ssh", fake_run)
    monkeypatch.setattr("grip.ssh_exec.run_ssh", fake_run)
    sample = sample_ssh_metrics(r, vault=None, persist_meta=FakeStore())  # type: ignore[arg-type]
    assert sample.ok is True
    assert sample.metrics["os_family"] == "windows"
    assert r.meta["os"] == "windows"
    assert r.meta["keep_me"] is True
    assert patched["name"] == "win-auto"
    assert patched["patch"] == {"os": "windows"}
    assert any("Get-CimInstance" in c for c in calls)


def test_auto_detect_linux_persists_meta(monkeypatch) -> None:
    _tcp_ok(monkeypatch)
    from grip.ssh_exec import ExecResult

    r = Resource(
        name="lin-auto",
        kind="ssh",
        status="reachable",
        meta={"os": "unix", "host": "1.1.1.1", "user": "a", "port": 22},
        secret_id="ssh:lin-auto",
    )
    patched: list[dict] = []

    class FakeStore:
        def patch_meta(self, name: str, patch: dict) -> None:
            patched.append(patch)
            r.meta.update(patch)

    def fake_run(resource, vault, command, **kwargs):  # noqa: ANN001
        if "uname" in command:
            return ExecResult(0, "Linux\n", "")
        assert "GRIP_METRICS_EOF" in command
        return ExecResult(0, LINUX_SAMPLE, "")

    monkeypatch.setattr("grip.metrics.run_ssh", fake_run)
    monkeypatch.setattr("grip.ssh_exec.run_ssh", fake_run)
    sample = sample_ssh_metrics(r, vault=None, persist_meta=FakeStore())  # type: ignore[arg-type]
    assert sample.ok is True
    assert r.meta["os"] == "linux"
    assert patched == [{"os": "linux"}]


def test_detect_failure_leaves_meta_and_fails_gracefully(monkeypatch) -> None:
    _tcp_ok(monkeypatch)
    from grip.ssh_exec import ExecResult

    r = Resource(
        name="bad",
        kind="ssh",
        status="reachable",
        meta={"os": "unix", "host": "1.1.1.1", "user": "a", "port": 22},
        secret_id="ssh:bad",
    )

    class FakeStore:
        def patch_meta(self, name: str, patch: dict) -> None:  # noqa: ARG002
            raise AssertionError("must not persist on failed detect")

    def fake_run(resource, vault, command, **kwargs):  # noqa: ANN001
        return ExecResult(1, "", "connection refused")

    monkeypatch.setattr("grip.metrics.run_ssh", fake_run)
    monkeypatch.setattr("grip.ssh_exec.run_ssh", fake_run)
    sample = sample_ssh_metrics(r, vault=None, persist_meta=FakeStore())  # type: ignore[arg-type]
    assert sample.ok is False
    assert r.meta["os"] == "unix"
