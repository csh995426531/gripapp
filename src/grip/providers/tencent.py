from __future__ import annotations

from typing import TYPE_CHECKING, Any

from grip.cloud_api import tencent as tc_api
from grip.providers._cli import slug

if TYPE_CHECKING:
    from grip.accounts import AccountStore, CloudAccount


def list_for_account(account: "CloudAccount", secrets: dict[str, Any]) -> list[dict[str, Any]]:
    sid = secrets.get("secret_id")
    skey = secrets.get("secret_key")
    if not sid or not skey:
        raise RuntimeError(f"{account.id}: missing secret_id/secret_key")
    regions = account.meta.get("regions") or ["ap-guangzhou"]
    product = (account.meta.get("product") or "lighthouse").lower()
    out: list[dict[str, Any]] = []
    for region in regions:
        if product == "cvm":
            items = tc_api.describe_cvm_instances(
                secret_id=sid, secret_key=skey, region=region
            )
            for i in items:
                name = i.get("InstanceName") or i.get("InstanceId")
                pub_list = i.get("PublicIpAddresses") or [None]
                prv_list = i.get("PrivateIpAddresses") or [None]
                pub = pub_list[0] if pub_list else None
                prv = prv_list[0] if prv_list else None
                row: dict[str, Any] = {
                    "cloud": "tencent",
                    "name": f"tc:{account.label}:{name}",
                    "id": i.get("InstanceId"),
                    "status": (i.get("InstanceState") or "").lower() or "unknown",
                    "region": region,
                    "shape": i.get("InstanceType"),
                    "private_ip": prv,
                    "public_ip": pub,
                    "account": account.label,
                    "product": "cvm",
                    "ssh_alias": "tc-" + slug(str(name)),
                }
                if i.get("CPU") is not None:
                    row["cpu_cores"] = i["CPU"]
                if i.get("Memory") is not None:
                    row["mem_gb"] = i["Memory"]
                out.append(row)
        else:
            items = tc_api.describe_lighthouse_instances(
                secret_id=sid, secret_key=skey, region=region
            )
            for i in items:
                name = i.get("InstanceName") or i.get("InstanceId")
                pub_list = i.get("PublicAddresses") or [None]
                prv_list = i.get("PrivateAddresses") or [None]
                pub = pub_list[0] if pub_list else None
                prv = prv_list[0] if prv_list else None
                row = {
                    "cloud": "tencent",
                    "name": f"tc:{account.label}:{name}",
                    "id": i.get("InstanceId"),
                    "status": (i.get("InstanceState") or "").lower() or "unknown",
                    "region": region,
                    "shape": "Lighthouse",
                    "private_ip": prv,
                    "public_ip": pub,
                    "account": account.label,
                    "product": "lighthouse",
                    "ssh_alias": "tc-" + slug(str(name)),
                }
                if i.get("CPU") is not None:
                    row["cpu_cores"] = i["CPU"]
                if i.get("Memory") is not None:
                    row["mem_gb"] = i["Memory"]
                out.append(row)
    return out


def list_tencent_instances(accounts: "AccountStore") -> list[dict[str, Any]]:
    rows = accounts.list("tencent")
    if not rows:
        raise RuntimeError(
            "No Tencent account configured. Run: grip cloud-add tencent --label ... --secret-id ... --secret-key ..."
        )
    out: list[dict[str, Any]] = []
    for acc in rows:
        out.extend(list_for_account(acc, accounts.secret_dict(acc)))
    return out
