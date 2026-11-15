from __future__ import annotations

"""Detach remote command runner with task_id marks + JSONL log for reconcile.

Unix: nohup + GRIP_TASK_ID (survives SSH SIGHUP).
Windows: Start-Process hidden PowerShell with GRIP_TASK_ID.
Remote log: ~/.grip/agent/tasks.jsonl (~24h retention).
Stdout markers: __GRIP_STARTED__ / __GRIP_FINISHED__ / __GRIP_OUT_B64__
Output: full stream on host out-<id>.log; Grip only pulls a truncated tail
(OUT_TAIL_MAX bytes) so huge logs never flood SQLite/MCP.
"""

import base64
import os
import re
import shlex
from dataclasses import dataclass

from grip.resources import Resource
from grip.ssh_exec import run_ssh
from grip.vault import Vault

STARTED_RE = re.compile(r"__GRIP_STARTED__\s+(\S+)(?:\s+(\d+))?")
FINISHED_RE = re.compile(r"__GRIP_FINISHED__\s+(\S+)\s+(-?\d+)")
OUT_B64_RE = re.compile(r"__GRIP_OUT_B64__\s+(\S+)\s+(\d+)\s+(\S+)")

REMOTE_LOG_REL = ".grip/agent/tasks.jsonl"
# Last N bytes of remote stdout/stderr returned to Grip (enough to debug).
OUT_TAIL_MAX = 8192
RETENTION_HOURS = 24


