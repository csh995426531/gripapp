from __future__ import annotations

"""L3 host metrics via SSH (no agent). Common: load, memory, disk, uptime."""

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from grip.monitor import tcp_open
from grip.resources import Resource
from grip.ssh_exec import detect_remote_os, needs_os_probe, run_ssh
from grip.vault import Vault

# Shown in panel tip as-is (「采样失败: …」); keep Chinese-friendly.
METRICS_HOST_UNREACHABLE = "主机不可达 (TCP超时)"

# One remote script: Linux (/proc) or macOS (sysctl/vm_stat/df/ps).
# Linux: 1s /proc/stat window (0.5s often rounds idle multi-core to 0% CPU).
# macOS: ~0.5s for net rates; CPU via ps (avoids multi-sample top under load).
# Net counters soft-fail to 0 so a bad iface never aborts the whole sample.
METRICS_SH = r"""
set +e
cpu_used_pct=0
net_rx_kbps=0
net_tx_kbps=0
net_iface=""
if [ -r /proc/loadavg ]; then
  load1=$(awk '{print $1}' /proc/loadavg)
  # default iface (fallback eth0/ens*)
  net_iface=$(ip route 2>/dev/null | awk '/default/{print $5; exit}')
  if [ -z "$net_iface" ]; then net_iface=$(ls /sys/class/net 2>/dev/null | awk '$0!="lo"{print; exit}'); fi
  rx1=$(awk -v i="$net_iface" -F'[: ]+' '$1==i{print $2+0; exit}' /proc/net/dev 2>/dev/null)
  tx1=$(awk -v i="$net_iface" -F'[: ]+' '$1==i{print $10+0; exit}' /proc/net/dev 2>/dev/null)
  rx1=${rx1:-0}; tx1=${tx1:-0}
  read -r _ u1 n1 s1 i1 w1 x1 y1 z1 _ < /proc/stat
  idle1=$i1; total1=$((u1+n1+s1+i1+w1+x1+y1+z1))
  # 1s window: 0.5s often rounds idle multi-core hosts to 0% CPU.
  sleep 1
  read -r _ u2 n2 s2 i2 w2 x2 y2 z2 _ < /proc/stat
  idle2=$i2; total2=$((u2+n2+s2+i2+w2+x2+y2+z2))
  didle=$((idle2-idle1)); dtotal=$((total2-total1))
  if [ "$dtotal" -gt 0 ]; then
    cpu_used_pct=$(( (100 * (dtotal - didle)) / dtotal ))
  fi
  rx2=$(awk -v i="$net_iface" -F'[: ]+' '$1==i{print $2+0; exit}' /proc/net/dev 2>/dev/null)
  tx2=$(awk -v i="$net_iface" -F'[: ]+' '$1==i{print $10+0; exit}' /proc/net/dev 2>/dev/null)
  rx2=${rx2:-0}; tx2=${tx2:-0}
  net_rx_kbps=$(( (rx2 - rx1) * 8 / 1000 ))
  net_tx_kbps=$(( (tx2 - tx1) * 8 / 1000 ))
  mem_total_kb=$(awk '/^MemTotal:/{print $2}' /proc/meminfo)
  mem_avail_kb=$(awk '/^MemAvailable:/{print $2}' /proc/meminfo)
  if [ -z "$mem_avail_kb" ]; then
    mem_free_kb=$(awk '/^MemFree:/{print $2}' /proc/meminfo)
    mem_cached_kb=$(awk '/^Cached:/{print $2}' /proc/meminfo)
    mem_avail_kb=$((mem_free_kb + mem_cached_kb))
  fi
  mem_total_mb=$((mem_total_kb / 1024))
  mem_used_mb=$(( (mem_total_kb - mem_avail_kb) / 1024 ))
  if [ "$mem_total_mb" -gt 0 ]; then
    mem_used_pct=$(( mem_used_mb * 100 / mem_total_mb ))
  else
    mem_used_pct=0
  fi
  uptime_sec=$(awk '{print int($1)}' /proc/uptime)
  os_family=linux
else
  load1=$(sysctl -n vm.loadavg 2>/dev/null | awk '{gsub(/[{}]/,""); print $1}')
  net_iface=$(route -n get default 2>/dev/null | awk '/interface:/{print $2; exit}')
  if [ -z "$net_iface" ]; then net_iface=en0; fi
  rx1=$(netstat -ibn 2>/dev/null | awk -v i="$net_iface" '$1==i && $3 ~ /^<Link/ {print $(NF-4)+0; exit}')
  tx1=$(netstat -ibn 2>/dev/null | awk -v i="$net_iface" '$1==i && $3 ~ /^<Link/ {print $(NF-1)+0; exit}')
  rx1=${rx1:-0}; tx1=${tx1:-0}
  # Net rate needs a short window. CPU via ps (instant) — avoid multi-sample
  # top which stalls ~1–2s+ under load (and kern.cp_time is gone on Apple Silicon).
  sleep 0.5
  ncpu=$(sysctl -n hw.ncpu 2>/dev/null || echo 1)
  cpu_used_pct=$(ps -A -o %cpu= 2>/dev/null | awk -v n="$ncpu" '{s+=$1} END {if(n+0>0) printf "%d", int(s/n+0.5); else print 0}')
  rx2=$(netstat -ibn 2>/dev/null | awk -v i="$net_iface" '$1==i && $3 ~ /^<Link/ {print $(NF-4)+0; exit}')
  tx2=$(netstat -ibn 2>/dev/null | awk -v i="$net_iface" '$1==i && $3 ~ /^<Link/ {print $(NF-1)+0; exit}')
  rx2=${rx2:-0}; tx2=${tx2:-0}
  net_rx_kbps=$(( (rx2 - rx1) * 8 / 500 ))
  net_tx_kbps=$(( (tx2 - tx1) * 8 / 500 ))
  mem_total_b=$(sysctl -n hw.memsize 2>/dev/null)
  mem_total_mb=$((mem_total_b / 1024 / 1024))
  pagesize=$(pagesize 2>/dev/null || echo 4096)
  vm_stat_out=$(vm_stat 2>/dev/null)
  pages_active=$(printf '%s\n' "$vm_stat_out" | awk '/Pages active/ {gsub(/\./,"",$3); print $3+0}')
  pages_wired=$(printf '%s\n' "$vm_stat_out" | awk '/Pages wired/ {gsub(/\./,"",$4); print $4+0}')
  pages_comp=$(printf '%s\n' "$vm_stat_out" | awk '/Pages occupied by compressor/ {gsub(/\./,"",$5); print $5+0}')
  used_pages=$(( ${pages_active:-0} + ${pages_wired:-0} + ${pages_comp:-0} ))
  mem_used_mb=$(( used_pages * pagesize / 1024 / 1024 ))
  if [ "$mem_total_mb" -gt 0 ]; then
    mem_used_pct=$(( mem_used_mb * 100 / mem_total_mb ))
  else
    mem_used_pct=0
  fi
  boot=$(sysctl -n kern.boottime 2>/dev/null | awk -F'[=,]' '{print $2}' | tr -d ' ')
  now=$(date +%s)
  if [ -n "$boot" ] && [ "$boot" -gt 0 ]; then
    uptime_sec=$((now - boot))
  else
    uptime_sec=0
  fi
  os_family=macos
fi
df_line=$(df -kP / 2>/dev/null | tail -1)
disk_total_k=$(printf '%s\n' "$df_line" | awk '{print $2+0}')
disk_used_k=$(printf '%s\n' "$df_line" | awk '{print $3+0}')
disk_avail_k=$(printf '%s\n' "$df_line" | awk '{print $4+0}')
disk_pct_field=$(printf '%s\n' "$df_line" | awk '{print $5}' | tr -d '%')
if [ -n "$disk_pct_field" ] && [ "$disk_pct_field" -eq "$disk_pct_field" ] 2>/dev/null; then
  disk_used_pct=$disk_pct_field
elif [ "$disk_total_k" -gt 0 ]; then
  disk_used_pct=$(( disk_used_k * 100 / disk_total_k ))
else
  disk_used_pct=0
fi
disk_avail_gb=$(( disk_avail_k / 1024 / 1024 ))
case $cpu_used_pct in ''|*[!0-9]*) cpu_used_pct=0 ;; esac
if [ "$cpu_used_pct" -lt 0 ]; then cpu_used_pct=0; fi
if [ "$cpu_used_pct" -gt 100 ]; then cpu_used_pct=100; fi
case $net_rx_kbps in ''|*[!0-9-]*) net_rx_kbps=0 ;; esac
case $net_tx_kbps in ''|*[!0-9-]*) net_tx_kbps=0 ;; esac
if [ "$net_rx_kbps" -lt 0 ]; then net_rx_kbps=0; fi
if [ "$net_tx_kbps" -lt 0 ]; then net_tx_kbps=0; fi
printf 'os_family=%s\n' "$os_family"
printf 'cpu_used_pct=%s\n' "$cpu_used_pct"
printf 'load1=%s\n' "$load1"
printf 'mem_used_pct=%s\n' "$mem_used_pct"
printf 'mem_used_mb=%s\n' "$mem_used_mb"
printf 'mem_total_mb=%s\n' "$mem_total_mb"
printf 'disk_used_pct=%s\n' "$disk_used_pct"
printf 'disk_avail_gb=%s\n' "$disk_avail_gb"
printf 'net_iface=%s\n' "$net_iface"
printf 'net_rx_kbps=%s\n' "$net_rx_kbps"
printf 'net_tx_kbps=%s\n' "$net_tx_kbps"
printf 'uptime_sec=%s\n' "$uptime_sec"
"""

