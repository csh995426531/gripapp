from __future__ import annotations

import json
from pathlib import Path

from grip.paths import status_path
from grip.redact import assert_no_secrets
from grip.service import GripService


def test_e2e_mock_ssh_to_status_json(
    service: GripService, sample_key: Path, monkeypatch, grip_env: Path
) -> None:
    service.resources.add_ssh(
        name="lab",
        host="127.0.0.1",
        user="ubuntu",
        key_file=sample_key,
    )

    def fake_run(resource, vault, command, timeout_sec=60):
        from grip.ssh_exec import ExecResult

        assert resource.name == "lab"
        # Pretend remote echoed a PEM — must be redacted before status/MCP
        return ExecResult(
            0,
            "hello\n-----BEGIN RSA PRIVATE KEY-----\nABC\n-----END RSA PRIVATE KEY-----\n",
            "",
        )

    monkeypatch.setattr("grip.service.run_ssh", fake_run)
    out = service.run_command("lab", "echo hello", background=False)
    assert out["status"] == "success"

    path = status_path()
    assert path.exists()
    snap = json.loads(path.read_text(encoding="utf-8"))
    assert_no_secrets(snap)
    assert any(t["status"] == "success" for t in snap["tasks"])
    assert any(r["name"] == "lab" for r in snap["resources"])
    blob = json.dumps(snap)
    assert "BEGIN RSA PRIVATE KEY" not in blob
