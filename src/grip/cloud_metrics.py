"""Best-effort cloud vendor metrics (no host agent).

Used for cloud inventory rows that are **not** linked to a Grip SSH device yet.
Once SSH is linked, the periodic tick prefers unified SSH host metrics.

Supported now (map onto SSH-like board keys when the vendor exposes them):
  * aliyun — ECS ``DescribeInstanceMonitorData`` → CPU + net
  * oracle — OCI ``oci_computeagent`` → CPU / mem / load / net / disk iops
  * tencent — CMS ``GetMonitorData`` → CPU / mem / net
  * gcp — Monitoring API → CPU + net (mem needs ops agent)
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from grip.accounts import AccountStore
    from grip.resources import Resource, ResourceStore

KIND_TO_CLOUD = {
    "oracle_instance": "oracle",
    "gcp_instance": "gcp",
    "aliyun_instance": "aliyun",
    "tencent_instance": "tencent",
}

# Honest capability matrix for CLI/MCP callers.
CLOUD_METRICS_SUPPORT: dict[str, dict[str, str]] = {
    "aliyun": {
        "status": "supported",
        "detail": "ECS DescribeInstanceMonitorData → CPU + net (no memory in this API)",
    },
    "oracle": {
        "status": "supported",
        "detail": "OCI Monitoring oci_computeagent: CPU/mem/load/net (+disk iops)",
    },
    "gcp": {
        "status": "supported",
        "detail": "Cloud Monitoring: CPU + net (memory needs Ops Agent)",
    },
    "tencent": {
        "status": "supported",
        "detail": "CMS GetMonitorData: CPU / mem / net (CVM / Lighthouse)",
    },
}


@dataclass
class CloudMetricsSample:
    ok: bool
    metrics: dict[str, Any]
    detail: str
    sampled_at: str
    cloud: str
    name: str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_aliyun_monitor_point(
    point: dict[str, Any], *, period_sec: int = 60
) -> dict[str, Any]:
    """Map one Aliyun InstanceMonitorData row → board-friendly metrics keys.

    CPU% → ``cpu_used_pct``. Internet/Intranet RX/TX are kbits over ``period_sec``
    → average ``net_*_kbps``. No memory in this ECS API.
    """
    out: dict[str, Any] = {
        "source": "aliyun_ecs_monitor",
        "vendor_timestamp": point.get("TimeStamp"),
    }
    cpu = point.get("CPU")
    if cpu is not None:
        try:
            pct = int(round(float(cpu)))
            out["cpu_used_pct"] = max(0, min(100, pct))
        except (TypeError, ValueError):
            pass
    period = max(int(period_sec or 60), 1)
    for src, dst in (
        ("InternetRX", "aliyun_internet_rx_kbits"),
        ("InternetTX", "aliyun_internet_tx_kbits"),
        ("IntranetRX", "aliyun_intranet_rx_kbits"),
        ("IntranetTX", "aliyun_intranet_tx_kbits"),
        ("InternetBandwidth", "aliyun_internet_bandwidth_kbps"),
        ("IntranetBandwidth", "aliyun_intranet_bandwidth_kbps"),
    ):
        if point.get(src) is not None:
            out[dst] = point[src]
    rx = point.get("InternetRX")
    if rx is None:
        rx = point.get("IntranetRX")
    tx = point.get("InternetTX")
    if tx is None:
        tx = point.get("IntranetTX")
    try:
        if rx is not None:
            out["net_rx_kbps"] = max(0, int(round(float(rx) / period)))
        if tx is not None:
            out["net_tx_kbps"] = max(0, int(round(float(tx) / period)))
    except (TypeError, ValueError):
        pass
    return out


def fetch_aliyun_instance_monitor(
    *,
    access_key_id: str,
    access_key_secret: str,
    region: str,
    instance_id: str,
    lookback_minutes: int = 15,
    period: int = 60,
    client_factory: Any | None = None,
) -> dict[str, Any] | None:
    """Call ECS DescribeInstanceMonitorData; return latest point or None."""
    end = _utc_now()
    start = end - timedelta(minutes=lookback_minutes)

    if client_factory is not None:
        raw = client_factory(
            access_key_id=access_key_id,
            access_key_secret=access_key_secret,
            region=region,
            instance_id=instance_id,
            start_time=_iso(start),
            end_time=_iso(end),
            period=period,
        )
    else:
        from grip.cloud_api import aliyun as ali_api

        raw = ali_api.describe_instance_monitor_data(
            access_key_id=access_key_id,
            access_key_secret=access_key_secret,
            region=region,
            instance_id=instance_id,
            start_time=_iso(start),
            end_time=_iso(end),
            period=period,
        )

    points = (raw.get("MonitorData") or {}).get("InstanceMonitorData") or []
    if not points:
        return None
    # Prefer last point that actually has CPU
    for point in reversed(points):
        if isinstance(point, dict) and point.get("CPU") is not None:
            return point
    last = points[-1]
    return last if isinstance(last, dict) else None


def sample_aliyun_resource(
    resource: "Resource",
    accounts: "AccountStore",
    *,
    client_factory: Any | None = None,
) -> CloudMetricsSample:
    sampled_at = _iso(_utc_now())
    meta = resource.meta or {}
    instance_id = meta.get("id")
    region = meta.get("region")
    account_label = meta.get("account")
    if not instance_id or not region or not account_label:
        return CloudMetricsSample(
            False,
            {},
            "missing id/region/account on aliyun resource meta",
            sampled_at,
            "aliyun",
            resource.name,
        )
    if str(resource.status or "").lower() in {"stopped", "stopping"}:
        return CloudMetricsSample(
            False,
            {},
            f"skip stopped instance ({resource.status})",
            sampled_at,
            "aliyun",
            resource.name,
        )

    acc = accounts.get(f"aliyun:{account_label}")
    if not acc:
        return CloudMetricsSample(
            False,
            {},
            f"no account aliyun:{account_label}",
            sampled_at,
            "aliyun",
            resource.name,
        )
    secrets = accounts.secret_dict(acc)
    ak = secrets.get("access_key_id")
    sk = secrets.get("access_key_secret")
    if not ak or not sk:
        return CloudMetricsSample(
            False,
            {},
            "missing access_key_id/access_key_secret",
            sampled_at,
            "aliyun",
            resource.name,
        )

    try:
        point = fetch_aliyun_instance_monitor(
            access_key_id=ak,
            access_key_secret=sk,
            region=str(region),
            instance_id=str(instance_id),
            client_factory=client_factory,
        )
    except Exception as exc:  # noqa: BLE001
        return CloudMetricsSample(
            False,
            {},
            f"aliyun monitor error: {exc}"[:300],
            sampled_at,
            "aliyun",
            resource.name,
        )

    if not point:
        return CloudMetricsSample(
            False,
            {},
            "no monitor data (empty or instance not reporting)",
            sampled_at,
            "aliyun",
            resource.name,
        )

    metrics = parse_aliyun_monitor_point(point, period_sec=60)
    if not any(k in metrics for k in ("cpu_used_pct", "net_rx_kbps", "net_tx_kbps")):
        return CloudMetricsSample(
            False,
            metrics,
            "monitor point missing CPU/net",
            sampled_at,
            "aliyun",
            resource.name,
        )
    return CloudMetricsSample(
        True, metrics, "ok", sampled_at, "aliyun", resource.name
    )


def _oracle_ts(vendor_timestamp: Any) -> str | None:
    if vendor_timestamp is None:
        return None
    if hasattr(vendor_timestamp, "isoformat"):
        return vendor_timestamp.isoformat()
    return str(vendor_timestamp)


def parse_oracle_cpu_value(value: Any, *, vendor_timestamp: Any = None) -> dict[str, Any]:
    """Map OCI CpuUtilization datapoint → board metrics.

    Treat as percent 0–100 (same as MemoryUtilization). Do **not** ×100 when
    the value is ≤1 — that inflated idle hosts (e.g. 0.35 → 35%) vs SSH ~0%.
    """
    out: dict[str, Any] = {"source": "oci_computeagent"}
    ts = _oracle_ts(vendor_timestamp)
    if ts:
        out["vendor_timestamp"] = ts
    if value is None:
        return out
    try:
        raw = float(value)
    except (TypeError, ValueError):
        return out
    out["cpu_used_pct"] = max(0, min(100, int(round(raw))))
    return out


def parse_oracle_mem_value(value: Any) -> int | None:
    """MemoryUtilization is percent 0–100 (not a 0–1 fraction)."""
    if value is None:
        return None
    try:
        raw = float(value)
    except (TypeError, ValueError):
        return None
    return max(0, min(100, int(round(raw))))


def parse_oracle_bytes_rate_to_kbps(value: Any) -> int | None:
    """NetworksBytes* ``rate()`` is bytes/sec → integer kbps."""
    if value is None:
        return None
    try:
        bps = float(value)
    except (TypeError, ValueError):
        return None
    return max(0, int(round(bps * 8.0 / 1000.0)))


def _oracle_last_point(points: list[Any]) -> tuple[Any, Any]:
    if not points:
        return None, None
    last = points[-1]
    if isinstance(last, dict):
        return last.get("value"), last.get("timestamp")
    return getattr(last, "value", None), getattr(last, "timestamp", None)


def fetch_oracle_agent_metrics(
    *,
    secrets: dict[str, Any],
    region: str,
    compartment_id: str,
    instance_id: str,
    lookback_minutes: int = 30,
    client_factory: Any | None = None,
) -> dict[str, Any] | None:
    """Pull oci_computeagent series and map onto SSH-like board keys.

    CPU / mem / load / net (+ disk IOPS as extras). No disk_used_pct from agent.
    """
    if client_factory is not None:
        raw = client_factory(
            secrets=secrets,
            region=region,
            compartment_id=compartment_id,
            instance_id=instance_id,
        )
        if raw is None:
            return None
        if isinstance(raw, dict):
            out = dict(raw)
            out.setdefault("source", "oci_computeagent")
            return out
        if isinstance(raw, (int, float)):
            return parse_oracle_cpu_value(raw)
        return None

    from grip.cloud_api import oracle as oci_api

    end = _utc_now()
    start = end - timedelta(minutes=lookback_minutes)
    rid = str(instance_id)

    # mean for gauges; rate() for byte counters → bytes/sec
    queries: dict[str, str] = {
        "cpu": f'CpuUtilization[1m]{{resourceId = "{rid}"}}.mean()',
        "mem": f'MemoryUtilization[1m]{{resourceId = "{rid}"}}.mean()',
        "load": f'LoadAverage[1m]{{resourceId = "{rid}"}}.mean()',
        "net_in": f'NetworksBytesIn[1m]{{resourceId = "{rid}"}}.rate()',
        "net_out": f'NetworksBytesOut[1m]{{resourceId = "{rid}"}}.rate()',
        # IOPS/byte counters are cumulative — use rate() for instantaneous values.
        "disk_r_iops": f'DiskIopsRead[1m]{{resourceId = "{rid}"}}.rate()',
        "disk_w_iops": f'DiskIopsWritten[1m]{{resourceId = "{rid}"}}.rate()',
        "disk_r_bps": f'DiskBytesRead[1m]{{resourceId = "{rid}"}}.rate()',
        "disk_w_bps": f'DiskBytesWritten[1m]{{resourceId = "{rid}"}}.rate()',
    }

    def _run(key: str, query: str) -> tuple[str, Any, Any]:
        series = oci_api.summarize_metrics_data(
            secrets=secrets,
            region=region,
            compartment_id=compartment_id,
            namespace="oci_computeagent",
            query=query,
            start_time=start,
            end_time=end,
        )
        if not series:
            return key, None, None
        first = series[0]
        points = (
            first.get("aggregatedDatapoints")
            if isinstance(first, dict)
            else getattr(first, "aggregated_datapoints", None)
        ) or []
        val, ts = _oracle_last_point(points)
        return key, val, ts

    values: dict[str, Any] = {}
    stamps: dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = [pool.submit(_run, k, q) for k, q in queries.items()]
        for fut in as_completed(futs):
            key, val, ts = fut.result()
            values[key] = val
            if ts is not None:
                stamps[key] = ts

    out: dict[str, Any] = {"source": "oci_computeagent"}
    ts = stamps.get("cpu") or stamps.get("mem") or next(iter(stamps.values()), None)
    ts_s = _oracle_ts(ts)
    if ts_s:
        out["vendor_timestamp"] = ts_s

    if values.get("cpu") is not None:
        out.update(parse_oracle_cpu_value(values["cpu"], vendor_timestamp=ts))
    mem_pct = parse_oracle_mem_value(values.get("mem"))
    if mem_pct is not None:
        out["mem_used_pct"] = mem_pct
    if values.get("load") is not None:
        try:
            out["load1"] = round(float(values["load"]), 2)
        except (TypeError, ValueError):
            pass
    rx = parse_oracle_bytes_rate_to_kbps(values.get("net_in"))
    tx = parse_oracle_bytes_rate_to_kbps(values.get("net_out"))
    if rx is not None:
        out["net_rx_kbps"] = rx
    if tx is not None:
        out["net_tx_kbps"] = tx
    for src, dst in (
        ("disk_r_iops", "disk_iops_read"),
        ("disk_w_iops", "disk_iops_write"),
    ):
        if values.get(src) is not None:
            try:
                out[dst] = int(round(float(values[src])))
            except (TypeError, ValueError):
                pass
    for src, dst in (
        ("disk_r_bps", "disk_read_kbps"),
        ("disk_w_bps", "disk_write_kbps"),
    ):
        kbps = parse_oracle_bytes_rate_to_kbps(values.get(src))
        if kbps is not None:
            out[dst] = kbps

    # Need at least one primary host-like signal
    if not any(k in out for k in ("cpu_used_pct", "mem_used_pct", "load1")):
        return None
    return out


# Back-compat alias used by older tests / callers
fetch_oracle_cpu_utilization = fetch_oracle_agent_metrics


def sample_oracle_resource(
    resource: "Resource",
    accounts: "AccountStore",
    *,
    client_factory: Any | None = None,
) -> CloudMetricsSample:
    sampled_at = _iso(_utc_now())
    meta = resource.meta or {}
    instance_id = meta.get("id")
    region = meta.get("region")
    account_label = meta.get("account")
    if not instance_id or not region or not account_label:
        return CloudMetricsSample(
            False,
            {},
            "missing id/region/account on oracle resource meta",
            sampled_at,
            "oracle",
            resource.name,
        )
    if str(resource.status or "").lower() in {
        "stopped",
        "stopping",
        "terminated",
        "terminating",
    }:
        return CloudMetricsSample(
            False,
            {},
            f"skip stopped instance ({resource.status})",
            sampled_at,
            "oracle",
            resource.name,
        )

    acc = accounts.get(f"oracle:{account_label}")
    if not acc:
        return CloudMetricsSample(
            False,
            {},
            f"no account oracle:{account_label}",
            sampled_at,
            "oracle",
            resource.name,
        )
    secrets = accounts.secret_dict(acc)
    compartment = (
        meta.get("compartment_id")
        or acc.meta.get("compartment_id")
        or secrets.get("compartment_id")
    )
    if not compartment:
        return CloudMetricsSample(
            False,
            {},
            "missing compartment_id on oracle account",
            sampled_at,
            "oracle",
            resource.name,
        )

    try:
        metrics = fetch_oracle_agent_metrics(
            secrets=secrets,
            region=str(region),
            compartment_id=str(compartment),
            instance_id=str(instance_id),
            client_factory=client_factory,
        )
    except Exception as exc:  # noqa: BLE001
        return CloudMetricsSample(
            False,
            {},
            f"oracle monitor error: {exc}"[:300],
            sampled_at,
            "oracle",
            resource.name,
        )

    if not metrics:
        return CloudMetricsSample(
            False,
            {},
            "no oci_computeagent data (enable Oracle Cloud Agent monitoring?)",
            sampled_at,
            "oracle",
            resource.name,
        )
    return CloudMetricsSample(
        True, metrics, "ok", sampled_at, "oracle", resource.name
    )


def parse_tencent_cpu_value(value: Any, *, vendor_timestamp: Any = None) -> dict[str, Any]:
    out: dict[str, Any] = {"source": "tencent_cms"}
    if vendor_timestamp is not None:
        out["vendor_timestamp"] = str(vendor_timestamp)
    if value is None:
        return out
    try:
        pct = int(round(float(value)))
        out["cpu_used_pct"] = max(0, min(100, pct))
    except (TypeError, ValueError):
        pass
    return out


def _tencent_metric_specs(product: str) -> tuple[str, list[tuple[str, str]]]:
    """Return (namespace, [(MetricName, board_key), ...])."""
    prod = (product or "lighthouse").lower()
    if prod == "cvm":
        return "QCE/CVM", [
            ("CPUUsage", "cpu_used_pct"),
            ("MemUsage", "mem_used_pct"),
            ("LanIntraffic", "net_rx_kbps"),
            ("LanOuttraffic", "net_tx_kbps"),
        ]
    return "QCE/LIGHTHOUSE", [
        ("CPUUsage", "cpu_used_pct"),
        ("MemUsage", "mem_used_pct"),
        ("Intraffic", "net_rx_kbps"),
        ("Outtraffic", "net_tx_kbps"),
    ]


def fetch_tencent_monitor_bundle(
    *,
    secret_id: str,
    secret_key: str,
    region: str,
    instance_id: str,
    product: str = "lighthouse",
    lookback_minutes: int = 15,
    client_factory: Any | None = None,
) -> dict[str, Any] | None:
    """CMS GetMonitorData for CPU / mem / net → SSH-like board keys."""
    if client_factory is not None:
        raw = client_factory(
            secret_id=secret_id,
            secret_key=secret_key,
            region=region,
            instance_id=instance_id,
            product=product,
        )
        if raw is None:
            return None
        if isinstance(raw, dict):
            out = dict(raw)
            out.setdefault("source", "tencent_cms")
            return out
        if isinstance(raw, (int, float)):
            return parse_tencent_cpu_value(raw)
        return None

    from grip.cloud_api import tencent as tc_api

    end = _utc_now()
    start = end - timedelta(minutes=lookback_minutes)
    namespace, specs = _tencent_metric_specs(product)
    start_s = start.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_s = end.strftime("%Y-%m-%dT%H:%M:%SZ")

    def _one(metric_name: str) -> tuple[str, Any, Any]:
        try:
            resp = tc_api.get_monitor_data(
                secret_id=secret_id,
                secret_key=secret_key,
                region=region,
                namespace=namespace,
                metric_name=metric_name,
                instance_id=instance_id,
                start_time=start_s,
                end_time=end_s,
                period=60,
            )
        except Exception:  # noqa: BLE001
            return metric_name, None, None
        data = resp.get("DataPoints") or []
        if not data:
            return metric_name, None, None
        values = data[0].get("Values") or []
        times = data[0].get("Timestamps") or []
        if not values:
            return metric_name, None, None
        return metric_name, values[-1], (times[-1] if times else None)

    by_name: dict[str, tuple[Any, Any]] = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = [pool.submit(_one, name) for name, _ in specs]
        for fut in as_completed(futs):
            metric_name, val, ts = fut.result()
            by_name[metric_name] = (val, ts)
    out: dict[str, Any] = {"source": "tencent_cms"}
    stamp = None
    for metric_name, board_key in specs:
        val, ts = by_name.get(metric_name, (None, None))
        if val is None:
            continue
        if ts is not None and stamp is None:
            stamp = ts
        try:
            num = float(val)
        except (TypeError, ValueError):
            continue
        if board_key in {"cpu_used_pct", "mem_used_pct"}:
            out[board_key] = max(0, min(100, int(round(num))))
        else:
            # Traffic metrics are typically Kbps already.
            out[board_key] = max(0, int(round(num)))
    if stamp is not None:
        out["vendor_timestamp"] = str(stamp)
    if not any(k in out for k in ("cpu_used_pct", "mem_used_pct", "net_rx_kbps", "net_tx_kbps")):
        return None
    return out


# Back-compat
fetch_tencent_cpu_usage = fetch_tencent_monitor_bundle


def sample_tencent_resource(
    resource: "Resource",
    accounts: "AccountStore",
    *,
    client_factory: Any | None = None,
) -> CloudMetricsSample:
    sampled_at = _iso(_utc_now())
    meta = resource.meta or {}
    instance_id = meta.get("id")
    region = meta.get("region")
    account_label = meta.get("account")
    if not instance_id or not region or not account_label:
        return CloudMetricsSample(
            False,
            {},
            "missing id/region/account on tencent resource meta",
            sampled_at,
            "tencent",
            resource.name,
        )
    if str(resource.status or "").lower() in {"stopped", "stopping", "shutdown"}:
        return CloudMetricsSample(
            False,
            {},
            f"skip stopped instance ({resource.status})",
            sampled_at,
            "tencent",
            resource.name,
        )

    acc = accounts.get(f"tencent:{account_label}")
    if not acc:
        return CloudMetricsSample(
            False,
            {},
            f"no account tencent:{account_label}",
            sampled_at,
            "tencent",
            resource.name,
        )
    secrets = accounts.secret_dict(acc)
    sid = secrets.get("secret_id")
    skey = secrets.get("secret_key")
    if not sid or not skey:
        return CloudMetricsSample(
            False,
            {},
            "missing secret_id/secret_key",
            sampled_at,
            "tencent",
            resource.name,
        )
    product = (
        meta.get("product")
        or acc.meta.get("product")
        or "lighthouse"
    )

    try:
        metrics = fetch_tencent_monitor_bundle(
            secret_id=str(sid),
            secret_key=str(skey),
            region=str(region),
            instance_id=str(instance_id),
            product=str(product),
            client_factory=client_factory,
        )
    except Exception as exc:  # noqa: BLE001
        return CloudMetricsSample(
            False,
            {},
            f"tencent monitor error: {exc}"[:300],
            sampled_at,
            "tencent",
            resource.name,
        )

    if not metrics:
        return CloudMetricsSample(
            False,
            {},
            "no CMS monitor data",
            sampled_at,
            "tencent",
            resource.name,
        )
    return CloudMetricsSample(
        True, metrics, "ok", sampled_at, "tencent", resource.name
    )


def fetch_gcp_monitor_bundle(
    *,
    service_account: dict[str, Any],
    project: str,
    instance_id: str,
    zone: str | None = None,
    lookback_minutes: int = 15,
    client_factory: Any | None = None,
) -> dict[str, Any] | None:
    """Cloud Monitoring timeSeries → CPU (+ net). Memory needs Ops Agent."""
    if client_factory is not None:
        raw = client_factory(
            service_account=service_account,
            project=project,
            instance_id=instance_id,
            zone=zone,
        )
        if raw is None:
            return None
        if isinstance(raw, dict):
            out = dict(raw)
            out.setdefault("source", "gcp_monitoring")
            return out
        return None

    from grip.cloud_api import gcp as gcp_api

    end = _utc_now()
    start = end - timedelta(minutes=lookback_minutes)
    start_s = start.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_s = end.strftime("%Y-%m-%dT%H:%M:%SZ")

    def _query(metric_type: str, *, aligner: str = "ALIGN_MEAN") -> Any:
        filt = (
            f'metric.type="{metric_type}" '
            f'AND resource.labels.instance_id="{instance_id}"'
        )
        if zone:
            filt += f' AND resource.labels.zone="{zone}"'
        try:
            series = gcp_api.query_timeseries(
                sa_info=service_account,
                project=project,
                filter_expr=filt,
                start_time=start_s,
                end_time=end_s,
                aligner=aligner,
            )
        except Exception:  # noqa: BLE001
            return None
        if not series:
            return None
        points = series[0].get("points") or []
        if not points:
            return None
        val = points[0].get("value") or {}
        if "doubleValue" in val:
            return float(val["doubleValue"])
        if "int64Value" in val:
            return float(val["int64Value"])
        return None

    out: dict[str, Any] = {"source": "gcp_monitoring"}
    cpu = _query("compute.googleapis.com/instance/cpu/utilization")
    if cpu is not None:
        # 0–1 fraction
        pct = cpu * 100.0 if 0.0 <= float(cpu) <= 1.0 else float(cpu)
        out["cpu_used_pct"] = max(0, min(100, int(round(pct))))
    # ALIGN_RATE → bytes/sec
    rx = _query(
        "compute.googleapis.com/instance/network/received_bytes_count",
        aligner="ALIGN_RATE",
    )
    tx = _query(
        "compute.googleapis.com/instance/network/sent_bytes_count",
        aligner="ALIGN_RATE",
    )
    if rx is not None:
        out["net_rx_kbps"] = max(0, int(round(float(rx) * 8.0 / 1000.0)))
    if tx is not None:
        out["net_tx_kbps"] = max(0, int(round(float(tx) * 8.0 / 1000.0)))
    # Ops Agent memory (best-effort)
    mem = _query("agent.googleapis.com/memory/percent_used")
    if mem is not None:
        out["mem_used_pct"] = max(0, min(100, int(round(float(mem)))))
    if not any(k in out for k in ("cpu_used_pct", "mem_used_pct", "net_rx_kbps")):
        return None
    return out


def sample_gcp_resource(
    resource: "Resource",
    accounts: "AccountStore",
    *,
    client_factory: Any | None = None,
) -> CloudMetricsSample:
    sampled_at = _iso(_utc_now())
    meta = resource.meta or {}
    instance_id = meta.get("id")
    account_label = meta.get("account")
    project = meta.get("project")
    zone = meta.get("zone")
    if not instance_id or not account_label:
        return CloudMetricsSample(
            False,
            {},
            "missing id/account on gcp resource meta",
            sampled_at,
            "gcp",
            resource.name,
        )
    if str(resource.status or "").lower() in {"stopped", "terminated", "stopping"}:
        return CloudMetricsSample(
            False,
            {},
            f"skip stopped instance ({resource.status})",
            sampled_at,
            "gcp",
            resource.name,
        )
    acc = accounts.get(f"gcp:{account_label}")
    if not acc:
        return CloudMetricsSample(
            False,
            {},
            f"no account gcp:{account_label}",
            sampled_at,
            "gcp",
            resource.name,
        )
    secrets = accounts.secret_dict(acc)
    sa = secrets.get("service_account")
    if isinstance(sa, str):
        try:
            sa = json.loads(sa)
        except json.JSONDecodeError:
            sa = None
    if not isinstance(sa, dict):
        return CloudMetricsSample(
            False,
            {},
            "missing service_account JSON",
            sampled_at,
            "gcp",
            resource.name,
        )
    project = project or acc.meta.get("project") or sa.get("project_id")
    if not project:
        return CloudMetricsSample(
            False,
            {},
            "missing project",
            sampled_at,
            "gcp",
            resource.name,
        )
    try:
        metrics = fetch_gcp_monitor_bundle(
            service_account=sa,
            project=str(project),
            instance_id=str(instance_id),
            zone=str(zone) if zone else None,
            client_factory=client_factory,
        )
    except Exception as exc:  # noqa: BLE001
        return CloudMetricsSample(
            False,
            {},
            f"gcp monitor error: {exc}"[:300],
            sampled_at,
            "gcp",
            resource.name,
        )
    if not metrics:
        return CloudMetricsSample(
            False,
            {},
            "no GCP Monitoring data",
            sampled_at,
            "gcp",
            resource.name,
        )
    return CloudMetricsSample(
        True, metrics, "ok", sampled_at, "gcp", resource.name
    )


def _is_ssh_linked(resource: "Resource") -> bool:
    """True when this cloud row points at a Grip SSH device name."""
    ssh_name = (resource.meta or {}).get("ssh_name")
    return isinstance(ssh_name, str) and bool(ssh_name.strip())


def _cloud_targets(
    store: "ResourceStore",
    *,
    cloud: str | None = None,
    name: str | None = None,
    skip_ssh_linked: bool = False,
) -> list["Resource"]:
    if name:
        r = store.get(name)
        if not r:
            return []
        if r.kind not in KIND_TO_CLOUD:
            return []
        if cloud and KIND_TO_CLOUD[r.kind] != cloud:
            return []
        if skip_ssh_linked and _is_ssh_linked(r):
            return []
        return [r]
    out: list[Resource] = []
    for r in store.list():
        c = KIND_TO_CLOUD.get(r.kind)
        if not c:
            continue
        if cloud and c != cloud:
            continue
        if skip_ssh_linked and _is_ssh_linked(r):
            continue
        out.append(r)
    return out


def sample_cloud_metrics(
    store: "ResourceStore",
    accounts: "AccountStore",
    *,
    cloud: str | None = None,
    name: str | None = None,
    client_factory: Any | None = None,
    skip_ssh_linked: bool = False,
) -> dict[str, Any]:
    """Sample vendor metrics for synced cloud instances (best-effort).

    Aliyun / Oracle / Tencent return CPU% when the vendor API has data. GCP is
    via Monitoring REST (CPU/net; mem needs Ops Agent).

    When ``skip_ssh_linked`` is True (periodic tick), skip cloud rows that already
    have a Grip SSH device — those use unified SSH host metrics instead.
    """
    if cloud and cloud not in CLOUD_METRICS_SUPPORT:
        return {"error": "unknown_cloud", "cloud": cloud}

    support_note: dict[str, Any] = {}
    clouds_to_note = [cloud] if cloud else list(CLOUD_METRICS_SUPPORT)
    for c in clouds_to_note:
        support_note[c] = CLOUD_METRICS_SUPPORT[c]

    if cloud and CLOUD_METRICS_SUPPORT[cloud]["status"] == "skipped":
        return {
            "status": "skipped",
            "cloud": cloud,
            "checked": 0,
            "ok": 0,
            "results": [],
            "support": support_note,
            "detail": CLOUD_METRICS_SUPPORT[cloud]["detail"],
        }

    targets = _cloud_targets(
        store, cloud=cloud, name=name, skip_ssh_linked=skip_ssh_linked
    )
    # When sampling all / by name, only attempt supported vendors
    results: list[dict[str, Any]] = []
    ok_n = 0
    for resource in targets:
        c = KIND_TO_CLOUD[resource.kind]
        info = CLOUD_METRICS_SUPPORT[c]
        if info["status"] == "skipped":
            sampled_at = _iso(_utc_now())
            store.patch_meta(
                resource.name,
                {
                    "metrics_attempt_at": sampled_at,
                    "metrics_detail": info["detail"],
                },
            )
            results.append(
                {
                    "name": resource.name,
                    "cloud": c,
                    "ok": False,
                    "metrics": None,
                    "detail": info["detail"],
                    "sampled_at": sampled_at,
                    "skipped": True,
                }
            )
            continue
        if c == "aliyun":
            sample = sample_aliyun_resource(
                resource, accounts, client_factory=client_factory
            )
        elif c == "oracle":
            sample = sample_oracle_resource(
                resource, accounts, client_factory=client_factory
            )
        elif c == "tencent":
            sample = sample_tencent_resource(
                resource, accounts, client_factory=client_factory
            )
        elif c == "gcp":
            sample = sample_gcp_resource(
                resource, accounts, client_factory=client_factory
            )
        else:
            sample = CloudMetricsSample(
                False,
                {},
                f"no sampler for {c}",
                _iso(_utc_now()),
                c,
                resource.name,
            )
        if sample.ok:
            ok_n += 1
            store.apply_metrics(
                resource.name,
                sample.metrics,
                sample.sampled_at,
                detail=sample.detail,
            )
        else:
            # Do not overwrite metrics_at — UI shows last successful sample only.
            store.patch_meta(
                resource.name,
                {
                    "metrics_attempt_at": sample.sampled_at,
                    "metrics_detail": sample.detail,
                },
            )
        results.append(
            {
                "name": sample.name,
                "cloud": sample.cloud,
                "ok": sample.ok,
                "metrics": sample.metrics if sample.ok else None,
                "detail": sample.detail,
                "sampled_at": sample.sampled_at,
            }
        )

    return {
        "status": "success",
        "checked": len(results),
        "ok": ok_n,
        "results": results,
        "support": support_note,
    }
