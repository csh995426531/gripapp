from __future__ import annotations

import json

from grip.service import GripService


def test_secret_dict_repairs_pem_with_literal_newlines(service: GripService) -> None:
    acc = service.accounts.upsert(
        cloud="oracle",
        label="fix",
        meta={"compartment_id": "ocid1.compartment.oc1..x", "regions": ["ap-tokyo-1"]},
        secret_payload={
            "user_ocid": "ocid1.user.oc1..u",
            "tenancy_ocid": "ocid1.tenancy.oc1..t",
            "fingerprint": "aa:bb",
            "private_key": "-----BEGIN PRIVATE KEY-----\nLINE1\nLINE2\n-----END PRIVATE KEY-----\n",
            "compartment_id": "ocid1.compartment.oc1..x",
            "region": "ap-tokyo-1",
        },
    )
    # Simulate historical bad vault payload (literal newlines inside JSON string).
    bad = (
        '{"user_ocid":"ocid1.user.oc1..u","private_key":"-----BEGIN PRIVATE KEY-----\n'
        "LINE1\nLINE2\n-----END PRIVATE KEY-----\n"
        '","fingerprint":"aa:bb"}'
    )
    service.vault.put(acc.secret_id, bad)
    data = service.accounts.secret_dict(acc)
    assert "LINE1" in data["private_key"]
    # Rewritten as strict JSON
    raw = service.vault.get(acc.secret_id)
    json.loads(raw)  # must not raise
