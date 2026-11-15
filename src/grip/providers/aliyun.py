from __future__ import annotations

from typing import TYPE_CHECKING, Any

from grip.cloud_api import aliyun as ali_api
from grip.providers._cli import slug

if TYPE_CHECKING:
    from grip.accounts import AccountStore, CloudAccount


def list_for_account(account: "CloudAccount", secrets: dict[str, Any]) -> list[dict[str, Any]]:
    ak = secrets.get("access_key_id")
    sk = secrets.get("access_key_secret")
    if not ak or not sk:
        raise RuntimeError(f"{account.id}: missing access_key_id/access_key_secret")
    regions = account.meta.get("regions") or ["cn-hangzhou"]
    out: list[dict[str, Any]] = []
    for region in regions:
        page = 1
        while True:
            data = ali_api.describe_instances(
                access_key_id=ak,
                access_key_secret=sk,
                region=region,
                page_number=page,
                page_size=50,
            )
            instances = (data.get("Instances") or {}).get("Instance") or []
            for i in instances:
                pub = (i.get("PublicIpAddress") or {}).get("IpAddress") or []
                eip = (i.get("EipAddress") or {}).get("IpAddress")
                prv = (
                    (i.get("VpcAttributes") or {})
                    .get("PrivateIpAddress", {})
                    .get("IpAddress")
                    or (i.get("InnerIpAddress") or {}).get("IpAddress")
                    or []
                )
                name = i.get("InstanceName") or i.get("InstanceId")
                cpu_cores = i.get("Cpu")
                mem_mb = i.get("Memory")
                row: dict[str, Any] = {
                    "cloud": "aliyun",
                    "name": f"ali:{account.label}:{name}",
                    "id": i.get("InstanceId"),
                    "status": (i.get("Status") or "").lower() or "unknown",
                    "region": region,
                    "shape": i.get("InstanceType"),
                    "private_ip": prv[0] if prv else None,
                    "public_ip": (pub[0] if pub else eip),
                    "account": account.label,
                    "ssh_alias": "ali-" + slug(str(name)),
                }
                if cpu_cores is not None:
                    row["cpu_cores"] = cpu_cores
                if mem_mb is not None:
                    row["mem_mb"] = mem_mb
                out.append(row)
            total = int(data.get("TotalCount") or 0)
            if page * 50 >= total or not instances:
                break
            page += 1
    return out


def list_aliyun_instances(accounts: "AccountStore") -> list[dict[str, Any]]:
    rows = accounts.list("aliyun")
    if not rows:
        raise RuntimeError(
            "No Aliyun account configured. Run: grip cloud-add aliyun --label ... --ak ... --sk ..."
        )
    out: list[dict[str, Any]] = []
    for acc in rows:
        out.extend(list_for_account(acc, accounts.secret_dict(acc)))
    return out
