"""Host adapter registry."""

from __future__ import annotations

from pathlib import Path

from .base import HostAdapter
from .claude import ClaudeCodeAdapter
from .codex import CodexAdapter

_ADAPTERS: dict[str, type[HostAdapter]] = {
    ClaudeCodeAdapter.host_id: ClaudeCodeAdapter,
    CodexAdapter.host_id: CodexAdapter,
}


def supported_hosts() -> tuple[str, ...]:
    return tuple(_ADAPTERS)


def get_adapter(host_id: str, home: Path | None = None) -> HostAdapter:
    try:
        adapter_cls = _ADAPTERS[host_id]
    except KeyError as exc:
        raise ValueError(
            f"unknown host {host_id!r}; supported hosts: {', '.join(supported_hosts())}"
        ) from exc
    return adapter_cls(home=home)
