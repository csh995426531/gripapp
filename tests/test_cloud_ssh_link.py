from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from grip.cloud_ssh import link_ssh_from_cloud, pick_ssh_host
from grip.providers import oracle as oracle_mod
from grip.service import GripService
from grip.sync import sync_cloud


def test_pick_ssh_host_prefers_public() -> None:
    assert pick_ssh_host({"public_ip": "1.2.3.4", "private_ip": "10.0.0.1"}) == "1.2.3.4"
    assert pick_ssh_host({"private_ip": "10.0.0.1"}) == "10.0.0.1"
    assert pick_ssh_host({}) is None


def test_oracle_vnic_ips_helper() -> None:
    compute = SimpleNamespace(
        list_vnic_attachments=lambda *a, **k: SimpleNamespace(
            data=[SimpleNamespace(vnic_id="ocid1.vnic.x")]
        )
    )
    network = SimpleNamespace(
        get_vnic=lambda _id: SimpleNamespace(
            data=SimpleNamespace(private_ip="10.0.0.9", public_ip="203.0.113.9")
        )
    )
    prv, pub = oracle_mod._vnic_ips(
        compute, network, compartment_id="ocid1.tenancy.x", instance_id="ocid1.instance.x"
    )
    assert prv == "10.0.0.9"
    assert pub == "203.0.113.9"


def test_link_ssh_from_cloud_creates_ssh(
    service: GripService, sample_key: Path
) -> None:
    service.accounts.upsert(
        cloud="oracle",
        label="main",
        meta={"compartment_id": "ocid1.x", "regions": ["ap-tokyo-1"]},
        secret_payload={
            "user_ocid": "u",
            "tenancy_ocid": "t",
            "fingerprint": "f",
            "private_key": "APIKEY",
            "compartment_id": "ocid1.x",
        },
    )
    service.accounts.set_instance_ssh_defaults(
        "oracle:main", ssh_user="opc", key_file=sample_key
    )
    service.resources.upsert_cloud_instance(
        name="oci:main:box",
        kind="oracle_instance",
        status="running",
        meta={
            "cloud": "oracle",
            "account": "main",
            "public_ip": "203.0.113.10",
            "ssh_alias": "oci-box",
            "region": "ap-tokyo-1",
        },
    )
    ssh = link_ssh_from_cloud(
        service.resources,
        service.vault,
        service.accounts,
        cloud_name="oci:main:box",
        kind="oracle_instance",
        meta=service.resources.get("oci:main:box").meta,  # type: ignore[union-attr]
    )
    assert ssh is not None
    assert ssh.name == "oci-box"
    assert ssh.meta["host"] == "203.0.113.10"
    assert ssh.meta["user"] == "opc"
    assert ssh.meta["cloud_ref"] == "oci:main:box"
    assert service.vault.get("ssh:oci-box").startswith("-----BEGIN")
    cloud = service.resources.get("oci:main:box")
    assert cloud is not None
    assert cloud.meta.get("ssh_name") == "oci-box"


def test_sync_links_when_defaults_set(
    service: GripService, sample_key: Path, monkeypatch
) -> None:
    service.accounts.upsert(
        cloud="oracle",
        label="t1",
        meta={"compartment_id": "ocid1.c", "regions": ["ap-tokyo-1"]},
        secret_payload={
            "user_ocid": "u",
            "tenancy_ocid": "t",
            "fingerprint": "f",
            "private_key": "APIKEY",
            "compartment_id": "ocid1.c",
        },
    )
    service.accounts.set_instance_ssh_defaults(
        "oracle:t1", ssh_user="opc", key_file=sample_key
    )

    def fake_list(cloud, accounts):
        assert cloud == "oracle"
        return [
            {
                "cloud": "oracle",
                "name": "oci:t1:vm1",
                "id": "ocid1.instance.1",
                "status": "running",
                "region": "ap-tokyo-1",
                "account": "t1",
                "public_ip": "198.51.100.7",
                "ssh_alias": "oci-vm1",
            }
        ]

    monkeypatch.setattr("grip.sync.list_cloud_instances", fake_list)
    out = sync_cloud(
        service.resources, service.accounts, "oracle", vault=service.vault
    )
    assert out["count"] == 1
    assert out["ssh_linked"] == 1
    ssh = service.resources.get("oci-vm1")
    assert ssh is not None and ssh.kind == "ssh"
    assert ssh.meta["host"] == "198.51.100.7"
