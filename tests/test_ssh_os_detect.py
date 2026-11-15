from __future__ import annotations

from grip.resources import Resource
from grip.service import GripService
from grip.ssh_exec import (
    ExecResult,
    detect_remote_os,
    needs_os_probe,
    normalize_detected_os,
    windows_powershell_remote,
)


def test_windows_powershell_uses_encoded_command() -> None:
    # Avoid -Command + shell quoting (breaks metrics PS1 on Win OpenSSH).
    remote = windows_powershell_remote('$x="hi"; Write-Output $x')
    assert remote.startswith("powershell -NoProfile -NonInteractive -EncodedCommand ")
    assert "'" not in remote
    assert '"' not in remote.split("EncodedCommand ", 1)[1]


def test_needs_os_probe_ambiguous_default() -> None:
    assert needs_os_probe(None) is True
    assert needs_os_probe("") is True
    assert needs_os_probe("unix") is True
    assert needs_os_probe("windows") is False
    assert needs_os_probe("linux") is False
    assert needs_os_probe("macos") is False
    assert needs_os_probe("darwin") is False


def test_normalize_detected_os() -> None:
    assert normalize_detected_os("Linux") == "linux"
    assert normalize_detected_os("Darwin") == "macos"
    assert normalize_detected_os("Windows_NT") == "windows"
    assert normalize_detected_os("FreeBSD") == "unix"
    assert normalize_detected_os("") is None
    assert normalize_detected_os(None) is None


def test_detect_remote_os_windows(monkeypatch) -> None:
    r = Resource(
        name="w",
        kind="ssh",
        status="reachable",
        meta={"os": "unix", "host": "h", "user": "u", "port": 22},
        secret_id="ssh:w",
    )

    def fake_run(resource, vault, command, **kwargs):  # noqa: ANN001
        assert kwargs.get("os_wrap") is False
        if "uname" in command:
            return ExecResult(127, "", "not found")
        return ExecResult(0, "Windows_NT\n", "")

    monkeypatch.setattr("grip.ssh_exec.run_ssh", fake_run)
    assert detect_remote_os(r, vault=None) == "windows"  # type: ignore[arg-type]


def test_detect_remote_os_darwin(monkeypatch) -> None:
    r = Resource(
        name="m",
        kind="ssh",
        status="reachable",
        meta={"os": "unix", "host": "h", "user": "u", "port": 22},
        secret_id="ssh:m",
    )

    def fake_run(resource, vault, command, **kwargs):  # noqa: ANN001
        return ExecResult(0, "Darwin\n", "")

    monkeypatch.setattr("grip.ssh_exec.run_ssh", fake_run)
    assert detect_remote_os(r, vault=None) == "macos"  # type: ignore[arg-type]


def test_maybe_detect_ssh_os_persists(
    service: GripService, sample_key, monkeypatch
) -> None:
    service.resources.add_ssh(
        name="auto",
        host="127.0.0.1",
        user="u",
        key_file=sample_key,
        os_name="unix",
    )

    def fake_run(resource, vault, command, **kwargs):  # noqa: ANN001
        return ExecResult(0, "Linux\n", "")

    monkeypatch.setattr("grip.ssh_exec.run_ssh", fake_run)
    assert service.maybe_detect_ssh_os("auto") == "linux"
    r = service.resources.get("auto")
    assert r is not None
    assert r.meta["os"] == "linux"
    assert r.meta["host"] == "127.0.0.1"


def test_maybe_detect_skips_when_specific(
    service: GripService, sample_key, monkeypatch
) -> None:
    service.resources.add_ssh(
        name="win",
        host="127.0.0.1",
        user="u",
        key_file=sample_key,
        os_name="windows",
    )

    def boom(*a, **k):  # noqa: ANN001
        raise AssertionError("must not SSH when os already windows")

    monkeypatch.setattr("grip.ssh_exec.detect_remote_os", boom)
    assert service.maybe_detect_ssh_os("win") == "windows"
    assert service.resources.get("win").meta["os"] == "windows"  # type: ignore[union-attr]
