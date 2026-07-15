"""Focused component tests for MCP-distributed host pre-work adapters.

Exercises the real packaged installer/adapter code and the real Claude Code and
Codex configuration file formats in isolated temporary host homes. The daemon
is a test-only command runner double; the host hook mechanism (config-file
writing/removal) is the real product path under test. Evidence is host-specific:
each behavior is asserted for both hosts independently.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from preflight_adapters import get_adapter, supported_hosts
from preflight_adapters.base import MANAGED_COMMAND_TOKEN
from preflight_adapters.context import (
    FORBIDDEN_EVENT_FIELDS,
    PreworkContext,
    assert_no_forbidden_fields,
)
from preflight_adapters.runner import PreworkOutcome, run_prework
from preflight_adapters.state import AdapterState
from version import TOOLREQUEST_PROTOCOL_VERSION

HOSTS = list(supported_hosts())

HOST_CONFIG_FILE = {"claude": "settings.json", "codex": "hooks.json"}


class _FakeDaemon:
    """Test-only daemon command runner (never a fake production hook path)."""

    def __init__(
        self,
        *,
        protocol_version: str | None = TOOLREQUEST_PROTOCOL_VERSION,
        supported: list[str] | None = None,
        deferred: list[str] | None = None,
        raise_on_capabilities: Exception | None = None,
    ) -> None:
        self.protocol_version = protocol_version
        self.supported = supported if supported is not None else ["preflight.run"]
        self.deferred = deferred or []
        self.raise_on_capabilities = raise_on_capabilities
        self.requests: list[dict[str, Any]] = []

    async def capabilities(self) -> dict[str, Any]:
        if self.raise_on_capabilities is not None:
            raise self.raise_on_capabilities
        return {
            "toolrequest_protocol_version": self.protocol_version,
            "supported_commands": self.supported,
            "deferred_commands": self.deferred,
        }

    async def send_tool_request(self, tool_request: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(tool_request)
        return {"request_id": tool_request["request_id"], "status": "ok", "result": {}}


def _home_for(host: str, root: Path) -> Path:
    return root / f".{'claude' if host == 'claude' else 'codex'}"


def _install(host: str, root: Path):
    adapter = get_adapter(host, home=_home_for(host, root))
    result = adapter.install(consent=True)
    return adapter, result


def _startup_event(cwd: Path) -> dict[str, Any]:
    return {
        "session_id": "sess-1",
        "source": "startup",
        "cwd": str(cwd),
        "hook_event_name": "SessionStart",
        "transcript_path": "/should/not/be/read/transcript.jsonl",
    }


# --- lifecycle: install / status / update / disable / uninstall ---


@pytest.mark.parametrize("host", HOSTS)
def test_install_requires_explicit_consent(host: str, tmp_path: Path):
    adapter = get_adapter(host, home=_home_for(host, tmp_path))
    result = adapter.install(consent=False)
    assert result.ok is False
    assert result.state == AdapterState.NOT_INSTALLED
    assert "consent" in result.message.lower()
    assert not adapter.config_path().exists()


@pytest.mark.parametrize("host", HOSTS)
def test_install_writes_host_native_session_start_hook(host: str, tmp_path: Path):
    adapter, result = _install(host, tmp_path)
    assert result.ok is True
    assert result.state == AdapterState.ENABLED
    config_path = adapter.config_path()
    assert config_path.name == HOST_CONFIG_FILE[host]

    data = json.loads(config_path.read_text())
    handlers = data["hooks"]["SessionStart"][0]["hooks"]
    assert handlers[0]["type"] == "command"
    command = handlers[0]["command"]
    assert MANAGED_COMMAND_TOKEN in command
    assert f"--host {host}" in command

    status = adapter.status()
    assert status.state == AdapterState.ENABLED
    assert status.hook_present is True
    assert status.consent_granted is True


@pytest.mark.parametrize("host", HOSTS)
def test_install_preserves_existing_user_hooks(host: str, tmp_path: Path):
    home = _home_for(host, tmp_path)
    home.mkdir(parents=True)
    config_path = home / HOST_CONFIG_FILE[host]
    existing = {
        "hooks": {
            "SessionStart": [{"hooks": [{"type": "command", "command": "echo my-own-hook"}]}],
            "Stop": [{"hooks": [{"type": "command", "command": "echo stop"}]}],
        },
        "unrelated_setting": {"keep": True},
    }
    config_path.write_text(json.dumps(existing))

    adapter, result = _install(host, tmp_path)
    assert result.ok is True
    data = json.loads(config_path.read_text())
    commands = [h["command"] for group in data["hooks"]["SessionStart"] for h in group["hooks"]]
    assert "echo my-own-hook" in commands
    assert any(MANAGED_COMMAND_TOKEN in c for c in commands)
    assert data["hooks"]["Stop"][0]["hooks"][0]["command"] == "echo stop"
    assert data["unrelated_setting"] == {"keep": True}


@pytest.mark.parametrize("host", HOSTS)
def test_disable_removes_only_managed_hook_and_keeps_consent(host: str, tmp_path: Path):
    home = _home_for(host, tmp_path)
    home.mkdir(parents=True)
    config_path = home / HOST_CONFIG_FILE[host]
    config_path.write_text(
        json.dumps(
            {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "echo keep"}]}]}}
        )
    )
    adapter, _ = _install(host, tmp_path)

    result = adapter.disable()
    assert result.ok is True
    assert result.state == AdapterState.DISABLED
    data = json.loads(config_path.read_text())
    commands = [h["command"] for group in data["hooks"]["SessionStart"] for h in group["hooks"]]
    assert "echo keep" in commands
    assert not any(MANAGED_COMMAND_TOKEN in c for c in commands)

    status = adapter.status()
    assert status.state == AdapterState.DISABLED
    assert status.consent_granted is True


@pytest.mark.parametrize("host", HOSTS)
def test_update_rewrites_managed_hook_without_duplicates(host: str, tmp_path: Path):
    adapter, _ = _install(host, tmp_path)
    result = adapter.update()
    assert result.ok is True
    data = json.loads(adapter.config_path().read_text())
    managed = [
        h["command"]
        for group in data["hooks"]["SessionStart"]
        for h in group["hooks"]
        if MANAGED_COMMAND_TOKEN in h["command"]
    ]
    assert len(managed) == 1


@pytest.mark.parametrize("host", HOSTS)
def test_update_before_install_fails_visibly(host: str, tmp_path: Path):
    adapter = get_adapter(host, home=_home_for(host, tmp_path))
    result = adapter.update()
    assert result.ok is False
    assert result.state == AdapterState.NOT_INSTALLED


@pytest.mark.parametrize("host", HOSTS)
def test_uninstall_removes_hook_and_metadata(host: str, tmp_path: Path):
    home = _home_for(host, tmp_path)
    home.mkdir(parents=True)
    config_path = home / HOST_CONFIG_FILE[host]
    config_path.write_text(
        json.dumps(
            {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "echo keep"}]}]}}
        )
    )
    adapter, _ = _install(host, tmp_path)

    result = adapter.uninstall()
    assert result.ok is True
    assert result.state == AdapterState.NOT_INSTALLED

    data = json.loads(config_path.read_text())
    commands = [h["command"] for group in data["hooks"]["SessionStart"] for h in group["hooks"]]
    assert "echo keep" in commands
    assert not any(MANAGED_COMMAND_TOKEN in c for c in commands)
    assert adapter.status().state == AdapterState.NOT_INSTALLED
    assert adapter.status().consent_granted is False


@pytest.mark.parametrize("host", HOSTS)
def test_capability_fails_visibly_when_host_home_absent(host: str, tmp_path: Path):
    missing = tmp_path / "nope" / "deeper" / f".{host}"
    adapter = get_adapter(host, home=missing)
    capability = adapter.capability()
    assert capability.supported is False
    result = adapter.install(consent=True)
    assert result.ok is False
    assert "fails visibly" in result.message


# --- runner: exactly-once pre-work invocation with bounded context ---


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.asyncio
async def test_prework_invokes_preflight_once_with_bounded_context(host: str, tmp_path: Path):
    adapter, _ = _install(host, tmp_path)
    daemon = _FakeDaemon()
    event = _startup_event(tmp_path)

    result = await run_prework(host, event, home=adapter.home(), client_factory=lambda: daemon)
    assert result.outcome == PreworkOutcome.INVOKED
    assert result.preflight_invoked is True
    assert len(daemon.requests) == 1

    request = daemon.requests[0]
    assert request["command"]["name"] == "preflight.run"
    params = request["command"]["params"]
    assert params["checkpoint_hint"] == "pre_work"
    # Bounded context only: no transcript/secret/tool-output keys forwarded, and
    # only allowlisted preflight params are present.
    forbidden_present = set(params) & FORBIDDEN_EVENT_FIELDS
    assert forbidden_present == set()
    assert set(params) <= {"files", "symbols", "diff_context", "branch", "checkpoint_hint"}
    # Correlation/idempotency metadata rides in the request authority.
    audit = request["authority"]["audit_metadata"]
    assert audit["surface"] == "mcp"
    assert audit["correlation_id"] == f"{host}:sess-1"
    assert audit["idempotency_key"] == f"{host}:sess-1"


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.asyncio
async def test_prework_is_idempotent_per_task_boundary(host: str, tmp_path: Path):
    adapter, _ = _install(host, tmp_path)
    daemon = _FakeDaemon()
    event = _startup_event(tmp_path)

    first = await run_prework(host, event, home=adapter.home(), client_factory=lambda: daemon)
    second = await run_prework(host, event, home=adapter.home(), client_factory=lambda: daemon)

    assert first.outcome == PreworkOutcome.INVOKED
    assert second.outcome == PreworkOutcome.SKIPPED_ALREADY_FIRED
    assert len(daemon.requests) == 1


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("source", ["resume", "compact", "clear"])
@pytest.mark.asyncio
async def test_prework_does_not_fire_mid_session_sources(host: str, source: str, tmp_path: Path):
    adapter, _ = _install(host, tmp_path)
    daemon = _FakeDaemon()
    event = {"session_id": "s", "source": source, "cwd": str(tmp_path)}

    result = await run_prework(host, event, home=adapter.home(), client_factory=lambda: daemon)
    assert result.outcome == PreworkOutcome.SKIPPED_NOT_PREWORK
    assert result.preflight_invoked is False
    assert daemon.requests == []


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.asyncio
async def test_prework_skips_when_disabled(host: str, tmp_path: Path):
    adapter, _ = _install(host, tmp_path)
    adapter.disable()
    daemon = _FakeDaemon()

    result = await run_prework(
        host, _startup_event(tmp_path), home=adapter.home(), client_factory=lambda: daemon
    )
    assert result.outcome == PreworkOutcome.SKIPPED_DISABLED
    assert daemon.requests == []


# --- runner: visible fallback, never claims preflight ran ---


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.asyncio
async def test_protocol_mismatch_falls_back_visibly(host: str, tmp_path: Path):
    adapter, _ = _install(host, tmp_path)
    daemon = _FakeDaemon(protocol_version="v0-ancient")

    result = await run_prework(
        host, _startup_event(tmp_path), home=adapter.home(), client_factory=lambda: daemon
    )
    assert result.outcome == PreworkOutcome.FALLBACK_PROTOCOL_MISMATCH
    assert result.preflight_invoked is False
    assert "manual" in result.message.lower() or "explicit" in result.message.lower()
    # A fallback must not consume the once-per-boundary marker (retry allowed).
    assert adapter._store().has_fired(f"{host}:sess-1") is False


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.asyncio
async def test_unsupported_capability_falls_back_visibly(host: str, tmp_path: Path):
    adapter, _ = _install(host, tmp_path)
    daemon = _FakeDaemon(supported=["lookup.query"])

    result = await run_prework(
        host, _startup_event(tmp_path), home=adapter.home(), client_factory=lambda: daemon
    )
    assert result.outcome == PreworkOutcome.FALLBACK_CAPABILITY_UNSUPPORTED
    assert result.preflight_invoked is False


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.asyncio
async def test_daemon_unavailable_falls_back_visibly(host: str, tmp_path: Path):
    from daemon_client import DaemonConnectionError

    adapter, _ = _install(host, tmp_path)
    daemon = _FakeDaemon(raise_on_capabilities=DaemonConnectionError("boom"))

    result = await run_prework(
        host, _startup_event(tmp_path), home=adapter.home(), client_factory=lambda: daemon
    )
    assert result.outcome == PreworkOutcome.FALLBACK_DAEMON_UNAVAILABLE
    assert result.preflight_invoked is False


# --- host independence ---


@pytest.mark.asyncio
async def test_host_evidence_is_independent(tmp_path: Path):
    # Installing/enabling Claude Code must not enable Codex, and vice versa.
    claude, _ = _install("claude", tmp_path)
    codex_home = _home_for("codex", tmp_path)
    codex = get_adapter("codex", home=codex_home)
    assert claude.status().state == AdapterState.ENABLED
    assert codex.status().state == AdapterState.NOT_INSTALLED

    daemon = _FakeDaemon()
    result = await run_prework(
        "codex",
        {"session_id": "c1", "source": "startup", "cwd": str(tmp_path)},
        home=codex_home,
        client_factory=lambda: daemon,
    )
    assert result.outcome == PreworkOutcome.SKIPPED_DISABLED
    assert daemon.requests == []


# --- privacy guards ---


def test_forbidden_field_detector_flags_transcript_and_secrets():
    payload = {
        "session_id": "x",
        "cwd": "/repo",
        "transcript_path": "/t.jsonl",
        "api_key": "sk-secret",
    }
    flagged = assert_no_forbidden_fields(payload)
    assert "transcript_path" in flagged
    assert "api_key" in flagged
    assert "session_id" not in flagged


def test_context_never_carries_transcript():
    context = PreworkContext(
        host="claude",
        correlation_id="claude:s",
        task_boundary="session_start",
        workspace="/repo",
        branch="dev",
    )
    args = context.to_preflight_arguments()
    assert set(args) & FORBIDDEN_EVENT_FIELDS == set()
    assert context.transcript_included is False
