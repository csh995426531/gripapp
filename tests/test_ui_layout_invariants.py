"""Guard recurring menubar UI regressions that keep getting rewritten away."""

from __future__ import annotations

from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "src" / "grip" / "web" / "app.py"


def _src() -> str:
    return APP.read_text(encoding="utf-8")


def test_list_spark_pills_stay_inline_fixed_width() -> None:
    src = _src()
    assert ".mrow:not(.mrow-detail) .mpill{" in src
    assert "width:36px;height:12px;flex:0 0 36px" in src
    assert "flex-wrap:nowrap" in src
    assert "text-overflow:ellipsis" in src
    assert "grid-template-columns:16px 8px minmax(0,1fr)" in src
    assert ".device > span:last-child{min-width:0;overflow:hidden}" in src
    # Must not reintroduce the stacked two-row pill grid that killed sparks.
    assert "grid-template-rows:auto auto" not in src


def test_list_disk_io_stays_compact_and_unlike_net() -> None:
    src = _src()
    # List: short R/W + spark first so the curve is not clipped.
    assert "`${fmtKbps(m.disk_read_kbps??0)}/${fmtKbps(m.disk_write_kbps??0)}`" in src
    assert "sparkDiskIoSVG(win, {large:false})" in src
    assert 'lab:"盘IO"' in src
    assert "credentials_ok" in src
    # List mval must be allowed to shrink or long 盘IO text hides the spark.
    assert "flex:1 1 0;min-width:0;max-width:100%;text-align:right" in src



def test_cloud_row_is_two_column_not_device_three_col() -> None:
    src = _src()
    assert "#cloudList .device.cloud-row{" in src
    assert "grid-template-columns:8px minmax(0,1fr)" in src
    assert "#cloudList .cloud-handle{display:none}" in src


def test_metrics_resource_prefers_ssh_then_vendor() -> None:
    src = _src()
    assert "function metricsResource(r){" in src
    assert "if(ssh && hasUsableMetrics(ssh)) return ssh;" in src
    assert "if(hasUsableMetrics(r)) return r;" in src


def test_detail_metrics_hint_distinguishes_ssh_vs_vendor() -> None:
    src = _src()
    assert "const fromSsh=!!(ssh && src===ssh);" in src
    assert "sshHandshakeBroken" in src
    assert 'lab:"盘IO"' in src
    assert "SSH连不上" in src


def test_ssh_handshake_broken_covers_banner_and_plain_ssh_rows() -> None:
    src = _src()
    # AutoDL gateways often die as "Error reading SSH protocol banner" while TCP still ok.
    assert "protocol banner" in src
    assert "Plain SSH rows" in src
    assert 'lab:"SSH连不上"' in src
    # Badge must be red (bad), not yellow warn — ops-wise it's down.
    assert 'cls:"bad", lab:"SSH连不上"' in src
    assert 'cls:"bad", lab:cloudLab+" · SSH连不上"' in src


def test_rendezvous_prompt_sits_on_device_list() -> None:
    src = _src()
    assert 'id="rvStrip"' in src
    assert "选择一台公网机器当汇合点" in src
    assert "出门也能连" in src
    assert "支持多通道" in src
    assert "支持中继" in src
    assert "换网络也能连" not in src
    assert "tun.relay_proven!==true" in src
    assert "已改走隧道" in src
    assert "/api/tunnel/setup" in src
    assert "plan.show_ports" in src
    assert "plan.port_hint" in src
    assert "tunnel_job" in src
    assert "面板没有等到接入结果" in src
