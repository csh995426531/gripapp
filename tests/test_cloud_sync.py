from __future__ import annotations

from grip.redact import assert_no_secrets
from grip.service import GripService
from grip.sync import upsert_instances


def test_upsert_four_clouds_into_inventory(service: GripService) -> None:
    for cloud, rows in {
        "oracle": [
            {
                "cloud": "oracle",
                "name": "oci:demo",
                "id": "ocid1",
                "status": "running",
                "region": "ap-tokyo-1",
            }
        ],
        "gcp": [
            {
                "cloud": "gcp",
                "name": "gcp:p:vm1",
                "id": "1",
                "status": "running",
                "project": "p",
            }
        ],
        "aliyun": [
            {
                "cloud": "aliyun",
                "name": "ali:a:ecs1",
                "id": "i-1",
                "status": "running",
                "region": "cn-hangzhou",
            }
        ],
        "tencent": [
            {
                "cloud": "tencent",
                "name": "tc:lh1",
                "id": "lhins-1",
                "status": "running",
                "region": "ap-guangzhou",
            }
        ],
    }.items():
        n, dismissed = upsert_instances(service.resources, cloud, rows)
        assert n == 1
        assert dismissed == []

    payload = service.list_resources()
    assert_no_secrets(payload)
    names = {r["name"] for r in payload["resources"]}
    assert names == {"oci:demo", "gcp:p:vm1", "ali:a:ecs1", "tc:lh1"}


def test_cloud_add_stores_secret_not_in_public(service: GripService) -> None:
    acc = service.accounts.upsert(
        cloud="aliyun",
        label="main",
        meta={"regions": ["cn-hangzhou"]},
        secret_payload={
            "access_key_id": "AKTEST",
            "access_key_secret": "SECRET",
        },
    )
    pub = acc.public()
    assert_no_secrets(pub)
    assert "SECRET" not in str(pub)
    listed = service.list_cloud_accounts()
    assert_no_secrets(listed)
    assert listed["accounts"][0]["id"] == "aliyun:main"
    # vault has secret; MCP-facing list does not
    secret = service.accounts.secret_dict(acc)
    assert secret["access_key_secret"] == "SECRET"


def test_sync_cloud_uses_provider(monkeypatch, service: GripService) -> None:
    service.accounts.upsert(
        cloud="gcp",
        label="p",
        meta={"project": "p"},
        secret_payload={"service_account": {"project_id": "p", "type": "service_account"}},
    )

    monkeypatch.setattr(
        "grip.sync.list_cloud_instances",
        lambda cloud, accounts: [
            {
                "cloud": cloud,
                "name": f"{cloud}:x",
                "id": "1",
                "status": "running",
            }
        ],
    )
    out = service.sync_cloud("gcp")
    assert out["status"] == "success"
    assert out["count"] == 1


def test_sync_all_soft_fails(monkeypatch, service: GripService) -> None:
    # Ensure every cloud has an account so sync_all actually attempts them.
    for cloud, secret in (
        ("oracle", {"user_ocid": "u", "tenancy_ocid": "t", "fingerprint": "f", "private_key": "k"}),
        ("gcp", {"service_account": {"project_id": "p", "type": "service_account"}}),
        ("aliyun", {"access_key_id": "a", "access_key_secret": "b"}),
        ("tencent", {"secret_id": "s", "secret_key": "k"}),
    ):
        service.accounts.upsert(
            cloud=cloud, label="main", meta={"regions": ["x"]}, secret_payload=secret
        )

    def fake(cloud, accounts):
        if cloud == "oracle":
            raise RuntimeError("boom")
        return [{"cloud": cloud, "name": f"{cloud}:ok", "id": "1", "status": "running"}]

    monkeypatch.setattr("grip.sync.list_cloud_instances", fake)
    out = service.sync_all()
    assert out["status"] == "success"
    assert any(e["cloud"] == "oracle" for e in out["errors"])


def test_sync_all_skips_clouds_without_accounts(
    monkeypatch, service: GripService
) -> None:
    service.accounts.upsert(
        cloud="oracle",
        label="only",
        meta={"regions": ["ap-tokyo-1"], "compartment_id": "ocid1.compartment.oc1..x"},
        secret_payload={
            "user_ocid": "u",
            "tenancy_ocid": "t",
            "fingerprint": "f",
            "private_key": "k",
            "compartment_id": "ocid1.compartment.oc1..x",
        },
    )

    def fake(cloud, accounts):
        assert cloud == "oracle"
        return [
            {
                "cloud": "oracle",
                "name": "oci:only:box",
                "id": "i-1",
                "status": "running",
                "region": "ap-tokyo-1",
                "account": "only",
            }
        ]

    monkeypatch.setattr("grip.sync.list_cloud_instances", fake)
    out = service.sync_all()
    assert not out.get("errors")
    assert "gcp" in (out.get("skipped") or [])
    assert "aliyun" in (out.get("skipped") or [])
    assert out["ok"] >= 1
