# Task Store (Code-Spec)

## 1. Scope / Trigger

Persisted execution records for MCP actions and background syncs.

## 2. Signatures

```python
@dataclass
class Task:
    id: str
    resource_name: str
    action: str
    status: str
    created_at: str
    finished_at: str | None
    exit_code: int | None
    summary: str | None
    error: str | None
    command: str | None  # stored but redacted on read for MCP if needed
    actor: str  # panel | mcp | mcp:<client> | cli | unknown | custom

class TaskStore:
    def create(self, ..., actor: str | None = None) -> Task: ...
    def set_running(self, task_id: str) -> None: ...
    def finish(self, task_id: str, *, status: str, exit_code: int | None, summary: str | None, error: str | None) -> None: ...
    def get(self, task_id: str) -> Task | None: ...
    def list(self, *, limit: int = 20, status: str | None = None) -> list[Task]: ...
    def list_audit(self, *, name: str | None = None, limit: int = 2000, action: str | None = None) -> list[Task]: ...
```

SQLite table `tasks` in `~/.grip/grip.db`. Column `actor` migrates with default `unknown` for old rows.

## 3. Contracts

* Every MCP mutating/exec tool creates a task **before** execution.
* `summary`/`error` always passed through `redact_text`, max 2048 chars.
* Status machine: `queued → running → success|failed` or `queued → denied`.
* `actor` is set by caller: panel UI → `panel`, CLI → `cli`, MCP → `mcp` (or `mcp:<clientInfo.name>` / explicit `actor`).
* Audit export (`export_audit` / `grip audit-export` / `GET /api/audit/export`) never dumps vault; command/detail are redacted.

## 4. Validation & Error Matrix

| Case | Behavior |
|------|----------|
| finish unknown id | raise |
| list limit > 100 | clamp to 100 |

## 5. Good / Base / Bad

* Good: run_command creates queued then success.
* Base: list empty.
* Bad: store raw PEM in summary.

## 6. Tests Required

* `test_task_lifecycle_success`
* `test_task_summary_truncated_and_redacted`

## 7. Wrong vs Correct

* **Wrong**: only return stdout to MCP and forget persistence (menubar blind).  
* **Correct**: persist first, then return `task_id` + status.
