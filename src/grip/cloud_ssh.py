from __future__ import annotations

"""Link cloud inventory rows to Grip SSH resources (noc-ops pattern, vault-native)."""

from typing import Any

from grip.accounts import AccountStore, CloudAccount
from grip.resources import Resource, ResourceStore
from grip.vault import Vault

KIND_TO_CLOUD = {
    "oracle_instance": "oracle",
    "gcp_instance": "gcp",
    "aliyun_instance": "aliyun",
    "tencent_instance": "tencent",
}


def pick_ssh_host(meta: dict[str, Any]) -> str | None:
    pub = (meta.get("public_ip") or "").strip()
    prv = (meta.get("private_ip") or "").strip()
    return pub or prv or None


def ssh_name_for_cloud_row(meta: dict[str, Any], cloud_name: str) -> str:
    alias = (meta.get("ssh_alias") or "").strip()
    if alias:
        return alias
    # fallback: sanitize cloud resource name
    raw = cloud_name.replace(":", "-")
    return raw[:64] or "cloud-host"


def resolve_account(
    accounts: AccountStore, cloud: str, account_label: str | None
) -> CloudAccount | None:
    if not account_label:
        return None
    return accounts.get(f"{cloud}:{account_label}")


def link_ssh_from_cloud(
    store: ResourceStore,
    vault: Vault,
    accounts: AccountStore,
    *,
    cloud_name: str,
    kind: str,
    meta: dict[str, Any],
    ssh_user: str | None = None,
    key_material: str | None = None,
) -> Resource | None:
    """Upsert an SSH device for a cloud instance row. Returns None if skipped."""
    _ = vault  # same vault as store/accounts; kept for call-site clarity
    cloud = KIND_TO_CLOUD.get(kind)
    if not cloud:
        return None
    host = pick_ssh_host(meta)
    if not host:
        return None

    user = (ssh_user or "").strip() or None
    key = key_material
    if not user or not key:
        acc = resolve_account(accounts, cloud, meta.get("account"))
        if not acc:
            return None
        u, k = accounts.instance_ssh_defaults(acc)
        user = user or u
        key = key or k
    if not user or not key:
        return None

    ssh_name = ssh_name_for_cloud_row(meta, cloud_name)
    ssh = store.upsert_ssh_with_key(
        name=ssh_name,
        host=host,
        user=user,
        key_material=key,
        port=22,
        os_name="unix",
        extra_meta={
            "cloud_ref": cloud_name,
            "cloud": cloud,
            "from_cloud_sync": True,
        },
    )
    # Point cloud row at SSH device (merge into existing cloud meta)
    store.patch_meta(cloud_name, {"ssh_name": ssh.name, "ssh_host": host})
    return ssh


def link_all_from_sync(
    store: ResourceStore,
    vault: Vault,
    accounts: AccountStore,
    cloud: str,
    rows: list[dict[str, Any]],
) -> int:
    """After inventory upsert, try linking each row. Soft-fail per row."""
    kind = {
        "oracle": "oracle_instance",
        "gcp": "gcp_instance",
        "aliyun": "aliyun_instance",
        "tencent": "tencent_instance",
    }[cloud]
    dismissed = store.dismissed_names()
    linked = 0
    for row in rows:
        name = row.get("name")
        if not name:
            continue
        if str(name) in dismissed:
            continue
        # Re-read meta from store (includes preserved fields)
        cur = store.get(str(name))
        meta = dict(cur.meta) if cur else {
            k: v for k, v in row.items() if k not in {"name", "status"} and v is not None
        }
        try:
            ssh = link_ssh_from_cloud(
                store, vault, accounts, cloud_name=str(name), kind=kind, meta=meta
            )
        except Exception:  # noqa: BLE001
            continue
        if ssh:
            linked += 1
    return linked