# Windows OpenSSH: raw PowerShell (run_ssh wraps with powershell -Command).
# Avoid single quotes — ssh_exec doubles them for PowerShell escaping.
# Same key=value lines as METRICS_SH so parse_metrics_kv reuses.
# load1 omitted (no Unix load average on Windows).
METRICS_PS1 = r"""
$ErrorActionPreference="Continue"
$os_family="windows"
$cpu_used_pct=0
$mem_used_pct=0
$mem_used_mb=0
$mem_total_mb=0
$disk_used_pct=0
$disk_avail_gb=0
$net_iface=""
$net_rx_kbps=0
$net_tx_kbps=0
$uptime_sec=0
try {
  $cpu_avg=(Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average
  if ($null -ne $cpu_avg) { $cpu_used_pct=[int][math]::Round($cpu_avg) }
} catch {}
if ($cpu_used_pct -lt 0) { $cpu_used_pct=0 }
if ($cpu_used_pct -gt 100) { $cpu_used_pct=100 }
try {
  $os=Get-CimInstance Win32_OperatingSystem
  $mem_total_mb=[int]($os.TotalVisibleMemorySize/1024)
  $mem_free_mb=[int]($os.FreePhysicalMemory/1024)
  $mem_used_mb=$mem_total_mb-$mem_free_mb
  if ($mem_used_mb -lt 0) { $mem_used_mb=0 }
  if ($mem_total_mb -gt 0) { $mem_used_pct=[int](100*$mem_used_mb/$mem_total_mb) }
  if ($os.LastBootUpTime) {
    $uptime_sec=[int]((Get-Date)-$os.LastBootUpTime).TotalSeconds
    if ($uptime_sec -lt 0) { $uptime_sec=0 }
  }
} catch {}
try {
  $disk=Get-CimInstance Win32_LogicalDisk | Where-Object { $_.DriveType -eq 3 -and $_.Size -gt 0 } | Sort-Object Size -Descending | Select-Object -First 1
  if ($disk) {
    $disk_used_pct=[int](100*($disk.Size-$disk.FreeSpace)/$disk.Size)
    $disk_avail_gb=[int]($disk.FreeSpace/1GB)
  }
} catch {}
try {
  $iface=Get-CimInstance Win32_PerfRawData_Tcpip_NetworkInterface | Where-Object { $_.Name -notmatch "Loopback|isatap|Teredo|Pseudo" } | Sort-Object BytesTotalPersec -Descending | Select-Object -First 1
  if ($iface) {
    $net_iface=$iface.Name
    $rx1=[int64]$iface.BytesReceivedPersec
    $tx1=[int64]$iface.BytesSentPersec
    Start-Sleep -Milliseconds 500
    $iface2=Get-CimInstance Win32_PerfRawData_Tcpip_NetworkInterface | Where-Object { $_.Name -eq $net_iface } | Select-Object -First 1
    if ($iface2) {
      $net_rx_kbps=[int](($iface2.BytesReceivedPersec-$rx1)*8/500)
      $net_tx_kbps=[int](($iface2.BytesSentPersec-$tx1)*8/500)
      if ($net_rx_kbps -lt 0) { $net_rx_kbps=0 }
      if ($net_tx_kbps -lt 0) { $net_tx_kbps=0 }
    }
  }
} catch {}
Write-Output "os_family=$os_family"
Write-Output "cpu_used_pct=$cpu_used_pct"
Write-Output "mem_used_pct=$mem_used_pct"
Write-Output "mem_used_mb=$mem_used_mb"
Write-Output "mem_total_mb=$mem_total_mb"
Write-Output "disk_used_pct=$disk_used_pct"
Write-Output "disk_avail_gb=$disk_avail_gb"
Write-Output "net_iface=$net_iface"
Write-Output "net_rx_kbps=$net_rx_kbps"
Write-Output "net_tx_kbps=$net_tx_kbps"
Write-Output "uptime_sec=$uptime_sec"
"""


