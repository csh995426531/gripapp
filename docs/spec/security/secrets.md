# Secrets & Redaction (Code-Spec)

## 1. Scope / Trigger

Vault storage, encryption, output redaction, and audit integrity. Touches every MCP/task/menubar path.

## 2. Signatures

```python
class Vault:
    def put(self, secret_id: str, plaintext: str) -> None: ...
    def get(self, secret_id: str) -> str: ...          # process-internal only
    def delete(self, secret_id: str) -> None: ...
    @property
    def backend(self) -> Literal["keyring", "file"]: ...
    def audit_hmac_key(self) -> bytes: ...             # HKDF from master

def redact_text(text: str) -> str: ...
def assert_no_secrets(payload: dict) -> None: ...      # raises if forbidden keys/patterns
```

Forbidden JSON keys (case-insensitive substring match):  
`password`, `private_key`, `api_key`, `access_key`, `secret`, `token`, `pem`, `credential`, `ssh_key`.

Forbidden body patterns: PEM headers, `AKIA[0-9A-Z]{16}`, long base64 key blobs (heuristic).

## 3. Contracts

* Secrets referenced by opaque `secret_id` on resource rows; never store plaintext in SQLite.
* **Master key (primary)**: OS keyring service `grip`, account `master` (macOS Keychain / libsecret / Windows Credential Locker via `keyring`).
* **Master key (fallback)**: file `GRIP_HOME/master.key` mode `0600` when keyring is disabled (`GRIP_KEYRING=0`) or unavailable — **weak vs same-user AI**; emits a warning.
* One-shot migrate: if `master.key` exists and keyring accepts the key → store in keyring and **delete** the file.
* Payload encryption: Fernet(master).
* Audit events: HMAC-SHA256 chain (`prev_hash` → `event_hash`) with key = HKDF(master, info=`grip-audit-v1`). Sealed head in `grip_meta.audit_head_hash`.
* `board.security`: `{ vault_backend, audit_integrity: ok|broken|unknown }`.
* MCP never returns vault plaintext or master key material.

## 4. Threat model (honest)

| Adversary | What Grip does | What Grip cannot do |
|-----------|----------------|---------------------|
| MCP / AI tools (intended path) | Never echoes secrets; name-based ops only | — |
| Same-user AI that only uses MCP | Secrets stay in process | — |
| Same-user AI with unrestricted FS/shell | No on-disk `master.key` when keyring works; quiet audit wipe → `audit_integrity=broken` | Absolute prevention (Keychain APIs, kill Grip, delete `GRIP_HOME`) |
| Other OS users / disk casual peek | Fernet + 0700 home + keyring | Stolen unlocked login session |

**Out of scope:** same-user agent with full filesystem and credential-store access that **ignores** instructions.

**Aligned-agent layer (prompt policy):** delivered only via the **MCP protocol** when a host connects to `grip mcp`:

* Server `instructions` + tool descriptions (see `grip.mcp_server`)
* Written form for maintainers: [agent-policy.md](agent-policy.md)

Repo-local agent rule files are **not** a product control — end-user installs never see them.  
This does **not** stop a deliberately hostile agent; it nudges normal assistants away from casually reading the vault. Combine with Keychain + audit integrity.

## 5. Validation & Error Matrix

| Case | Behavior |
|------|----------|
| `get` unknown id | raise `SecretNotFound` |
| Redact PEM | replace block with `***REDACTED_PEM***` |
| Menubar dump | must run `assert_no_secrets` |
| Truncated `task_events` with leftover head | `audit_integrity=broken` |
| Keyring unavailable | file backend + warning |

## 6. Good / Base / Bad

* Good: put/get roundtrip; keyring backend; MCP never sees plaintext; chain verifies `ok`.
* Base: empty vault; empty events → integrity `ok`.
* Bad: logging `vault.get(...)` return value; leaving `master.key` on disk in production.

## 7. Tests Required

* `test_vault_roundtrip`
* `test_vault_migrates_master_key_to_keyring`
* `test_redact_pem`
* `test_assert_no_secrets_rejects_api_key_field`
* `test_audit_integrity_ok_after_events`
* `test_audit_integrity_broken_after_wipe`

## 8. Wrong vs Correct

* **Wrong**: pass SSH private key string into MCP tool schema “for the model to use ssh”.  
* **Correct**: model passes `name=web1`; Grip loads secret_id `ssh:web1` internally.
* **Wrong**: assume file encryption alone stops local AI from reading secrets.  
* **Correct**: keyring + MCP boundary + audit integrity detection + keep `~/.grip` out of agent scope.
