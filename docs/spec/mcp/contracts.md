# MCP Contracts (Full Product)

## Tools

| Tool | Args | Returns |
|------|------|---------|
| `list_resources` | `kind?: all\|ssh\|oracle\|gcp\|aliyun\|tencent` | `{ resources: ResourceSummary[] }` |
| `list_cloud_accounts` | `cloud?` | `{ accounts }` — labels/meta only, never secrets |
| `get_resource` | `name` | `{ resource }` |
| `run_command` | `name`, `command`, `timeout_sec?`, `confirm_write?`, `background?`, `await_probe_json?`, `actor?` | `{ task_id, status }` — SSH only; persists `actor` (`mcp` / `mcp:<clientInfo>` / override); default detached agent (Linux/macOS nohup, Windows Start-Process) |
| `reconcile_orphaned` | `name?` | re-check `orphaned`/待核对 via remote JSONL / process markers |
| `export_audit` | `name?`, `format?` jsonl\|json\|csv, `limit?` | redacted audit body (+ metadata); never vault |
| `get_task` / `list_tasks` | ids / filters | redacted task DTOs (include `actor`) |
| `board` | — | resources (L1/L2 health) + tasks by host |
| `probe_resources` | `kind?` | L2 TCP reachability results |
| `watch_once` | `sync_clouds?`, `sample_metrics?` (default true) | one tick: optional L1 sync + L2 probe + L3 metrics |
| `sample_metrics` | `name?` (empty = all SSH) | L3 SSH metrics (cpu/mem/disk/load/uptime/net) |
| `update_ssh` | `name`, optional `host`/`user`/`port`/`os_name`/`password`/`key_file` | public summary only — secrets never echoed |
| `autostart_status` | — | macOS Login Item status |
| `set_autostart` | `enabled` | enable/disable LaunchAgent |
| `sync_cloud` | `cloud: oracle\|gcp\|aliyun\|tencent` | `{ task_id, status, count }` |
| `sync_all` | — | `{ task_id, status, results, errors, ok }` |
| `sync_oracle` | deprecated alias | same as `sync_cloud("oracle")` |

Forbidden keys in all payloads: see `security/secrets.md`.
