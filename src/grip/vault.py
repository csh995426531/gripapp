from __future__ import annotations

import logging
import os
import warnings
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from grip import key_store
from grip.paths import ensure_home, master_key_path

log = logging.getLogger("grip.vault")

MASTER_ACCOUNT = "master"
AUDIT_INFO = b"grip-audit-v1"


class SecretNotFound(KeyError):
    pass


class Vault:
    """Encrypted secret store. Plaintext never goes to SQLite or MCP.

    Master key prefers OS keyring (macOS Keychain). On-disk master.key is
    migrated once then removed. File fallback is weak vs same-user AI.
    """

    def __init__(self, root: Path | None = None) -> None:
        ensure_home()
        self._root = root or ensure_home()
        self._store_path = self._root / "vault.enc"
        self._backend: key_store.Backend = "file"
        self._master = self._load_or_create_master()
        self._fernet = Fernet(self._master)
        self._cache: dict[str, str] = {}
        self._load()

    @property
    def backend(self) -> key_store.Backend:
        return self._backend

    def audit_hmac_key(self) -> bytes:
        """Derive a stable HMAC key from the vault master (not stored separately)."""
        return HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=AUDIT_INFO,
        ).derive(self._master)

    def _file_master_path(self) -> Path:
        if self._root.resolve() == ensure_home().resolve():
            return master_key_path()
        return self._root / "master.key"

    def _load_or_create_master(self) -> bytes:
        file_path = self._file_master_path()

        # 1) Prefer OS keyring — but only if it actually unlocks vault.enc.
        # Never delete master.key before decrypt succeeds (mixed CLI/app homes
        # previously wiped the file key with an unrelated Keychain master).
        if key_store.keyring_enabled():
            existing = key_store.get_secret(MASTER_ACCOUNT)
            if existing:
                key = existing.strip()
                if self._key_unlocks_store(key):
                    self._backend = "keyring"
                    # Keep master.key when the user opted into file vault — the
                    # packaged app may force GRIP_KEYRING=0 on next launch.
                    if file_path.exists() and not self._want_file_vault():
                        try:
                            file_path.unlink()
                            log.info("removed leftover %s after keyring unlock", file_path)
                        except OSError:
                            pass
                    return key
                log.warning(
                    "keyring master present but does not unlock %s; trying file key",
                    self._store_path.name,
                )

            # 2) Migrate from on-disk master.key → keyring (only after unlock ok)
            if file_path.exists():
                key = file_path.read_bytes().strip()
                if self._store_path.exists() and not self._key_unlocks_store(key):
                    raise RuntimeError(
                        f"Cannot unlock Grip vault: {file_path.name} does not match "
                        f"{self._store_path.name}"
                    )
                if key_store.set_secret(MASTER_ACCOUNT, key):
                    self._backend = "keyring"
                    if not self._want_file_vault():
                        try:
                            file_path.unlink()
                            log.info("migrated master key to keyring; removed %s", file_path)
                        except OSError as exc:
                            log.warning(
                                "could not remove %s after migrate: %s", file_path, exc
                            )
                    else:
                        log.info(
                            "kept %s (file_vault_accepted) after keyring sync",
                            file_path.name,
                        )
                    return key
                # Keyring set failed — keep using file
                self._backend = "file"
                self._warn_file_backend()
                return key

            # 3) Fresh key → keyring only
            # If vault.enc already exists, a missing keyring master is fatal —
            # do not mint a new file key (that would look like "decrypt failed").
            if self._store_path.exists() and not file_path.exists():
                raise RuntimeError(
                    "Cannot unlock Grip vault: OS keyring has no master key "
                    "(or Keychain access timed out for this app). "
                    "Allow Keychain access for Grip, or restore master.key."
                )
            key = Fernet.generate_key()
            if key_store.set_secret(MASTER_ACCOUNT, key):
                self._backend = "keyring"
                return key
            # Fall through to file

        # File backend (tests via GRIP_KEYRING=0, or keyring unavailable)
        self._backend = "file"
        if file_path.exists():
            if key_store.keyring_enabled():
                self._warn_file_backend()
            return file_path.read_bytes().strip()
        if self._store_path.exists():
            raise RuntimeError(
                "Cannot unlock Grip vault: master key not in OS keyring or "
                f"{file_path.name}. Keyring may be disabled (GRIP_KEYRING=0)."
            )
        key = Fernet.generate_key()
        file_path.write_bytes(key)
        os.chmod(file_path, 0o600)
        if key_store.keyring_enabled():
            self._warn_file_backend()
        return key

    def _want_file_vault(self) -> bool:
        """True when the panel opted into on-disk master.key (file vault)."""
        try:
            state = self._root / "permissions.json"
            if not state.is_file():
                return False
            import json

            data = json.loads(state.read_text(encoding="utf-8"))
            return bool(data.get("file_vault_accepted"))
        except Exception:  # noqa: BLE001
            return False

    def _key_unlocks_store(self, key: bytes) -> bool:
        """Return True if key decrypts vault.enc, or if there is no store yet."""
        if not self._store_path.exists():
            return True
        raw = self._store_path.read_bytes()
        if not raw:
            return True
        try:
            Fernet(key).decrypt(raw)
            return True
        except InvalidToken:
            return False
        except Exception:  # noqa: BLE001
            return False

    def _warn_file_backend(self) -> None:
        msg = (
            "Grip vault master key is stored as a local file (not OS keyring). "
            "Same-user agents that can read GRIP_HOME can decrypt secrets. "
            "Fix Keychain/keyring access or accept the weaker file backend."
        )
        warnings.warn(msg, UserWarning, stacklevel=3)
        log.warning(msg)

    def _load(self) -> None:
        if not self._store_path.exists():
            self._cache = {}
            return
        raw = self._store_path.read_bytes()
        if not raw:
            self._cache = {}
            return
        try:
            plain = self._fernet.decrypt(raw).decode("utf-8")
        except InvalidToken as exc:
            raise RuntimeError("vault decrypt failed; master key mismatch") from exc
        self._cache = {}
        for line in plain.splitlines():
            if not line or "=" not in line:
                continue
            sid, val = line.split("=", 1)
            self._cache[sid] = val.replace("\\n", "\n")

    def _persist(self) -> None:
        # Escape newlines for the line-oriented store (avoid f-string \\ which
        # breaks on older Python parsers).
        lines = [
            "{}={}".format(k, v.replace("\n", "\\n"))
            for k, v in sorted(self._cache.items())
        ]
        token = self._fernet.encrypt("\n".join(lines).encode("utf-8"))
        self._store_path.write_bytes(token)
        os.chmod(self._store_path, 0o600)

    def put(self, secret_id: str, plaintext: str) -> None:
        if not secret_id or plaintext is None:
            raise ValueError("secret_id and plaintext required")
        self._cache[secret_id] = plaintext
        self._persist()

    def get(self, secret_id: str) -> str:
        if secret_id not in self._cache:
            raise SecretNotFound(secret_id)
        return self._cache[secret_id]

    def delete(self, secret_id: str) -> None:
        if secret_id in self._cache:
            del self._cache[secret_id]
            self._persist()

    def has(self, secret_id: str) -> bool:
        return secret_id in self._cache
