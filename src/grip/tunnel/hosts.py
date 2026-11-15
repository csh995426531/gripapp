from __future__ import annotations

import ipaddress


def is_lan_host(host: str) -> bool:
    """True for private, loopback, and link-local addresses.

    Hostnames are treated as public: a name like an AutoDL gateway is not a
    LAN device, even before DNS.
    """
    raw = (host or "").strip().strip("[]")
    if not raw:
        return False
    if raw.lower() == "localhost":
        return True
    # host:port (not IPv6)
    if raw.count(":") == 1 and "." in raw:
        raw = raw.split(":", 1)[0]
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError:
        return False
    return bool(ip.is_private or ip.is_loopback or ip.is_link_local)
