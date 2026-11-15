from __future__ import annotations

from grip.tunnel.wgembed import binary_name, bundled_binary_path, platform_tag
from grip.tunnel.wguapi import conf_to_uapi, parse_wg_quick_conf


def test_platform_tag_linux_amd64() -> None:
    assert platform_tag("Linux", "x86_64") == "linux-amd64"
    assert platform_tag("Darwin", "arm64") == "darwin-arm64"


def test_bundled_wireguard_go_present_for_common_targets() -> None:
    for tag in ("darwin-arm64", "darwin-amd64", "linux-amd64", "linux-arm64"):
        path = bundled_binary_path(tag)
        assert path is not None, tag
        assert path.is_file(), tag
        assert path.stat().st_size > 100_000, tag
        assert binary_name(tag) in path.name


def test_parse_conf_and_uapi_hex_keys() -> None:
    # 32 zero bytes → base64
    priv = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
    pub = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
    text = f"""
[Interface]
PrivateKey = {priv}
Address = 10.77.0.1/32
ListenPort = 51820

[Peer]
PublicKey = {pub}
AllowedIPs = 10.77.0.9/32
Endpoint = 203.0.113.9:51820
PersistentKeepalive = 25
"""
    conf = parse_wg_quick_conf(text)
    assert conf.listen_port == 51820
    assert conf.address.startswith("10.77.0.1")
    assert len(conf.peers) == 1
    body = conf_to_uapi(conf)
    assert "private_key=" + ("00" * 32) in body
    assert "public_key=" + ("00" * 32) in body
    assert "endpoint=203.0.113.9:51820" in body
    assert "allowed_ip=10.77.0.9/32" in body
    assert "replace_peers=true" in body
