"""Focused component tests for MCP-distributed host pre-work adapters.

Exercises the real packaged installer/adapter code and the real Claude Code and
Codex configuration file formats in isolated temporary host homes. The daemon
is a test-only command runner double; the host hook mechanism (config-file
writing/removal) is the real product path under test. Evidence is host-specific:
each behavior is asserted for both hosts independently.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

import pytest

import preflight_adapters.base as adapters_base
from preflight_adapters import get_adapter, supported_hosts
from preflight_adapters.base import (
    MANAGED_COMMAND_TOKEN,
    HostConfigError,
    resolve_package_provenance,
)
from preflight_adapters.cli import run_adapters_cli
from preflight_adapters.context import (
    FORBIDDEN_EVENT_FIELDS,
    PreworkContext,
    assert_no_forbidden_fields,
)
from preflight_adapters.runner import PreworkOutcome, run_prework
from preflight_adapters.state import ADAPTER_CONTRACT_VERSION, AdapterState
from version import SERVER_VERSION, TOOLREQUEST_PROTOCOL_VERSION

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


def test_package_provenance_prefers_active_environment_console_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    bin_dir = tmp_path / "venv" / "bin"
    bin_dir.mkdir(parents=True)
    base_bin = tmp_path / "runtime" / "bin"
    base_bin.mkdir(parents=True)
    base_python = base_bin / "python"
    base_python.write_text("")
    python = bin_dir / "python"
    python.symlink_to(base_python)
    console = bin_dir / "bicameral-mcp"
    console.write_text("")
    monkeypatch.setattr(adapters_base.sys, "executable", str(python))

    provenance = resolve_package_provenance()

    assert provenance.executable_path == str(console)
    assert provenance.runner_invocation == str(console)


def test_package_provenance_falls_back_to_active_interpreter_not_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    python = tmp_path / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("")
    monkeypatch.setattr(adapters_base.sys, "executable", str(python))

    provenance = resolve_package_provenance()

    assert provenance.source == "python_module"
    assert provenance.executable_path == str(python)
    assert provenance.runner_invocation == f"{python} -m server"


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
    assert not config_path.with_name(f"{config_path.name}.bicameral-backup").exists()

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


def test_codex_clean_install_creates_discoverable_user_config_layer(tmp_path: Path):
    home = _home_for("codex", tmp_path)
    adapter = get_adapter("codex", home=home)

    result = adapter.install(consent=True)

    assert result.ok is True
    assert (home / "config.toml").read_bytes() == b""
    assert adapter.config_path().is_file()


def test_codex_lifecycle_preserves_existing_user_config_layer(tmp_path: Path):
    home = _home_for("codex", tmp_path)
    home.mkdir(parents=True)
    config = home / "config.toml"
    original = b'model = "gpt-5.3-codex"\n[projects."/workspace"]\ntrust_level = "trusted"\n'
    config.write_bytes(original)

    adapter, _ = _install("codex", tmp_path)
    adapter.update()
    adapter.disable()
    adapter.uninstall()

    assert config.read_bytes() == original


@pytest.mark.parametrize("host", HOSTS)
def test_install_and_status_report_exact_package_provenance(host: str, tmp_path: Path):
    adapter, result = _install(host, tmp_path)

    installed = result.to_dict()["package_provenance"]
    assert installed["package_name"] == "bicameral-mcp"
    assert installed["package_version"] == SERVER_VERSION
    assert Path(installed["executable_path"]).is_absolute()
    assert installed["runner_invocation"] in adapter.config_path().read_text()

    status = adapter.status().to_dict()
    assert status["package_provenance"] == installed
    assert status["package_matches"] is True


@pytest.mark.parametrize("host", HOSTS)
def test_status_fails_closed_when_installed_package_is_missing(host: str, tmp_path: Path):
    adapter, _ = _install(host, tmp_path)
    metadata_path = adapter.home() / "bicameral-mcp" / "adapter.json"
    metadata = json.loads(metadata_path.read_text())
    metadata["runner_executable"] = str(tmp_path / "missing-bicameral-mcp")
    metadata_path.write_text(json.dumps(metadata))

    status = adapter.status()

    assert status.state == AdapterState.PACKAGE_MISSING_OR_MISMATCHED
    assert status.package_matches is False
    assert "update" in status.detail.lower()


@pytest.mark.parametrize("host", HOSTS)
def test_status_fails_closed_when_hook_command_differs_from_recorded_package(
    host: str, tmp_path: Path
):
    adapter, _ = _install(host, tmp_path)
    config = json.loads(adapter.config_path().read_text())
    config["hooks"]["SessionStart"][0]["hooks"][0]["command"] = (
        f"/bin/sh {MANAGED_COMMAND_TOKEN} --host {host}"
    )
    adapter.config_path().write_text(json.dumps(config))

    status = adapter.status()

    assert status.hook_present is True
    assert status.state == AdapterState.PACKAGE_MISSING_OR_MISMATCHED
    assert status.package_matches is False


@pytest.mark.parametrize("host", HOSTS)
def test_update_migrates_an_older_adapter_contract(host: str, tmp_path: Path):
    adapter, _ = _install(host, tmp_path)
    metadata_path = adapter.home() / "bicameral-mcp" / "adapter.json"
    metadata = json.loads(metadata_path.read_text())
    metadata["contract_version"] = "1"
    metadata_path.write_text(json.dumps(metadata))

    before = adapter.status()
    updated = adapter.update()
    after = adapter.status()

    assert before.state == AdapterState.PACKAGE_MISSING_OR_MISMATCHED
    assert updated.ok is True
    assert after.state == AdapterState.INSTALLED_ENABLED
    assert after.contract_version == ADAPTER_CONTRACT_VERSION
    assert after.package_matches is True


@pytest.mark.parametrize("host", HOSTS)
def test_enabled_metadata_without_hook_requires_manual_fallback(host: str, tmp_path: Path):
    adapter, _ = _install(host, tmp_path)
    adapter.config_path().write_text("{}\n")

    status = adapter.status()

    assert status.state == AdapterState.MANUAL_FALLBACK_REQUIRED
    assert status.fallback_state == AdapterState.MANUAL_FALLBACK_REQUIRED
    assert "update" in status.detail.lower()


@pytest.mark.parametrize("host", HOSTS)
def test_lifecycle_receipts_retain_installed_package_provenance(host: str, tmp_path: Path):
    adapter, installed = _install(host, tmp_path)
    results = [installed, adapter.update(), adapter.disable(), adapter.uninstall()]

    for result in results:
        provenance = result.to_dict()["package_provenance"]
        assert provenance["package_name"] == "bicameral-mcp"
        assert provenance["package_version"] == SERVER_VERSION
        assert Path(provenance["executable_path"]).is_file()


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
def test_install_rejects_malformed_config_without_changing_it(host: str, tmp_path: Path):
    home = _home_for(host, tmp_path)
    home.mkdir(parents=True)
    config_path = home / HOST_CONFIG_FILE[host]
    original = b'{"hooks": '
    config_path.write_bytes(original)

    adapter = get_adapter(host, home=home)
    with pytest.raises(HostConfigError, match="malformed JSON"):
        adapter.install(consent=True)

    assert config_path.read_bytes() == original
    if host == "codex":
        assert not (home / "config.toml").exists()


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("original", [b"[]", b"null", b'"scalar"', b"1"])
def test_install_rejects_non_object_config_without_changing_it(
    host: str, original: bytes, tmp_path: Path
):
    home = _home_for(host, tmp_path)
    home.mkdir(parents=True)
    config_path = home / HOST_CONFIG_FILE[host]
    config_path.write_bytes(original)

    adapter = get_adapter(host, home=home)
    with pytest.raises(HostConfigError, match="must contain a JSON object"):
        adapter.install(consent=True)

    assert config_path.read_bytes() == original


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("action", ["update", "disable", "uninstall"])
def test_installed_lifecycle_rejects_malformed_config_without_losing_state(
    host: str, action: str, tmp_path: Path
):
    adapter, _ = _install(host, tmp_path)
    config_path = adapter.config_path()
    installed_config = config_path.read_bytes()
    malformed = b'{"hooks": '
    config_path.write_bytes(malformed)

    with pytest.raises(HostConfigError, match="malformed JSON"):
        getattr(adapter, action)()

    assert config_path.read_bytes() == malformed
    config_path.write_bytes(installed_config)
    status = adapter.status()
    assert status.state == AdapterState.ENABLED
    assert status.consent_granted is True


@pytest.mark.parametrize("host", HOSTS)
def test_status_reports_invalid_config_without_mutating_it(host: str, tmp_path: Path):
    adapter, _ = _install(host, tmp_path)
    config_path = adapter.config_path()
    malformed = b'{"hooks": '
    config_path.write_bytes(malformed)

    status = adapter.status()

    assert status.state == AdapterState.CONFIG_INVALID_FAIL_CLOSED
    assert status.hook_present is False
    assert "refusing to modify" in status.detail
    assert config_path.read_bytes() == malformed


@pytest.mark.parametrize("host", HOSTS)
def test_cli_emits_machine_readable_fail_closed_config_receipt(
    host: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    home = _home_for(host, tmp_path)
    home.mkdir(parents=True)
    config_path = home / HOST_CONFIG_FILE[host]
    malformed = b'{"hooks": '
    config_path.write_bytes(malformed)

    exit_code = run_adapters_cli(
        ["install", "--host", host, "--home", str(home), "--consent", "--json"]
    )

    receipt = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert receipt["ok"] is False
    assert receipt["state"] == "config_invalid_fail_closed"
    assert "refusing to modify" in receipt["message"]
    assert config_path.read_bytes() == malformed


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission boundary")
@pytest.mark.parametrize("host", HOSTS)
def test_install_rejects_unreadable_config_without_changing_it(host: str, tmp_path: Path):
    home = _home_for(host, tmp_path)
    home.mkdir(parents=True)
    config_path = home / HOST_CONFIG_FILE[host]
    original = b'{"unrelated_setting": true}\n'
    config_path.write_bytes(original)
    config_path.chmod(0)
    if os.access(config_path, os.R_OK):
        config_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        pytest.skip("filesystem does not enforce owner read permissions")

    adapter = get_adapter(host, home=home)
    try:
        with pytest.raises(HostConfigError, match="Cannot read host config"):
            adapter.install(consent=True)
    finally:
        config_path.chmod(stat.S_IRUSR | stat.S_IWUSR)

    assert config_path.read_bytes() == original


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission boundary")
@pytest.mark.parametrize("host", HOSTS)
def test_install_write_failure_leaves_existing_config_unchanged(host: str, tmp_path: Path):
    home = _home_for(host, tmp_path)
    home.mkdir(parents=True)
    config_path = home / HOST_CONFIG_FILE[host]
    original = b'{"unrelated_setting": true}\n'
    config_path.write_bytes(original)
    if host == "codex":
        (home / "config.toml").write_bytes(b"")
    home.chmod(stat.S_IRUSR | stat.S_IXUSR)
    if os.access(home, os.W_OK):
        home.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        pytest.skip("filesystem does not enforce owner directory write permissions")

    adapter = get_adapter(host, home=home)
    try:
        with pytest.raises(HostConfigError, match="Cannot atomically update host config"):
            adapter.install(consent=True)
    finally:
        home.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)

    assert config_path.read_bytes() == original
    assert not config_path.with_name(f"{config_path.name}.bicameral-backup").exists()


@pytest.mark.parametrize("host", HOSTS)
def test_install_backs_up_existing_config_before_replacing_it(host: str, tmp_path: Path):
    home = _home_for(host, tmp_path)
    home.mkdir(parents=True)
    config_path = home / HOST_CONFIG_FILE[host]
    original = b'{\n  "unrelated_setting": {"keep": true}\n}\n'
    config_path.write_bytes(original)

    adapter = get_adapter(host, home=home)
    result = adapter.install(consent=True)

    assert result.ok is True
    backup_path = config_path.with_name(f"{config_path.name}.bicameral-backup")
    assert backup_path.read_bytes() == original
    assert json.loads(config_path.read_text())["unrelated_setting"] == {"keep": True}


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
    before_update = adapter.config_path().read_bytes()
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
    backup_path = adapter.config_path().with_name(f"{adapter.config_path().name}.bicameral-backup")
    assert backup_path.read_bytes() == before_update


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
    status = adapter.status()
    assert status.state == AdapterState.HOST_MECHANISM_UNAVAILABLE
    assert status.fallback_state == AdapterState.MANUAL_FALLBACK_REQUIRED
    assert "bicameral.preflight" in status.manual_preflight
    result = adapter.install(consent=True)
    assert result.ok is False
    assert result.state == AdapterState.HOST_MECHANISM_UNAVAILABLE
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
