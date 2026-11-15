from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from grip.db import connect, meta_dumps, meta_loads
from grip.redact import assert_no_secrets, strip_forbidden_keys
from grip.vault import SecretNotFound, Vault

CLOUD_SCHEMA = """
CREATE TABLE IF NOT EXISTS cloud_accounts (
  id TEXT PRIMARY KEY,
  cloud TEXT NOT NULL,
  label TEXT NOT NULL,
  meta_json TEXT NOT NULL DEFAULT '{}',
  secret_id TEXT NOT NULL
);
"""


@dataclass
class CloudAccount:
    id: str
    cloud: str
    label: str
    meta: dict[str, Any]
    secret_id: str

    def public(self) -> dict[str, Any]:
        payload = {
            "id": self.id,
            "cloud": self.cloud,
            "label": self.label,
            "meta": strip_forbidden_keys(dict(self.meta)),
        }
        assert_no_secrets(payload)
        return payload


def _edit_form_ids(
    cloud: str,
    secrets: dict[str, Any],
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Non-secret IDs for the edit form.

    Prefer vault values; fall back to meta so the panel still fills after a
    vault reset (OCIDs / AK IDs are identifiers, not private key material).
    Key names avoid redact.FORBIDDEN substrings (``secret`` / ``access_key`` / …).
    Never include private key material.
    """
    cloud = (cloud or "").lower()
    meta = meta or {}
    out: dict[str, Any] = {}
    if cloud == "aliyun":
        ak = secrets.get("access_key_id") or meta.get("ak_id")
        if ak:
            out["ak_id"] = ak
    elif cloud == "tencent":
        tc = secrets.get("secret_id") or meta.get("tc_id")
        if tc:
            out["tc_id"] = tc
    elif cloud == "oracle":
        for key in ("user_ocid", "tenancy_ocid", "fingerprint"):
            val = secrets.get(key) or meta.get(key)
            if val:
                out[key] = val
    elif cloud == "gcp":
        sa = secrets.get("service_account")
        if isinstance(sa, str):
            try:
                sa = json.loads(sa)
            except json.JSONDecodeError:
                sa = None
        if isinstance(sa, dict):
            if sa.get("client_email"):
                out["client_email"] = sa["client_email"]
            if sa.get("project_id"):
                out["project_id"] = sa["project_id"]
        if not out.get("client_email") and meta.get("client_email"):
            out["client_email"] = meta["client_email"]
        if not out.get("project_id") and (meta.get("project_id") or meta.get("project")):
            out["project_id"] = meta.get("project_id") or meta.get("project")
    return out


class AccountStore:
    def __init__(self, db_path: Path | None = None, vault: Vault | None = None) -> None:
        self._db_path = db_path
        self.vault = vault or Vault()

    def _conn(self):
        conn = connect(self._db_path)
        conn.executescript(CLOUD_SCHEMA)
        return conn

    def upsert(
        self,
        *,
        cloud: str,
        label: str,
        meta: dict[str, Any],
        secret_payload: dict[str, Any],
    ) -> CloudAccount:
        cloud = cloud.lower().strip()
        acc_id = f"{cloud}:{label}"
        secret_id = f"cloud:{acc_id}"
        safe_meta = strip_forbidden_keys(meta)
        assert_no_secrets(safe_meta)
        # secrets only in vault
        self.vault.put(secret_id, json.dumps(secret_payload, ensure_ascii=False))
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO cloud_accounts(id, cloud, label, meta_json, secret_id)
                   VALUES (?,?,?,?,?)""",
                (acc_id, cloud, label, meta_dumps(safe_meta), secret_id),
            )
            conn.commit()
        out = self.get(acc_id)
        assert out is not None
        return out

    def get(self, acc_id: str) -> CloudAccount | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM cloud_accounts WHERE id=?", (acc_id,)
            ).fetchone()
        if not row:
            return None
        return CloudAccount(
            id=row["id"],
            cloud=row["cloud"],
            label=row["label"],
            meta=meta_loads(row["meta_json"]),
            secret_id=row["secret_id"],
        )

    def list(self, cloud: str | None = None) -> list[CloudAccount]:
        with self._conn() as conn:
            if cloud:
                rows = conn.execute(
                    "SELECT * FROM cloud_accounts WHERE cloud=? ORDER BY label",
                    (cloud,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM cloud_accounts ORDER BY cloud, label"
                ).fetchall()
        return [
            CloudAccount(
                id=r["id"],
                cloud=r["cloud"],
                label=r["label"],
                meta=meta_loads(r["meta_json"]),
                secret_id=r["secret_id"],
            )
            for r in rows
        ]

    def secret_dict(self, account: CloudAccount) -> dict[str, Any]:
        raw = self.vault.get(account.secret_id)
        # PEM keys historically landed with literal newlines inside the JSON
        # string (invalid strict JSON). Accept them, then rewrite cleanly.
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = json.loads(raw, strict=False)
            try:
                self.vault.put(
                    account.secret_id, json.dumps(data, ensure_ascii=False)
                )
            except Exception:  # noqa: BLE001
                pass
        if not isinstance(data, dict):
            raise ValueError(f"cloud secret {account.secret_id} is not an object")
        return data

    def public_for_edit(self, account: CloudAccount) -> dict[str, Any]:
        """Panel edit view: public meta + non-secret ID fields (``form``)."""
        payload = account.public()
        secrets: dict[str, Any] = {}
        vault_ok = False
        try:
            secrets = self.secret_dict(account)
            vault_ok = True
        except Exception:  # noqa: BLE001 — missing/corrupt vault entry
            secrets = {}
        payload["form"] = _edit_form_ids(account.cloud, secrets, account.meta)
        # Lazy heal: mirror non-secret IDs into meta so the next vault wipe
        # still leaves the edit form usable.
        if vault_ok and account.cloud in ("oracle", "aliyun", "tencent", "gcp"):
            heal = self._meta_id_heal(account.cloud, secrets, account.meta)
            if heal:
                try:
                    self.update(account.id, meta=heal)
                    refreshed = self.get(account.id)
                    if refreshed:
                        account = refreshed
                        payload = account.public()
                        payload["form"] = _edit_form_ids(
                            account.cloud, secrets, account.meta
                        )
                except Exception:  # noqa: BLE001
                    pass
        # Name must avoid redact.FORBIDDEN substrings (api_key / secret / …).
        signing_set = False
        if account.cloud == "oracle":
            signing_set = bool(secrets.get("private_key"))
        elif account.cloud == "aliyun":
            signing_set = bool(secrets.get("access_key_secret"))
        elif account.cloud == "tencent":
            signing_set = bool(secrets.get("secret_key"))
        elif account.cloud == "gcp":
            signing_set = bool(secrets.get("service_account"))
        payload["signing_set"] = signing_set
        if not vault_ok or not signing_set:
            # Avoid redact.FORBIDDEN substrings (credential / secret / …).
            payload["need_rekey"] = True
        assert_no_secrets(payload)
        return payload

    @staticmethod
    def _meta_id_heal(
        cloud: str, secrets: dict[str, Any], meta: dict[str, Any]
    ) -> dict[str, Any]:
        """Non-secret identifiers to copy from vault → meta (once)."""
        heal: dict[str, Any] = {}
        if cloud == "oracle":
            for key in ("user_ocid", "tenancy_ocid", "fingerprint"):
                if secrets.get(key) and not meta.get(key):
                    heal[key] = secrets[key]
        elif cloud == "aliyun":
            if secrets.get("access_key_id") and not meta.get("ak_id"):
                heal["ak_id"] = secrets["access_key_id"]
        elif cloud == "tencent":
            if secrets.get("secret_id") and not meta.get("tc_id"):
                heal["tc_id"] = secrets["secret_id"]
        elif cloud == "gcp":
            sa = secrets.get("service_account")
            if isinstance(sa, dict):
                if sa.get("client_email") and not meta.get("client_email"):
                    heal["client_email"] = sa["client_email"]
                if sa.get("project_id") and not (
                    meta.get("project_id") or meta.get("project")
                ):
                    heal["project_id"] = sa["project_id"]
        return heal

    @staticmethod
    def ssh_secret_id(account_id: str) -> str:
        return f"cloud-ssh:{account_id}"

    def set_instance_ssh_defaults(
        self,
        acc_id: str,
        *,
        ssh_user: str,
        key_file: Path | None = None,
        key_material: str | None = None,
    ) -> CloudAccount:
        """Store instance-login SSH user + private key for cloud→SSH linking.

        The key is for VM login (opc/ubuntu/…), not the cloud API signing key.
        """
        acc = self.get(acc_id)
        if not acc:
            raise KeyError(acc_id)
        user = (ssh_user or "").strip()
        if not user:
            raise ValueError("ssh_user required")
        if key_file is not None and key_material is not None:
            raise ValueError("provide only one of key_file or key_material")
        if key_file is None and key_material is None:
            raise ValueError("provide key_file or key_material")
        text = key_material
        path_note: str | None = None
        if key_file is not None:
            key_path = Path(key_file).expanduser()
            if not key_path.is_file():
                raise FileNotFoundError(key_path)
            text = key_path.read_text(encoding="utf-8")
            path_note = str(key_path)
        if not text or not str(text).strip():
            raise ValueError("ssh private key empty")
        sid = self.ssh_secret_id(acc.id)
        self.vault.put(sid, str(text))
        meta = dict(acc.meta)
        meta["ssh_user"] = user
        meta["ssh_key_set"] = True
        if path_note:
            meta["ssh_key_path"] = path_note
        safe = strip_forbidden_keys(meta)
        assert_no_secrets(safe)
        with self._conn() as conn:
            conn.execute(
                "UPDATE cloud_accounts SET meta_json=? WHERE id=?",
                (meta_dumps(safe), acc.id),
            )
            conn.commit()
        out = self.get(acc.id)
        assert out is not None
        return out

    def instance_ssh_defaults(
        self, account: CloudAccount
    ) -> tuple[str | None, str | None]:
        """Return (ssh_user, private_key_pem) or (None, None) if unset."""
        user = (account.meta.get("ssh_user") or "").strip() or None
        sid = self.ssh_secret_id(account.id)
        if not user or not self.vault.has(sid):
            return None, None
        try:
            return user, self.vault.get(sid)
        except Exception:  # noqa: BLE001
            return user, None

    def update(
        self,
        acc_id: str,
        *,
        meta: dict[str, Any] | None = None,
        secret_payload: dict[str, Any] | None = None,
    ) -> CloudAccount:
        """Patch an existing account. Empty secret values keep the previous vault entry."""
        acc = self.get(acc_id)
        if not acc:
            raise KeyError(acc_id)
        if meta is not None:
            merged_meta = dict(acc.meta)
            merged_meta.update(strip_forbidden_keys(meta))
            safe = strip_forbidden_keys(merged_meta)
            assert_no_secrets(safe)
            with self._conn() as conn:
                conn.execute(
                    "UPDATE cloud_accounts SET meta_json=? WHERE id=?",
                    (meta_dumps(safe), acc.id),
                )
                conn.commit()
        if secret_payload is not None:
            try:
                existing = self.secret_dict(acc)
            except (SecretNotFound, ValueError, json.JSONDecodeError):
                # Vault wipe / first re-key after orphan — start fresh.
                existing = {}
            for key, val in secret_payload.items():
                if val is None:
                    continue
                if isinstance(val, str) and not val.strip():
                    continue
                existing[key] = val
            self.vault.put(
                acc.secret_id, json.dumps(existing, ensure_ascii=False)
            )
        out = self.get(acc.id)
        assert out is not None
        return out

    def delete(self, acc_id: str) -> bool:
        acc = self.get(acc_id)
        if not acc:
            return False
        if self.vault.has(acc.secret_id):
            self.vault.delete(acc.secret_id)
        ssh_sid = self.ssh_secret_id(acc.id)
        if self.vault.has(ssh_sid):
            self.vault.delete(ssh_sid)
        with self._conn() as conn:
            conn.execute("DELETE FROM cloud_accounts WHERE id=?", (acc_id,))
            conn.commit()
        return True
