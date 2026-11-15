from __future__ import annotations

from grip.service import GripService


def test_add_ssh_password_auth(service: GripService, monkeypatch) -> None:
    r = service.resources.add_ssh(
        name="box",
        host="192.168.1.50",
        user="admin",
        password="s3cret",
        os_name="unix",
    )
    assert r.meta["auth"] == "password"
    assert "password" not in r.summary()["labels"]
    assert service.vault.get("ssh:box") == "s3cret"

    def fake_pw(resource, vault, command, timeout_sec=60, **_kwargs):
        from grip.ssh_exec import ExecResult

        assert resource.meta["auth"] == "password"
        assert vault.get(resource.secret_id) == "s3cret"
        return ExecResult(0, "ok\n", "")

    monkeypatch.setattr("grip.ssh_exec._run_ssh_password", fake_pw)
    # route through run_ssh
    from grip.ssh_exec import run_ssh

    out = run_ssh(r, service.vault, "echo ok")
    assert out.exit_code == 0


def test_add_ssh_requires_one_auth(service: GripService, sample_key) -> None:
    import pytest

    with pytest.raises(ValueError):
        service.resources.add_ssh(
            name="x", host="1.1.1.1", user="u", key_file=sample_key, password="p"
        )
