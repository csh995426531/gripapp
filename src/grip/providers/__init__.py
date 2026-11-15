from __future__ import annotations

from typing import TYPE_CHECKING

from grip.providers.aliyun import list_aliyun_instances
from grip.providers.gcp import list_gcp_instances
from grip.providers.oracle import list_oracle_instances
from grip.providers.tencent import list_tencent_instances

if TYPE_CHECKING:
    from grip.accounts import AccountStore

CLOUDS = ("oracle", "gcp", "aliyun", "tencent")

LISTERS = {
    "oracle": list_oracle_instances,
    "gcp": list_gcp_instances,
    "aliyun": list_aliyun_instances,
    "tencent": list_tencent_instances,
}


def list_cloud_instances(cloud: str, accounts: "AccountStore") -> list[dict]:
    if cloud not in LISTERS:
        raise ValueError(f"unknown cloud: {cloud}")
    return LISTERS[cloud](accounts)
