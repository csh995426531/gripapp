# Resource Model (Code-Spec)

## 1. Scope / Trigger

Inventory of operable targets (SSH hosts, cloud instances).

## 2. Signatures

```python
# CLI (operator-facing; not MCP)
# grip resource add-ssh --name dev1 --host 192.168.1.10 --user ubuntu --key-file ~/.ssh/id_ed25519
# grip resource list
# grip oracle sync

@dataclass
class Resource:
    name: str                 # unique logical name
    kind: str                 # ssh | oracle_instance
    status: str
    meta: dict                # host, user, oci_id, region, shape — NO secrets
    secret_id: str | None     # vault pointer for ssh kind
```

## 3. Contracts

* MCP addresses resources **only** by `name`.
* SSH executor: `ssh -o BatchMode=yes -i <temp_or_path> user@host command`  
  Prefer writing key to temp file 0600 from vault when material stored; delete after.
* Oracle instances: synced into inventory with `kind=oracle_instance`; read-only in MVP (list/status). Optional later: start/stop via oci CLI behind confirm.

## 4. Validation & Error Matrix

| Case | Behavior |
|------|----------|
| Duplicate name | reject |
| add-ssh without key | reject |
| oracle sync without oci CLI | failed task with clear error |

## 5. Good / Base / Bad

* Good: add-ssh then list_resources shows status after probe.
* Base: empty inventory.
* Bad: meta contains `private_key`.

## 6. Tests Required

* `test_add_ssh_stores_secret_separately`
* `test_resource_summary_safe_for_mcp`

## 7. Wrong vs Correct

* **Wrong**: kind-specific MCP tools that take `--api-key` from the model.  
* **Correct**: operator CLI configures secrets; MCP only uses names.
