from __future__ import annotations

from grip.service import GripService
from grip.vault import SecretNotFound


def test_public_for_edit_shows_oracle_ids_not_private_key(service: GripService) -> None:
    service.accounts.upsert(
        cloud="oracle",
        label="tokyo",
        meta={"compartment_id": "ocid1.compartment.oc1..c", "regions": ["ap-tokyo-1"]},
        secret_payload={
            "user_ocid": "ocid1.user.oc1..u",
            "tenancy_ocid": "ocid1.tenancy.oc1..t",
            "fingerprint": "aa:bb",
            "private_key": "-----BEGIN PRIVATE KEY-----\nX\n-----END PRIVATE KEY-----\n",
            "compartment_id": "ocid1.compartment.oc1..c",
        },
    )
    out = service.list_cloud_accounts()
    row = next(a for a in out["accounts"] if a["id"] == "oracle:tokyo")
    form = row["form"]
    assert form["user_ocid"].startswith("ocid1.user")
    assert form["fingerprint"] == "aa:bb"
    assert "private_key" not in form
    assert row.get("signing_set") is True
    assert not row.get("need_rekey")
    blob = str(row)
    assert "BEGIN PRIVATE KEY" not in blob
    # Non-secret IDs are healed into meta for vault-loss resilience.
    got = service.accounts.get("oracle:tokyo")
    assert got is not None
    assert got.meta.get("user_ocid") == "ocid1.user.oc1..u"
    assert got.meta.get("fingerprint") == "aa:bb"


def test_edit_form_fills_oracle_ids_from_meta_when_vault_missing(
    service: GripService,
) -> None:
    service.accounts.upsert(
        cloud="oracle",
        label="tokyo",
        meta={
            "compartment_id": "ocid1.compartment.oc1..c",
            "regions": ["ap-tokyo-1"],
            "user_ocid": "ocid1.user.oc1..from-meta",
            "tenancy_ocid": "ocid1.tenancy.oc1..from-meta",
            "fingerprint": "cc:dd",
        },
        secret_payload={
            "user_ocid": "ocid1.user.oc1..from-meta",
            "tenancy_ocid": "ocid1.tenancy.oc1..from-meta",
            "fingerprint": "cc:dd",
            "private_key": "-----BEGIN PRIVATE KEY-----\nX\n-----END PRIVATE KEY-----\n",
        },
    )
    acc = service.accounts.get("oracle:tokyo")
    assert acc is not None
    service.vault.delete(acc.secret_id)
    out = service.list_cloud_accounts()
    row = next(a for a in out["accounts"] if a["id"] == "oracle:tokyo")
    assert row["form"]["user_ocid"] == "ocid1.user.oc1..from-meta"
    assert row["form"]["fingerprint"] == "cc:dd"
    assert row["meta"]["compartment_id"].startswith("ocid1.compartment")
    assert row.get("need_rekey") is True
    assert row.get("signing_set") is False


def test_update_recreates_oracle_secret_after_vault_wipe(service: GripService) -> None:
    service.accounts.upsert(
        cloud="oracle",
        label="tokyo",
        meta={
            "compartment_id": "ocid1.compartment.oc1..c",
            "regions": ["ap-tokyo-1"],
            "user_ocid": "ocid1.user.oc1..u",
            "tenancy_ocid": "ocid1.tenancy.oc1..t",
            "fingerprint": "aa:bb",
        },
        secret_payload={
            "user_ocid": "ocid1.user.oc1..u",
            "tenancy_ocid": "ocid1.tenancy.oc1..t",
            "fingerprint": "aa:bb",
            "private_key": "-----BEGIN PRIVATE KEY-----\nold\n-----END PRIVATE KEY-----\n",
        },
    )
    acc = service.accounts.get("oracle:tokyo")
    assert acc is not None
    service.vault.delete(acc.secret_id)
    try:
        service.accounts.secret_dict(acc)
        raise AssertionError("expected SecretNotFound")
    except SecretNotFound:
        pass
    service.accounts.update(
        "oracle:tokyo",
        secret_payload={
            "user_ocid": "ocid1.user.oc1..u",
            "tenancy_ocid": "ocid1.tenancy.oc1..t",
            "fingerprint": "aa:bb",
            "private_key": "-----BEGIN PRIVATE KEY-----\nnew\n-----END PRIVATE KEY-----\n",
        },
    )
    data = service.accounts.secret_dict(service.accounts.get("oracle:tokyo"))  # type: ignore[arg-type]
    assert "new" in data["private_key"]
    assert data["user_ocid"] == "ocid1.user.oc1..u"