def remote_agent_enabled() -> bool:
    return (os.environ.get("GRIP_REMOTE_AGENT") or "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _is_windows(resource: Resource) -> bool:
    return (resource.meta.get("os") or "").lower() in {"windows", "win", "win32"}


def decode_out_tail(b64: str | None, *, out_bytes: int | None = None) -> str:
    """Decode base64 tail; prefix a notice when the remote log was truncated."""
    if not b64:
        return ""
    try:
        raw = base64.b64decode(b64.encode("ascii"), validate=False)
    except Exception:  # noqa: BLE001
        return ""
    text = raw.decode("utf-8", errors="replace")
    total = int(out_bytes) if out_bytes is not None else len(raw)
    if total > len(raw):
        return f"[…truncated, showing last {len(raw)} of {total} bytes…]\n{text}"
    return text


def parse_agent_markers(
    stdout: str,
) -> tuple[bool, str | None, int | None, bool, int | None, str]:
    """Return (started, task_id, pid, finished, exit_code, out_tail_text)."""
    started = False
    tid: str | None = None
    pid: int | None = None
    finished = False
    exit_code: int | None = None
    out_tail = ""
    for line in (stdout or "").splitlines():
        m = STARTED_RE.search(line)
        if m:
            started = True
            tid = m.group(1)
            if m.group(2):
                try:
                    pid = int(m.group(2))
                except ValueError:
                    pid = None
        m2 = FINISHED_RE.search(line)
        if m2:
            finished = True
            tid = m2.group(1)
            try:
                exit_code = int(m2.group(2))
            except ValueError:
                exit_code = None
        m3 = OUT_B64_RE.search(line)
        if m3:
            try:
                nbytes = int(m3.group(2))
            except ValueError:
                nbytes = None
            out_tail = decode_out_tail(m3.group(3), out_bytes=nbytes)
    return started, tid, pid, finished, exit_code, out_tail


def build_detached_launch(
    resource: Resource,
    *,
    task_id: str,
    command: str,
    wait_timeout_sec: int = 60,
) -> str:
    """Remote script: detach job, emit STARTED, poll log for FINISHED (or exit 0 to orphan)."""
    if _is_windows(resource):
        return _windows_launch(task_id, command, wait_timeout_sec)
    return _unix_launch(task_id, command, wait_timeout_sec)


def _unix_emit_out_snippet() -> str:
    """Shell fragment: echo __GRIP_OUT_B64__ from $OUT (uses $TID)."""
    return f"""
if [ -f "$OUT" ]; then
  BYTES=$(wc -c < "$OUT" | tr -d ' ')
  OB64=$(tail -c {OUT_TAIL_MAX} "$OUT" 2>/dev/null | base64 | tr -d '\\n\\r')
  echo "__GRIP_OUT_B64__ $TID ${{BYTES:-0}} $OB64"
fi
""".strip()


def _unix_launch(task_id: str, command: str, wait_timeout_sec: int) -> str:
    b64 = base64.b64encode(command.encode("utf-8")).decode("ascii")
    tid = shlex.quote(task_id)
    wait_s = max(5, int(wait_timeout_sec))
    emit_out = _unix_emit_out_snippet()
    # Outer script is what SSH runs. Inner job is nohup'd and ignores hangup.
    # Command stdout/stderr → out-$TID.log (full). JSONL end carries out_b64 tail.
    return f"""
set +e
TID={tid}
B64={shlex.quote(b64)}
LOG="$HOME/{REMOTE_LOG_REL}"
OUT="$HOME/.grip/agent/out-$TID.log"
mkdir -p "$(dirname "$LOG")"
NOW=$(date +%s 2>/dev/null || echo 0)
CUT=$((NOW - {RETENTION_HOURS} * 3600))
if [ -f "$LOG" ] && [ "$NOW" -gt 0 ]; then
  TMP="$LOG.tmp.$$"
  : > "$TMP"
  while IFS= read -r line || [ -n "$line" ]; do
    TS=$(printf '%s\\n' "$line" | sed -n 's/.*"ts"[[:space:]]*:[[:space:]]*\\([0-9][0-9]*\\).*/\\1/p')
    if [ -z "$TS" ] || [ "$TS" -ge "$CUT" ] 2>/dev/null; then
      printf '%s\\n' "$line" >> "$TMP"
    fi
  done < "$LOG"
  mv -f "$TMP" "$LOG" 2>/dev/null || true
fi
printf '%s\\n' "{{\\"id\\":\\"$TID\\",\\"event\\":\\"start\\",\\"ts\\":$NOW}}" >> "$LOG"
: > "$OUT"
nohup env GRIP_TASK_ID="$TID" GRIP_LOG="$LOG" GRIP_OUT="$OUT" GRIP_CMD_B64="$B64" GRIP_TAIL={OUT_TAIL_MAX} sh -c '
  CMD=$(printf %s "$GRIP_CMD_B64" | base64 -d 2>/dev/null || printf %s "$GRIP_CMD_B64" | base64 -D 2>/dev/null)
  eval "$CMD" >"$GRIP_OUT" 2>&1
  ec=$?
  end=$(date +%s 2>/dev/null || echo 0)
  BYTES=$(wc -c < "$GRIP_OUT" 2>/dev/null | tr -d " ")
  OB64=$(tail -c "$GRIP_TAIL" "$GRIP_OUT" 2>/dev/null | base64 | tr -d "\\n\\r")
  printf "%s\\n" "{{\\"id\\":\\"$GRIP_TASK_ID\\",\\"event\\":\\"end\\",\\"ts\\":$end,\\"exit\\":$ec,\\"out_bytes\\":${{BYTES:-0}},\\"out_b64\\":\\"$OB64\\"}}" >> "$GRIP_LOG"
  exit $ec
' >/dev/null 2>&1 &
PID=$!
echo "__GRIP_STARTED__ $TID $PID"
DEADLINE=$(( $(date +%s) + {wait_s} ))
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  LINE=$(grep "\\"id\\":\\"$TID\\"" "$LOG" 2>/dev/null | grep '"event":"end"' | tail -1)
  if [ -n "$LINE" ]; then
    EC=$(printf '%s\\n' "$LINE" | sed -n 's/.*"exit"[[:space:]]*:[[:space:]]*\\(-*[0-9][0-9]*\\).*/\\1/p')
    echo "__GRIP_FINISHED__ $TID ${{EC:-0}}"
    OB64=$(printf '%s\\n' "$LINE" | sed -n 's/.*"out_b64"[[:space:]]*:[[:space:]]*"\\([^"]*\\)".*/\\1/p')
    BYTES=$(printf '%s\\n' "$LINE" | sed -n 's/.*"out_bytes"[[:space:]]*:[[:space:]]*\\([0-9][0-9]*\\).*/\\1/p')
    if [ -n "$OB64" ]; then
      echo "__GRIP_OUT_B64__ $TID ${{BYTES:-0}} $OB64"
    else
      {emit_out}
    fi
    exit 0
  fi
  sleep 0.5
done
# Still running after wait window — SSH may drop; job keeps going for reconcile
exit 0
""".strip()


def _windows_launch(task_id: str, command: str, wait_timeout_sec: int) -> str:
    b64 = base64.b64encode(command.encode("utf-8")).decode("ascii")
    tid = task_id.replace("'", "''")
    wait_s = max(5, int(wait_timeout_sec))
    return f"""
$ErrorActionPreference='Continue'
$Tid='{tid}'
$B64='{b64}'
$TailMax={OUT_TAIL_MAX}
$Log=Join-Path $env:USERPROFILE '.grip\\agent\\tasks.jsonl'
$Out=Join-Path $env:USERPROFILE ('.grip\\agent\\out-'+$Tid+'.log')
New-Item -ItemType Directory -Force -Path (Split-Path $Log) | Out-Null
$now=[DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
$cut=$now - {RETENTION_HOURS}*3600
if (Test-Path $Log) {{
  $kept=@()
  Get-Content $Log -ErrorAction SilentlyContinue | ForEach-Object {{
    if ($_ -match '"ts"\\s*:\\s*(\\d+)') {{ if ([int64]$Matches[1] -ge $cut) {{ $kept += $_ }} }} else {{ $kept += $_ }}
  }}
  Set-Content -Path $Log -Value $kept -Encoding utf8
}}
Add-Content -Path $Log -Value ('{{"id":"'+$Tid+'","event":"start","ts":'+$now+'}}') -Encoding utf8
$worker = @"
`$ErrorActionPreference='Continue'
`$env:GRIP_TASK_ID='$Tid'
`$Log=Join-Path `$env:USERPROFILE '.grip\\agent\\tasks.jsonl'
`$Out=Join-Path `$env:USERPROFILE ('.grip\\agent\\out-'+$env:GRIP_TASK_ID+'.log')
`$TailMax=$TailMax
`$bytes=[Convert]::FromBase64String('$B64')
`$cmd=[Text.Encoding]::UTF8.GetString(`$bytes)
try {{
  `$output = Invoke-Expression `$cmd 2>&1 | Out-String
  if (`$null -ne `$LASTEXITCODE) {{ `$ec=`$LASTEXITCODE }} else {{ `$ec=0 }}
}} catch {{
  `$output = `$_.Exception.Message
  `$ec=1
}}
[IO.File]::WriteAllText(`$Out, `$output)
`$raw=[Text.Encoding]::UTF8.GetBytes(`$output)
`$n=`$raw.Length
`$slice= if (`$n -gt `$TailMax) {{ `$raw[(`$n-`$TailMax)..(`$n-1)] }} else {{ `$raw }}
`$ob64=[Convert]::ToBase64String(`$slice)
`$end=[DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
Add-Content -Path `$Log -Value ('{{"id":"'+$env:GRIP_TASK_ID+'","event":"end","ts":'+$end+',"exit":'+$ec+',"out_bytes":'+$n+',"out_b64":"'+$ob64+'"}}') -Encoding utf8
exit `$ec
"@
# -Command + multi-line $worker often dies before writing end/out; EncodedCommand is reliable.
$workerEnc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($worker))
$ps = Start-Process -FilePath 'powershell.exe' -ArgumentList @('-NoProfile','-NonInteractive','-EncodedCommand',$workerEnc) -WindowStyle Hidden -PassThru
Write-Output ("__GRIP_STARTED__ $Tid $($ps.Id)")
$deadline = (Get-Date).AddSeconds({wait_s})
while ((Get-Date) -lt $deadline) {{
  if (Test-Path $Log) {{
    $line = Get-Content $Log -ErrorAction SilentlyContinue | Where-Object {{ $_ -match [regex]::Escape($Tid) -and $_ -match '"event"\\s*:\\s*"end"' }} | Select-Object -Last 1
    if ($line) {{
      $ec = 0
      if ($line -match '"exit"\\s*:\\s*(-?\\d+)') {{ $ec = [int]$Matches[1] }}
      Write-Output ("__GRIP_FINISHED__ $Tid $ec")
      $ob64 = ''
      $nbytes = 0
      if ($line -match '"out_b64"\\s*:\\s*"([^"]*)"') {{ $ob64 = $Matches[1] }}
      if ($line -match '"out_bytes"\\s*:\\s*(\\d+)') {{ $nbytes = [int64]$Matches[1] }}
      if (-not $ob64 -and (Test-Path $Out)) {{
        $raw = [IO.File]::ReadAllBytes($Out)
        $nbytes = $raw.Length
        $slice = if ($nbytes -gt $TailMax) {{ $raw[($nbytes-$TailMax)..($nbytes-1)] }} else {{ $raw }}
        $ob64 = [Convert]::ToBase64String($slice)
      }}
      if ($ob64) {{ Write-Output ("__GRIP_OUT_B64__ $Tid $nbytes $ob64") }}
      exit 0
    }}
  }}
  Start-Sleep -Milliseconds 500
}}
exit 0
""".strip()


@dataclass
class ReconcileResult:
    found: bool
    finished: bool
    exit_code: int | None = None
    alive: bool = False
    detail: str = ""
    out_tail: str = ""


def reconcile_task_on_host(
    resource: Resource,
    vault: Vault,
    task_id: str,
    *,
    timeout_sec: int = 20,
) -> ReconcileResult:
    """SSH check: JSONL end event and/or live process with task id."""
    if _is_windows(resource):
        tid = task_id.replace("'", "''")
        script = f"""
$Tid='{tid}'
$TailMax={OUT_TAIL_MAX}
$Log=Join-Path $env:USERPROFILE '.grip\\agent\\tasks.jsonl'
$Out=Join-Path $env:USERPROFILE ('.grip\\agent\\out-'+$Tid+'.log')
$alive=$false
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | ForEach-Object {{
  if ($_.CommandLine -and ($_.CommandLine.Contains($Tid) -or $_.CommandLine.Contains('GRIP_TASK_ID'))) {{
    if ($_.CommandLine.Contains($Tid)) {{ $alive=$true }}
  }}
}}
$line=$null
if (Test-Path $Log) {{
  $line=Get-Content $Log -ErrorAction SilentlyContinue | Where-Object {{ $_ -match [regex]::Escape($Tid) -and $_ -match '"event"\\s*:\\s*"end"' }} | Select-Object -Last 1
}}
if ($line) {{
  $ec=0
  if ($line -match '"exit"\\s*:\\s*(-?\\d+)') {{ $ec=[int]$Matches[1] }}
  Write-Output ("FINISHED $ec")
  $ob64=''
  $nbytes=0
  if ($line -match '"out_b64"\\s*:\\s*"([^"]*)"') {{ $ob64=$Matches[1] }}
  if ($line -match '"out_bytes"\\s*:\\s*(\\d+)') {{ $nbytes=[int64]$Matches[1] }}
  if (-not $ob64 -and (Test-Path $Out)) {{
    $raw=[IO.File]::ReadAllBytes($Out)
    $nbytes=$raw.Length
    $slice= if ($nbytes -gt $TailMax) {{ $raw[($nbytes-$TailMax)..($nbytes-1)] }} else {{ $raw }}
    $ob64=[Convert]::ToBase64String($slice)
  }}
  if ($ob64) {{ Write-Output ("OUT_B64 $nbytes $ob64") }}
}} elseif ($alive) {{
  Write-Output 'ALIVE'
}} else {{
  Write-Output 'UNKNOWN'
}}
"""
        result = run_ssh(
            resource, vault, script, timeout_sec=timeout_sec, os_wrap=True
        )
    else:
        tid = shlex.quote(task_id)
        script = f"""
TID={tid}
LOG="$HOME/{REMOTE_LOG_REL}"
OUT="$HOME/.grip/agent/out-$TID.log"
LINE=""
if [ -f "$LOG" ]; then
  LINE=$(grep "\\"id\\":\\"$TID\\"" "$LOG" 2>/dev/null | grep '"event":"end"' | tail -1 || true)
fi
if [ -n "$LINE" ]; then
  EC=$(printf '%s\\n' "$LINE" | sed -n 's/.*"exit"[[:space:]]*:[[:space:]]*\\(-*[0-9][0-9]*\\).*/\\1/p')
  echo "FINISHED ${{EC:-0}}"
  OB64=$(printf '%s\\n' "$LINE" | sed -n 's/.*"out_b64"[[:space:]]*:[[:space:]]*"\\([^"]*\\)".*/\\1/p')
  BYTES=$(printf '%s\\n' "$LINE" | sed -n 's/.*"out_bytes"[[:space:]]*:[[:space:]]*\\([0-9][0-9]*\\).*/\\1/p')
  if [ -z "$OB64" ] && [ -f "$OUT" ]; then
    BYTES=$(wc -c < "$OUT" | tr -d ' ')
    OB64=$(tail -c {OUT_TAIL_MAX} "$OUT" 2>/dev/null | base64 | tr -d '\\n\\r')
  fi
  if [ -n "$OB64" ]; then
    echo "OUT_B64 ${{BYTES:-0}} $OB64"
  fi
  exit 0
fi
if pgrep -f "GRIP_TASK_ID=$TID" >/dev/null 2>&1; then
  echo ALIVE
  exit 0
fi
echo UNKNOWN
"""
        result = run_ssh(
            resource, vault, script, timeout_sec=timeout_sec, os_wrap=False
        )
    lines = [ln.strip() for ln in (result.stdout or "").splitlines() if ln.strip()]
    finished = False
    exit_code: int | None = None
    alive = False
    detail = ""
    out_tail = ""
    for ln in lines:
        if ln.startswith("FINISHED"):
            finished = True
            parts = ln.split()
            if len(parts) > 1 and parts[1].lstrip("-").isdigit():
                exit_code = int(parts[1])
            else:
                exit_code = 0
            detail = ln
        elif ln.startswith("OUT_B64"):
            parts = ln.split(None, 2)
            nbytes = None
            b64 = ""
            if len(parts) >= 3:
                try:
                    nbytes = int(parts[1])
                except ValueError:
                    nbytes = None
                b64 = parts[2]
            elif len(parts) == 2:
                b64 = parts[1]
            out_tail = decode_out_tail(b64, out_bytes=nbytes)
        elif ln == "ALIVE":
            alive = True
            detail = ln
        elif ln == "UNKNOWN":
            detail = ln
    if finished:
        return ReconcileResult(True, True, exit_code, False, detail, out_tail)
    if alive:
        return ReconcileResult(True, False, None, True, detail or "ALIVE", "")
    return ReconcileResult(
        False, False, None, False, detail or (result.stderr or "unknown"), ""
    )
