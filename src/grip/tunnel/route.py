"""How an SSH call is reaching the device, and how long an agent should wait."""

from __future__ import annotations

from typing import Any

from grip.i18n import route_text


def route_notice(path: str, *, when: str = "本次") -> dict[str, Any]:
    """Stable fields for MCP callers. `when` is 本次 for a command, 当前 for a device."""
    return route_text(path, when=when)
