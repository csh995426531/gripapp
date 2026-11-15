# Grip Architecture

## Components

```
┌─────────────┐     MCP (stdio/SSE)      ┌──────────────────┐
│ AI Clients  │ ◄──────────────────────► │  grip.mcp        │
└─────────────┘                          │  (tools only)    │
                                         └────────┬─────────┘
                                                  │
                    ┌─────────────────────────────┼─────────────────────────────┐
                    ▼                             ▼                             ▼
             ┌────────────┐               ┌─────────────┐               ┌────────────┐
             │ vault      │               │ task store  │               │ resources  │
             │ (keyring/  │               │ (SQLite)    │               │ inventory  │
             │  ferrets)  │               └──────┬──────┘               └─────┬──────┘
             └─────┬──────┘                      │                            │
                   │                             │                            │
                   └──────────────► executors ◄──┴────────────────────────────┘
                                    (ssh / oci CLI)
                                         │
                                         ▼
                               cloud / LAN machines

┌─────────────┐   read-only poll   ┌──────────────────┐
│ Menubar App │ ◄─────────────────► │ status JSON API  │
└─────────────┘   ~/.grip/status.json or local HTTP   │  (same SQLite)
```

## Data locations (default)

| Path | Content |
|------|---------|
| `~/.grip/config.toml` | Non-secret settings |
| `~/.grip/grip.db` | Resources metadata + tasks (no raw secrets) |
| OS keyring / `~/.grip/vault.enc` | Secrets (API keys, SSH private key material or path refs) |
| `~/.grip/status.json` | Snapshot for menubar (redacted) |

## Trust boundary

* **Inside Grip process**: may decrypt secrets for execution only.
* **MCP wire to AI**: redacted DTOs only.
* **Menubar / status.json**: redacted only.
* **Logs**: redacted; never log Authorization headers or PEM bodies.

## Relation to noc-ops (research)

Source: [`.trellis-scratch/research/noc-ops-mcp-capabilities.md`](../../../.trellis-scratch/research/noc-ops-mcp-capabilities.md) ([Probe noc-ops MCP APIs](d8ae606b-466c-4029-bb1d-2f37953e41f3)).

| Topic | noc-ops today | Grip stance |
|-------|---------------|-------------|
| MCP | Yes — `ops-mcp` / `multicloud-ops`, ~24 tools | Keep a **smaller** secure tool surface focused on name-based ops + tasks |
| Secret hygiene | Config/Keychain good; **no systematic MCP stdout redaction** | **Hard requirement**: redact MCP/task/menubar payloads |
| SSH / LAN | Deep (ssh config sync, local-servers, Tailscale, ttyd) | MVP: explicit register + vault; later may import noc-ops inventory |
| Task UX | Ops audit log + EventBridge schedules; **not** menubar task board | First-class local task store + menubar |

**Implication:** Do not replace Grip with raw `ops-mcp` for the product vision — gap is output filtering + human task observability. Optional later: adapter that syncs noc-ops `data.json` into Grip inventory while keeping Grip’s redaction gateway.

## Tech stack (MVP)

* Python ≥3.11, package `grip`
* MCP: official `mcp` Python SDK 2.x (`MCPServer`, stdio)
* DB: SQLite via stdlib `sqlite3`
* SSH: system `ssh` subprocess + temp key file from vault
* Cloud: **thin signed HTTPS** (`grip.cloud_api`) for Aliyun / Tencent / Oracle / GCP inventory + monitor only — **no vendor SDKs / CLIs**
* Credentials: `cloud-add` → vault; AccountStore meta only in SQLite
* Menubar: native `NSStatusItem` + `NSPopover`/`WKWebView` on macOS (`grip start`)
* Crypto: `cryptography.Fernet` + machine-local key file

## Work packages

| ID | Name | Depends |
|----|------|---------|
| WP1 | Scaffold + config + DB schema | — |
| WP2 | Vault + redaction | WP1 |
| WP3 | Resources + SSH executor | WP2 |
| WP4 | Task store + MCP tools | WP3 |
| WP5 | Oracle provider (CLI) | WP1 |
| WP6 | Menubar + status snapshot | WP4 |
| WP7 | Docs + tests + DoD | WP4–WP6 |
