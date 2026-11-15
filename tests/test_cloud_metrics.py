from __future__ import annotations

from grip.cloud_metrics import (
    CLOUD_METRICS_SUPPORT,
    parse_aliyun_monitor_point,
    parse_oracle_cpu_value,
    sample_cloud_metrics,
)
from grip.service import GripService
from grip.sync import upsert_instances


def test_parse_aliyun_monitor_maps_cpu_not_fake_mem() -> None:
    m = parse_aliyun_monitor_point(
        {
            "CPU": 23,
            "InternetRX": 100,
            "InternetTX": 50,
            "InternetBandwidth": 10,
            "TimeStamp": "2014-10-30T05:00:00Z",
        }
    )
    assert m["cpu_used_pct"] == 23
    assert m["source"] == "aliyun_ecs_monitor"
    assert "mem_used_pct" not in m
    assert m["aliyun_internet_rx_kbits"] == 100
    assert m["aliyun_internet_bandwidth_kbps"] == 10


def test_parse_oracle_cpu_as_percent_no_extra_scale() -> None:
    # Idle fraction-looking values must stay near 0 (not ×100 → 35%).
    assert parse_oracle_cpu_value(0.354)["cpu_used_pct"] == 0
    assert parse_oracle_cpu_value(0.0)["cpu_used_pct"] == 0
    assert parse_oracle_cpu_value(1.0)["cpu_used_pct"] == 1
    assert parse_oracle_cpu_value(42)["cpu_used_pct"] == 42
    assert parse_oracle_cpu_value(35.4)["cpu_used_pct"] == 35


def test_parse_oracle_mem_and_net_helpers() -> None:
    from grip.cloud_metrics import (
        parse_oracle_bytes_rate_to_kbps,
        parse_oracle_mem_value,
    )

    assert parse_oracle_mem_value(5.516) == 6
    assert parse_oracle_mem_value(0) == 0
    # 500 bytes/sec → 4 kbps
    assert parse_oracle_bytes_rate_to_kbps(500) == 4


def test_aliyun_support_matrix() -> None:
    assert CLOUD_METRICS_SUPPORT["aliyun"]["status"] == "supported"
    assert CLOUD_METRICS_SUPPORT["oracle"]["status"] == "supported"
    assert CLOUD_METRICS_SUPPORT["tencent"]["status"] == "supported"
    assert CLOUD_METRICS_SUPPORT["gcp"]["status"] == "supported"


def test_parse_aliyun_maps_net_kbps() -> None:
    m = parse_aliyun_monitor_point(
        {"CPU": 10, "InternetRX": 600, "InternetTX": 1200, "TimeStamp": "t"},
        period_sec=60,
    )
    assert m["cpu_used_pct"] == 10
    assert m["net_rx_kbps"] == 10
    assert m["net_tx_kbps"] == 20


def test_sample_cloud_metrics_aliyun_with_mock(service: GripService) -> None:
    service.accounts.upsert(
        cloud="aliyun",
        label="main",
        meta={"regions": ["cn-hangzhou"]},
        secret_payload={
            "access_key_id": "AKTEST",
            "access_key_secret": "SECRET",
        },
    )
    upsert_instances(
        service.resources,
        "aliyun",
        [
            {
                "cloud": "aliyun",
                "name": "ali:main:ecs1",
                "id": "i-1",
                "status": "running",
                "region": "cn-hangzhou",
                "account": "main",
                "cpu_cores": 2,
                "mem_mb": 4096,
            }
        ],
    )

    def fake_client(**kwargs):
        assert kwargs["instance_id"] == "i-1"
        assert kwargs["region"] == "cn-hangzhou"
        return {
            "MonitorData": {
                "InstanceMonitorData": [
                    {"CPU": 5, "TimeStamp": "2020-01-01T00:00:00Z"},
                    {
                        "CPU": 41,
                        "InternetRX": 12,
                        "InternetTX": 8,
                        "TimeStamp": "2020-01-01T00:14:00Z",
                    },
                ]
            }
        }

    out = sample_cloud_metrics(
        service.resources,
        service.accounts,
        cloud="aliyun",
        client_factory=fake_client,
    )
    assert out["status"] == "success"
    assert out["ok"] == 1
    assert out["results"][0]["metrics"]["cpu_used_pct"] == 41

    r = service.resources.get("ali:main:ecs1")
    assert r is not None
    assert r.meta["metrics"]["cpu_used_pct"] == 41
    assert r.meta["cpu_cores"] == 2
    assert r.meta["mem_mb"] == 4096
    assert "mem_used_pct" not in (r.meta.get("metrics") or {})
    assert r.meta.get("metrics_at")


