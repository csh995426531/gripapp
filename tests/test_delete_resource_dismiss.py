from __future__ import annotations

from grip.service import GripService
from grip.sync import upsert_instances


def test_delete_ssh_via_service(service: GripService, sample_key) -> None:
    service.resources.add_ssh(
        name="tmp-del", host="10.0.0.9", user="u", key_file=sample_key
    )
    out = service.delete_resource("tmp-del")
    assert out["ok"] is True
    assert service.resources.get("tmp-del") is None


def test_delete_cloud_dismisses_sync(service: GripService, sample_key) -> None:
    service.resources.upsert_cloud_instance(
        name="oci:test:box",
        kind="oracle_instance",
        status="running",
        meta={"public_ip": "1.2.3.4", "region": "ap-tokyo-1"},
    )
    service.resources.upsert_ssh_with_key(
        name="oci-box",
        host="1.2.3.4",
        user="ubuntu",
        key_material=sample_key.read_text(encoding="utf-8"),
        extra_meta={"cloud_ref": "oci:test:box"},
    )
    service.resources.patch_meta("oci:test:box", {"ssh_name": "oci-box"})

    out = service.delete_resource("oci:test:box")
    assert out["ok"] is True
    assert "oci:test:box" in out["deleted"]
    assert service.resources.get("oci:test:box") is None
    assert service.resources.get("oci-box") is None
    assert "oci:test:box" in service.resources.dismissed_names()

    # Sync must not resurrect a dismissed cloud row until user confirms.
    n, dismissed = upsert_instances(
        service.resources,
        "oracle",
        [
            {
                "name": "oci:test:box",
                "status": "running",
                "public_ip": "1.2.3.4",
                "region": "ap-tokyo-1",
            }
        ],
    )
    assert n == 0
    assert service.resources.get("oci:test:box") is None
    assert dismissed == [
        {"name": "oci:test:box", "cloud": "oracle", "status": "running"}
    ]

    # Explicit restore undismisses and allows the next upsert.
    service.resources.undismiss("oci:test:box")
    n2, dismissed2 = upsert_instances(
        service.resources,
        "oracle",
        [
            {
                "name": "oci:test:box",
                "status": "running",
                "public_ip": "1.2.3.4",
                "region": "ap-tokyo-1",
            }
        ],
    )
    assert n2 == 1
    assert dismissed2 == []
    assert service.resources.get("oci:test:box") is not None
