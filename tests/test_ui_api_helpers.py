from __future__ import annotations

from pathlib import Path

from grip.service import GripService


def test_delete_ssh_resource(service: GripService, sample_key: Path) -> None:
    service.resources.add_ssh(
        name="tmp", host="10.0.0.1", user="u", key_file=sample_key
    )
    assert service.resources.delete("tmp") is True
    assert service.resources.get("tmp") is None


def test_delete_cloud_account(service: GripService) -> None:
    service.accounts.upsert(
        cloud="aliyun",
        label="t",
        meta={"regions": ["cn-hangzhou"]},
        secret_payload={"access_key_id": "a", "access_key_secret": "b"},
    )
    assert service.accounts.delete("aliyun:t") is True
    assert service.accounts.get("aliyun:t") is None