def test_sample_cloud_metrics_failure_keeps_metrics_at(service: GripService) -> None:
    service.accounts.upsert(
        cloud="aliyun",
        label="main",
        meta={"regions": ["cn-hangzhou"]},
        secret_payload={
            "access_key_id": "AKTEST",
            "access_key_secret": "SECRET",
        },
    )
    upsert_instances(
        service.resources,
        "aliyun",
        [
            {
                "cloud": "aliyun",
                "name": "ali:main:ecs-fail",
                "id": "i-fail",
                "status": "running",
                "region": "cn-hangzhou",
                "account": "main",
            }
        ],
    )
    from datetime import datetime, timezone

    last_ok = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    good = {"cpu_used_pct": 11, "source": "aliyun_ecs_monitor"}
    service.resources.apply_metrics("ali:main:ecs-fail", good, last_ok, detail="ok")

    def boom(**kwargs):
        raise RuntimeError("monitor unavailable")

    out = sample_cloud_metrics(
        service.resources,
        service.accounts,
        cloud="aliyun",
        name="ali:main:ecs-fail",
        client_factory=boom,
    )
    assert out["ok"] == 0
    r = service.resources.get("ali:main:ecs-fail")
    assert r is not None
    assert r.meta.get("metrics_at") == last_ok
    assert r.meta.get("metrics") == good
    assert r.meta.get("metrics_detail")
    assert r.meta.get("metrics_detail") != "ok"
    assert r.meta.get("metrics_attempt_at")


def test_sample_gcp_cpu_net_with_mock(service: GripService) -> None:
    service.accounts.upsert(
        cloud="gcp",
        label="main",
        meta={"project": "demo-proj"},
        secret_payload={
            "service_account": {
                "type": "service_account",
                "project_id": "demo-proj",
                "client_email": "sa@demo-proj.iam.gserviceaccount.com",
            }
        },
    )
    upsert_instances(
        service.resources,
        "gcp",
        [
            {
                "cloud": "gcp",
                "name": "gcp:demo-proj:vm1",
                "id": "1234567890123456789",
                "status": "RUNNING",
                "zone": "us-central1-a",
                "account": "main",
                "project": "demo-proj",
            }
        ],
    )

    def fake_client(**kwargs):
        assert kwargs["instance_id"] == "1234567890123456789"
        assert kwargs["project"] == "demo-proj"
        return {
            "cpu_used_pct": 28,
            "net_rx_kbps": 15,
            "net_tx_kbps": 7,
            "source": "gcp_monitoring",
        }

    out = sample_cloud_metrics(
        service.resources,
        service.accounts,
        cloud="gcp",
        client_factory=fake_client,
    )
    assert out["status"] == "success"
    assert out["ok"] == 1
    m = out["results"][0]["metrics"]
    assert m["cpu_used_pct"] == 28
    assert m["net_rx_kbps"] == 15
    assert m["source"] == "gcp_monitoring"
    r = service.resources.get("gcp:demo-proj:vm1")
    assert r is not None
    assert r.meta["metrics"]["cpu_used_pct"] == 28


def test_sample_tencent_cpu_with_mock(service: GripService) -> None:
    service.accounts.upsert(
        cloud="tencent",
        label="main",
        meta={"regions": ["ap-guangzhou"], "product": "lighthouse"},
        secret_payload={"secret_id": "SID", "secret_key": "SKEY"},
    )
    upsert_instances(
        service.resources,
        "tencent",
        [
            {
                "cloud": "tencent",
                "name": "tc:lh1",
                "id": "lhins-1",
                "status": "running",
                "region": "ap-guangzhou",
                "account": "main",
                "product": "lighthouse",
                "cpu_cores": 1,
                "mem_gb": 1,
            }
        ],
    )

    def fake_client(**kwargs):
        assert kwargs["instance_id"] == "lhins-1"
        return {"cpu_used_pct": 33, "source": "tencent_cms"}

    out = sample_cloud_metrics(
        service.resources,
        service.accounts,
        cloud="tencent",
        client_factory=fake_client,
    )
    assert out["status"] == "success"
    assert out["ok"] == 1
    assert out["results"][0]["metrics"]["cpu_used_pct"] == 33


def test_sample_oracle_cpu_with_mock(service: GripService) -> None:
    service.accounts.upsert(
        cloud="oracle",
        label="main",
        meta={"compartment_id": "ocid1.compartment.oc1..x", "regions": ["ap-tokyo-1"]},
        secret_payload={
            "user_ocid": "ocid1.user.oc1..u",
            "tenancy_ocid": "ocid1.tenancy.oc1..t",
            "fingerprint": "aa:bb",
            "private_key": "-----BEGIN PRIVATE KEY-----\nX\n-----END PRIVATE KEY-----\n",
            "compartment_id": "ocid1.compartment.oc1..x",
            "region": "ap-tokyo-1",
        },
    )
    upsert_instances(
        service.resources,
        "oracle",
        [
            {
                "cloud": "oracle",
                "name": "oci:main:box",
                "id": "ocid1.instance.oc1.ap-tokyo-1.xxx",
                "status": "running",
                "region": "ap-tokyo-1",
                "account": "main",
            }
        ],
    )

    def fake_client(**kwargs):
        assert "ocid1.instance" in kwargs["instance_id"]
        assert kwargs["compartment_id"].startswith("ocid1.compartment")
        return {
            "cpu_used_pct": 17,
            "mem_used_pct": 42,
            "load1": 0.35,
            "net_rx_kbps": 12,
            "net_tx_kbps": 8,
            "disk_iops_read": 3,
            "disk_iops_write": 9,
            "source": "oci_computeagent",
        }

    out = sample_cloud_metrics(
        service.resources,
        service.accounts,
        cloud="oracle",
        client_factory=fake_client,
    )
    assert out["ok"] == 1
    r = service.resources.get("oci:main:box")
    assert r is not None
    m = r.meta["metrics"]
    assert m["cpu_used_pct"] == 17
    assert m["mem_used_pct"] == 42
    assert m["load1"] == 0.35
    assert m["net_rx_kbps"] == 12
    assert m["disk_iops_write"] == 9


