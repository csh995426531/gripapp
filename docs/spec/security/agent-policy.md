# Agent policy (MCP instructions)

Canonical soft policy for **aligned** AI hosts that connect to Grip via MCP.  
End users get this through the MCP `initialize` → `instructions` field (and tool descriptions), **not** via repo rule files.

## Must

1. Manage devices, run remote commands, and read audit **only via Grip MCP tools** (`list_resources`, `run_command`, `get_task`, `export_audit`, …).
2. Refer to hosts by **logical `name`**. Never ask the user to paste SSH passwords, private keys, or cloud AccessKeys into chat for you to hold.
3. If credentials are needed, tell the human to enter them in the **Grip menubar panel** (or `grip` CLI) so they land in the vault.

## Must not

1. **Do not read, copy, search, or print** under `~/.grip/` / `$GRIP_HOME` — especially `vault.enc`, `master.key`, `grip.db`, and Keychain items for service `grip`.
2. **Do not delete, truncate, rewrite, or “clean up”** Grip DB / vault / task events / audit exports to hide work.
3. **Do not** reverse-engineer Grip to extract secrets or bypass MCP redaction.
4. **Do not** suggest decrypting `vault.enc` or opening `grip.db` to “debug credentials”.

## If blocked

Use MCP tools. If a tool is missing, ask the human to enable the Grip MCP server — do not work around via the filesystem.

## Honesty

Well-behaved hosts that inject server `instructions` usually comply.  
Adversarial agents that ignore instructions are out of scope; see [secrets.md](secrets.md) (Keychain + audit integrity).
