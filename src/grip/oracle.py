"""Backward-compatible re-export — prefer grip.providers.oracle / grip.sync. """

from grip.providers.oracle import list_oracle_instances
from grip.sync import sync_cloud as _sync_cloud


def oci_available() -> bool:
    from grip.providers._cli import which

    return which("oci")


def sync_into_store(store, profile: str | None = None) -> int:
    _ = profile
    return int(_sync_cloud(store, "oracle")["count"])


__all__ = ["list_oracle_instances", "oci_available", "sync_into_store"]