def test_sample_metrics_routes_oracle_away_from_ssh(service: GripService) -> None:
    """Panel「刷新」on unlinked cloud uses vendor path (not SSH)."""
    service.resources.upsert_cloud_instance(
        name="oci:main:box",
        kind="oracle_instance",
        status="running",
        meta={"cloud": "oracle", "region": "ap-tokyo-1", "account": "main"},
    )
    out = service.sample_metrics(name="oci:main:box")
    assert out.get("error") != "resource_not_found"
    results = out.get("results") or []
    assert results
    assert results[0].get("source") != "ssh"
    assert results[0]["name"] == "oci:main:box"
    # No oracle account in this fixture → vendor error detail, not SSH timeout.
    assert results[0].get("ok") is False
    detail = results[0].get("detail") or ""
    assert "SSH" not in detail
    assert "account" in detail.lower() or "compartment" in detail.lower()


def test_sample_cloud_metrics_skip_ssh_linked(service: GripService) -> None:
    service.accounts.upsert(
        cloud="aliyun",
        label="main",
        meta={"regions": ["cn-hangzhou"]},
        secret_payload={
            "access_key_id": "AKTEST",
            "access_key_secret": "SECRET",
        },
    )
    upsert_instances(
        service.resources,
        "aliyun",
        [
            {
                "cloud": "aliyun",
                "name": "ali:main:linked",
                "id": "i-linked",
                "status": "running",
                "region": "cn-hangzhou",
                "account": "main",
            },
            {
                "cloud": "aliyun",
                "name": "ali:main:bare",
                "id": "i-bare",
                "status": "running",
                "region": "cn-hangzhou",
                "account": "main",
            },
        ],
    )
    service.resources.patch_meta("ali:main:linked", {"ssh_name": "ssh:linked"})

    seen: list[str] = []

    def fake_client(**kwargs):
        seen.append(kwargs["instance_id"])
        return {
            "MonitorData": {
                "InstanceMonitorData": [
                    {"CPU": 9, "TimeStamp": "2020-01-01T00:00:00Z"},
                ]
            }
        }

    out = sample_cloud_metrics(
        service.resources,
        service.accounts,
        cloud="aliyun",
        skip_ssh_linked=True,
        client_factory=fake_client,
    )
    assert out["ok"] == 1
    assert seen == ["i-bare"]
    assert service.resources.get("ali:main:bare").meta["metrics"]["cpu_used_pct"] == 9  # type: ignore[union-attr]
    assert "metrics" not in (service.resources.get("ali:main:linked").meta or {})


def test_sample_metrics_linked_cloud_uses_ssh_device(service: GripService) -> None:
    """Refreshing a linked cloud row samples the SSH device, not vendor API."""
    service.resources.upsert_cloud_instance(
        name="oci:main:box",
        kind="oracle_instance",
        status="running",
        meta={
            "cloud": "oracle",
            "region": "ap-tokyo-1",
            "account": "main",
            "ssh_name": "ssh:oci-box",
        },
    )
    service.resources.upsert_ssh_with_key(
        name="ssh:oci-box",
        host="127.0.0.1",
        user="opc",
        port=22,
        key_material="-----BEGIN RSA PRIVATE KEY-----\nMIIE\n-----END RSA PRIVATE KEY-----\n",
        extra_meta={"cloud_ref": "oci:main:box"},
    )
    out = service.sample_metrics(name="oci:main:box")
    results = out.get("results") or []
    assert results
    assert results[0]["name"] == "ssh:oci-box"
    assert results[0].get("source") == "ssh"


def test_service_sample_cloud_metrics_unknown_name(service: GripService) -> None:
    out = service.sample_cloud_metrics(name="no-such")
    assert out["error"] == "resource_not_found"


def test_tencent_capacity_on_sync_not_percent(service: GripService) -> None:
    upsert_instances(
        service.resources,
        "tencent",
        [
            {
                "cloud": "tencent",
                "name": "tc:cap",
                "id": "lhins-2",
                "status": "running",
                "region": "ap-guangzhou",
                "cpu_cores": 2,
                "mem_gb": 4,
            }
        ],
    )
    labels = service.resources.get("tc:cap").summary()["labels"]  # type: ignore[union-attr]
    assert labels["cpu_cores"] == 2
    assert labels["mem_gb"] == 4
    assert "cpu_used_pct" not in labels
