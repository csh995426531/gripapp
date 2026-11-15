# Execution Status Sync — Complete Scheme (Landed)

## Product rule

Grip answers only: **has this MCP-triggered operation started, is its execution channel still active, or has it finished?**  
It does **not** interpret business outcomes (DB rows, files, UI clicks).

## Architecture (perfect path for v1)

```
AI --MCP--> Grip
              | 1. create task (queued) + event
              | 2. mark running + heartbeat loop
              | 3. open OS executor session (SSH; Windows via OpenSSH/PowerShell)
              | 4. session ends -> exit_code
              | 5. optional await_probe (file|tcp|http) while phase=probing
              | 6. finish success|failed + event
              v
         SQLite tasks + task_events  == source of truth
              |
    board / menubar / get_task / list_tasks  (read local only)
```

## States

| status | meaning |
|--------|---------|
| `queued` | accepted, not started |
| `running` | executor session active (heartbeats) |
| `probing` | session exited 0; optional completion probe still waiting |
| `success` | session (+ probe if any) OK |
| `failed` | non-zero exit, probe timeout/fail, transport error, host unreachable, or stale watchdog |
| `orphaned` | remote job **started** (detached) but Grip lost the wait session — panel shows 待核对; reconcile later |
| `denied` | write gate blocked |

## Reachability gate

* `run_command` only proceeds to `running` when the SSH host looks live: status is not `unreachable`, and a cheap **TCP preflight** succeeds (~2s).
* Otherwise the task finishes immediately as `failed` with `主机不可达 (TCP超时)` — it must **not** linger in 执行中 while SSH connect hangs.
* Panel disables 「执行」 while the device badge is 不可达.

## SSH concurrency & session reuse

* **Per-host cap**: at most `GRIP_SSH_MAX_PER_HOST` (default **3**) concurrent SSH sessions per device; additional callers **queue** (wait up to command timeout).
* Shared by `run_command`, metrics, OS probe — so a burst of AI tasks cannot open unlimited sessions.
* **Reuse**: password auth keeps a paramiko client across calls (idle ~180s); key auth uses OpenSSH `ControlMaster` / `ControlPersist` under `GRIP_HOME/ssh-mux/`. Metrics every ~10s should not full-handshake each time when the mux/session is warm.

## Detached remote agent (default)

* Default on (`GRIP_REMOTE_AGENT=1`). Set `0`/`false`/`off` for legacy foreground SSH.
* **Linux / macOS**: `nohup` + `GRIP_TASK_ID`; markers `__GRIP_STARTED__` / `__GRIP_FINISHED__` / `__GRIP_OUT_B64__`; log `~/.grip/agent/tasks.jsonl` (~24h retention).
* **Windows**: `Start-Process` hidden PowerShell with the same markers/log under `%USERPROFILE%\.grip\agent\`.
* Full stdout/stderr stays on the host (`out-<task_id>.log`). Grip only pulls a **truncated tail** (~8KB) into the task summary — enough to debug, never floods SQLite/MCP on huge output.
* Grip waits up to `timeout_sec` for FINISHED. If STARTED was seen but not FINISHED → `orphaned` (待核对); job keeps going on the host.
* SSH drop **after** STARTED also → `orphaned` (not a false `failed`). Drop **before** STARTED → `failed`.

## Disconnect / reconnect

* Mid-wait link drop after STARTED → `orphaned`; reconcile on `probe_resources` / `watch_once` / MCP `reconcile_orphaned`.
* Reconcile reads JSONL end event or live process with `GRIP_TASK_ID` → `success`/`failed`, or stays `orphaned` if still alive/unknown.
* New commands still create **new** tasks; reconcile only updates existing orphaned rows.

## Stale watchdog

* `running` / `probing` tasks whose `last_heartbeat_at` (else `created_at`) is older than ~90s are reaped as `failed` / `stale_timeout` on `refresh_status` (board / menubar tick).
* `orphaned` is **not** reaped — awaits remote reconcile.

## Contracts

1. **Detach default**: remote jobs use nohup / Start-Process (not a foreground SSH-tied process).
2. **Completion probes**: optional `await_probe` (file/tcp/http) after a confirmed zero exit.
3. **Multi-host**: one task per resource; parallel background workers; board groups by `resource_name`.
4. **Windows / Linux / macOS**: same SSH transport; `meta.os` selects PowerShell vs sh/nohup wrappers.

## MCP

* `run_command(..., background=true, await_probe=null|{type,target,timeout_sec})`
* `reconcile_orphaned(name?)` — re-check 待核对 tasks
* `get_task` / `list_tasks` / `board` — include `last_heartbeat_at`, `phase`, events summary