@dataclass
class MetricsSample:
    ok: bool
    metrics: dict[str, Any]
    detail: str
    sampled_at: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_metrics_kv(text: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or "=" not in line or line.startswith("#"):
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip()
        if not k:
            continue
        if k in {"os_family", "net_iface"}:
            out[k] = v
            continue
        if k == "load1":
            try:
                out[k] = float(v)
            except ValueError:
                out[k] = v
            continue
        if k in {"cpu_used_pct", "net_rx_kbps", "net_tx_kbps"}:
            try:
                out[k] = int(float(v))
            except ValueError:
                out[k] = v
            continue
        if re.fullmatch(r"-?\d+", v):
            out[k] = int(v)
        else:
            try:
                out[k] = float(v)
            except ValueError:
                out[k] = v
    return out


def format_uptime(sec: int | float | None) -> str:
    if sec is None:
        return "—"
    try:
        s = int(sec)
    except (TypeError, ValueError):
        return "—"
    if s < 0:
        s = 0
    days, rem = divmod(s, 86400)
    hours, rem = divmod(rem, 3600)
    mins, _ = divmod(rem, 60)
    if days:
        return f"{days}天 {hours}时"
    if hours:
        return f"{hours}时 {mins}分"
    return f"{mins}分"


def metrics_brief(m: dict[str, Any] | None) -> str:
    if not m:
        return ""
    parts: list[str] = []
    if m.get("cpu_used_pct") is not None:
        parts.append(f"CPU {m['cpu_used_pct']}%")
    elif m.get("load1") is not None:
        parts.append(f"负载 {m['load1']}")
    if m.get("mem_used_pct") is not None:
        parts.append(f"内存 {m['mem_used_pct']}%")
    if m.get("disk_used_pct") is not None:
        parts.append(f"磁盘 {m['disk_used_pct']}%")
    if m.get("net_rx_kbps") is not None or m.get("net_tx_kbps") is not None:
        parts.append(f"网络 ↓{m.get('net_rx_kbps', 0)} ↑{m.get('net_tx_kbps', 0)} kbps")
    if m.get("uptime_sec") is not None:
        parts.append(f"已运行 {format_uptime(m['uptime_sec'])}")
    return " · ".join(parts)


def _is_windows_os(os_name: str | None) -> bool:
    return (os_name or "").lower() in {"windows", "win", "win32"}


def _remote_metrics_command(*, windows: bool = False) -> str:
    if windows:
        # run_ssh wraps Windows resources with powershell -Command.
        return METRICS_PS1.strip()
    return "sh <<'GRIP_METRICS_EOF'\n" + METRICS_SH.strip() + "\nGRIP_METRICS_EOF"


def _ensure_os_for_metrics(
    resource: Resource,
    vault: Vault,
    *,
    timeout_sec: int,
    persist_meta: Any | None,
) -> None:
    """Detect remote OS when meta.os is missing/ambiguous; optionally persist.

    Mutates resource.meta["os"] in-place when detection succeeds so the rest of
    this sample uses the correct script. Leaves meta unchanged on failure.
    persist_meta should be a ResourceStore (or any object with patch_meta).
    """
    current = resource.meta.get("os")
    if not needs_os_probe(current if isinstance(current, str) else None):
        return
    detected = detect_remote_os(
        resource, vault, timeout_sec=min(12, max(5, timeout_sec // 2))
    )
    if not detected:
        return
    resource.meta["os"] = detected
    if persist_meta is not None and getattr(persist_meta, "patch_meta", None):
        try:
            persist_meta.patch_meta(resource.name, {"os": detected})
        except Exception:  # noqa: BLE001
            # In-memory update still applies for this sample.
            pass


def sample_ssh_metrics(
    resource: Resource,
    vault: Vault,
    *,
    timeout_sec: int = 45,
    connect_timeout_sec: int = 10,
    tcp_timeout_sec: float = 2.0,
    persist_meta: Any | None = None,
) -> MetricsSample:
    """Sample host metrics over SSH.

    Cheap TCP preflight first: a dead host fails in ~tcp_timeout_sec with
    METRICS_HOST_UNREACHABLE instead of hanging on paramiko/OpenSSH connect.
    timeout_sec covers the full remote script (CPU+net share a ~0.5s sleep;
    keep headroom for SSH/auth under load). connect_timeout_sec caps the
    handshake after TCP is open.
    """
    sampled_at = _now()
    if resource.kind != "ssh":
        return MetricsSample(False, {}, "metrics require ssh resource", sampled_at)
    host = resource.meta.get("host")
    if not host:
        return MetricsSample(False, {}, "no host", sampled_at)
    port = int(resource.meta.get("port") or 22)
    if not tcp_open(str(host), port, timeout=float(tcp_timeout_sec)):
        return MetricsSample(False, {}, METRICS_HOST_UNREACHABLE, sampled_at)
    _ensure_os_for_metrics(
        resource, vault, timeout_sec=timeout_sec, persist_meta=persist_meta
    )
    os_name = (resource.meta.get("os") or "unix").lower()
    windows = _is_windows_os(os_name)
    try:
        result = run_ssh(
            resource,
            vault,
            _remote_metrics_command(windows=windows),
            timeout_sec=timeout_sec,
            connect_timeout_sec=connect_timeout_sec,
        )
    except Exception as exc:  # noqa: BLE001
        return MetricsSample(False, {}, str(exc), sampled_at)

    parsed = parse_metrics_kv(result.stdout)
    if result.exit_code != 0 and not parsed:
        detail = (result.stderr or result.stdout or f"exit {result.exit_code}").strip()
        return MetricsSample(False, {}, detail[:300], sampled_at)
    if not parsed:
        return MetricsSample(
            False,
            {},
            (result.stderr or "empty metrics output")[:300],
            sampled_at,
        )
    metrics = {
        "os_family": parsed.get("os_family"),
        "cpu_used_pct": parsed.get("cpu_used_pct"),
        "load1": parsed.get("load1"),
        "mem_used_pct": parsed.get("mem_used_pct"),
        "mem_used_mb": parsed.get("mem_used_mb"),
        "mem_total_mb": parsed.get("mem_total_mb"),
        "disk_used_pct": parsed.get("disk_used_pct"),
        "disk_avail_gb": parsed.get("disk_avail_gb"),
        "net_iface": parsed.get("net_iface"),
        "net_rx_kbps": parsed.get("net_rx_kbps"),
        "net_tx_kbps": parsed.get("net_tx_kbps"),
        "uptime_sec": parsed.get("uptime_sec"),
    }
    return MetricsSample(True, metrics, "ok", sampled_at)
