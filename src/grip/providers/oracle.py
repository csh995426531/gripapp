from __future__ import annotations

from typing import TYPE_CHECKING, Any

from grip.cloud_api import oracle as oci_api
from grip.providers._cli import slug

if TYPE_CHECKING:
    from grip.accounts import AccountStore, CloudAccount


def _vnic_ips(
    *,
    secrets: dict[str, Any],
    region: str,
    compartment_id: str,
    instance_id: str,
) -> tuple[str | None, str | None]:
    """Return (private_ip, public_ip) for the instance primary VNIC. Soft-fail."""
    try:
        atts = oci_api.list_vnic_attachments(
            secrets=secrets,
            region=region,
            compartment_id=compartment_id,
            instance_id=instance_id,
        )
    except Exception:  # noqa: BLE001
        return None, None
    for att in atts:
        vnic_id = att.get("vnicId")
        if not vnic_id:
            continue
        try:
            vnic = oci_api.get_vnic(
                secrets=secrets, region=region, vnic_id=str(vnic_id)
            )
        except Exception:  # noqa: BLE001
            continue
        prv = vnic.get("privateIp") or None
        pub = vnic.get("publicIp") or None
        return prv, pub
    return None, None


def list_for_account(account: "CloudAccount", secrets: dict[str, Any]) -> list[dict[str, Any]]:
    compartment = account.meta.get("compartment_id") or secrets.get("compartment_id")
    if not compartment:
        raise RuntimeError(f"{account.id}: missing compartment_id")
    regions = account.meta.get("regions") or [secrets.get("region") or "ap-tokyo-1"]
    out: list[dict[str, Any]] = []
    for region in regions:
        items = oci_api.list_instances(
            secrets=secrets,
            region=region,
            compartment_id=str(compartment),
        )
        for i in items:
            state = (i.get("lifecycleState") or "").lower()
            if state in ("terminated", "terminating"):
                continue
            display = i.get("displayName") or i.get("id")
            prv, pub = _vnic_ips(
                secrets=secrets,
                region=region,
                compartment_id=str(compartment),
                instance_id=str(i.get("id")),
            )
            row: dict[str, Any] = {
                "cloud": "oracle",
                "name": f"oci:{account.label}:{display}",
                "id": i.get("id"),
                "status": state or "unknown",
                "region": i.get("region") or region,
                "shape": i.get("shape"),
                "availability_domain": i.get("availabilityDomain"),
                "account": account.label,
                "ssh_alias": "oci-" + slug(str(display)),
            }
            if prv:
                row["private_ip"] = prv
            if pub:
                row["public_ip"] = pub
            out.append(row)
    return out


def list_oracle_instances(accounts: "AccountStore") -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    rows = accounts.list("oracle")
    if not rows:
        raise RuntimeError(
            "No Oracle account configured. Run: grip cloud-add oracle --label ... (keys stored in vault)"
        )
    for acc in rows:
        out.extend(list_for_account(acc, accounts.secret_dict(acc)))
    return out
