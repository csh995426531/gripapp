from __future__ import annotations

import base64
import secrets

from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)


def generate_wg_keypair() -> tuple[str, str]:
    """Return (private_b64, public_b64) in WireGuard's standard base64 form."""
    priv = X25519PrivateKey.generate()
    priv_raw = priv.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    pub_raw = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return (
        base64.b64encode(priv_raw).decode("ascii"),
        base64.b64encode(pub_raw).decode("ascii"),
    )


def public_from_private(private_b64: str) -> str:
    raw = base64.b64decode(private_b64)
    priv = X25519PrivateKey.from_private_bytes(raw)
    pub_raw = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return base64.b64encode(pub_raw).decode("ascii")


def new_node_id(prefix: str = "node") -> str:
    return f"{prefix}-{secrets.token_hex(4)}"


def verify_public_b64(public_b64: str) -> bool:
    try:
        raw = base64.b64decode(public_b64)
        if len(raw) != 32:
            return False
        X25519PublicKey.from_public_bytes(raw)
        return True
    except Exception:
        return False
