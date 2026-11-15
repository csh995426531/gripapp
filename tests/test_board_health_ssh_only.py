from __future__ import annotations

from grip.service import GripService


def test_board_reachable_counts_ssh_only_not_cloud_running(
    service: GripService, sample_key
) -> None:
    service.resources.add_ssh(
        name="local-ssh", host="127.0.0.1", user="u", key_file=sample_key
    )
    service.resources.set_status("local-ssh", "reachable")
    service.resources.upsert_cloud_instance(
        name="oci:main:box",
        kind="oracle_instance",
        status="running",
        meta={"cloud": "oracle", "region": "ap-tokyo-1", "account": "main"},
    )
    board = service.board()
    h = board["resource_health"]
    assert h["reachable"] == 1
    assert h["unreachable"] == 0
    # Cloud running must not inflate reachable
    assert all(
        (r.get("kind") == "ssh")
        for r in board["resources"]
        if (r.get("status") or "").lower() == "reachable"
    )


def test_account_update_keeps_secret_when_blank(service: GripService) -> None:
    acc = service.accounts.upsert(
        cloud="aliyun",
        label="main",
        meta={"regions": ["cn-hangzhou"]},
        secret_payload={
            "access_key_id": "AKOLD",
            "access_key_secret": "SKOLD",
        },
    )
    service.accounts.update(
        acc.id,
        meta={"regions": ["cn-beijing"]},
        secret_payload={"access_key_id": "", "access_key_secret": ""},
    )
    got = service.accounts.get(acc.id)
    assert got is not None
    assert got.meta["regions"] == ["cn-beijing"]
    sec = service.accounts.secret_dict(got)
    assert sec["access_key_id"] == "AKOLD"
    assert sec["access_key_secret"] == "SKOLD"
