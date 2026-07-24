"""Base class and shared helpers for host pre-work adapters.

Each concrete adapter (Claude Code, Codex) owns a *host-native* configuration
mechanism. The base class owns the host-agnostic lifecycle — consent gating,
install/status/update/disable/uninstall, and identification of MCP-managed hook
entries — and delegates the host-specific config file shape to subclasses.

No adapter depends on the Bicameral Factory at runtime. The Factory is never
installed, imported, fetched, or read by this code path.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from version import SERVER_VERSION

from .context import BoundedContextDescriptor, PreworkContext
from .state import (
    ADAPTER_CONTRACT_VERSION,
    MCP_STATE_DIRNAME,
    AdapterMetadata,
    AdapterState,
    AdapterStore,
    ConsentRecord,
)

#: Substring that identifies a hook command as MCP-managed. Detection uses this
#: plus the host id so an adapter never edits or removes unrelated user hooks.
MANAGED_COMMAND_TOKEN = "prework-run"

#: The Codex/Claude session-start "source" values that are genuine pre-work
#: boundaries. ``resume``/``compact``/``clear`` continue an existing session and
#: must not fire an automatic pre-work invocation.
PREWORK_SOURCES: frozenset[str] = frozenset({"startup"})


class HostConfigError(RuntimeError):
    """The host configuration cannot be read or changed without data loss."""


@dataclass(frozen=True)
class PackageProvenance:
    """Exact customer package command installed into a host configuration."""

    package_name: str
    package_version: str
    runner_invocation: str
    executable_path: str
    source: str

    def to_dict(self) -> dict[str, str]:
        return {
            "package_name": self.package_name,
            "package_version": self.package_version,
            "runner_invocation": self.runner_invocation,
            "executable_path": self.executable_path,
            "source": self.source,
        }


def resolve_package_provenance() -> PackageProvenance:
    """Return the command prefix that invokes the MCP pre-work runner.

    Prefers the installed ``bicameral-mcp`` console script (absolute path) so the
    hook keeps working regardless of the host's PATH at fire time. Falls back to
    ``<python> -m server`` which runs the same entrypoint.
    """
    active_environment_console = Path(sys.executable).absolute().parent / "bicameral-mcp"
    if active_environment_console.is_file():
        executable = str(active_environment_console)
        return PackageProvenance(
            package_name="bicameral-mcp",
            package_version=SERVER_VERSION,
            runner_invocation=_quote(executable),
            executable_path=executable,
            source="console_script",
        )
    executable = str(Path(sys.executable).absolute())
    return PackageProvenance(
        package_name="bicameral-mcp",
        package_version=SERVER_VERSION,
        runner_invocation=f"{_quote(executable)} -m server",
        executable_path=executable,
        source="python_module",
    )


def resolve_runner_invocation() -> str:
    return resolve_package_provenance().runner_invocation


def _quote(value: str) -> str:
    return f'"{value}"' if (" " in value or "\\" in value) else value


@dataclass(frozen=True)
class HostCapability:
    """Result of checking whether the host mechanism can honestly be used."""

    host: str
    supported: bool
    mechanism: str
    detail: str
    config_path: str


@dataclass(frozen=True)
class HostSessionEvent:
    """Parsed, allowlisted view of a host pre-work event payload."""

    session_id: str
    source: str
    cwd: str | None


@dataclass(frozen=True)
class AdapterActionResult:
    """Outcome of an install/update/disable/uninstall action."""

    host: str
    action: str
    ok: bool
    state: AdapterState
    message: str
    package_provenance: PackageProvenance | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "action": self.action,
            "ok": self.ok,
            "state": self.state.value,
            "message": self.message,
            "package_provenance": (
                self.package_provenance.to_dict() if self.package_provenance else None
            ),
        }


@dataclass(frozen=True)
class AdapterStatus:
    """Inspectable status of a host adapter on this machine."""

    host: str
    state: AdapterState
    mechanism: str
    config_path: str
    hook_present: bool
    capability_supported: bool
    contract_version: str | None
    consent_granted: bool
    detail: str
    package_provenance: PackageProvenance | None = None
    package_matches: bool = False
    fallback_state: AdapterState | None = None
    manual_preflight: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "state": self.state.value,
            "mechanism": self.mechanism,
            "config_path": self.config_path,
            "hook_present": self.hook_present,
            "capability_supported": self.capability_supported,
            "contract_version": self.contract_version,
            "consent_granted": self.consent_granted,
            "detail": self.detail,
            "package_provenance": (
                self.package_provenance.to_dict() if self.package_provenance else None
            ),
            "package_matches": self.package_matches,
            "fallback_state": self.fallback_state.value if self.fallback_state else None,
            "manual_preflight": self.manual_preflight,
        }


class HostAdapter(ABC):
    """A consented, MCP-owned pre-work adapter for a single coding host."""

    host_id: str
    display_name: str
    #: Exact official mechanism/version this adapter targets, recorded as
    #: honest per-host evidence.
    official_mechanism: str

    def __init__(self, home: Path | None = None) -> None:
        self._home = Path(home) if home is not None else self.default_home()

    # --- host-specific surface (implemented by subclasses) ---

    @abstractmethod
    def default_home(self) -> Path:
        """Return the host config home (e.g. ``~/.claude`` or ``~/.codex``)."""

    @abstractmethod
    def config_path(self) -> Path:
        """Return the host config file the adapter reads/writes."""

    @abstractmethod
    def parse_event(self, payload: dict[str, Any]) -> HostSessionEvent:
        """Parse a raw host event payload into an allowlisted event view."""

    def home(self) -> Path:
        return self._home

    # --- shared lifecycle ---

    def _store(self) -> AdapterStore:
        return AdapterStore(self._home / MCP_STATE_DIRNAME)

    def bounded_context_descriptor(self) -> BoundedContextDescriptor:
        return BoundedContextDescriptor()

    def capability(self) -> HostCapability:
        """Honest capability probe: is the host mechanism usable here?

        The mechanism is a documented host-native pre-work hook. It is
        considered available when the host config home exists (the host is
        installed for this user) or can be created. The result is host-specific;
        a positive Claude Code probe never implies Codex support.
        """
        config = self.config_path()
        home = self._home
        supported = home.exists() or home.parent.exists()
        if supported:
            detail = (
                f"{self.display_name} config home is present; the "
                f"{self.official_mechanism} mechanism can be configured."
            )
        else:
            detail = (
                f"{self.display_name} config home {home} was not found and its "
                "parent is missing; cannot honestly configure an automatic "
                "pre-work hook. Use explicit/manual bicameral.preflight instead."
            )
        return HostCapability(
            host=self.host_id,
            supported=supported,
            mechanism=self.official_mechanism,
            detail=detail,
            config_path=str(config),
        )

    def install(self, *, consent: bool) -> AdapterActionResult:
        if not consent:
            descriptor = self.bounded_context_descriptor().render()
            return AdapterActionResult(
                host=self.host_id,
                action="install",
                ok=False,
                state=self.status().state,
                message=(
                    "Explicit consent is required to enable the "
                    f"{self.display_name} pre-work adapter. Re-run with consent "
                    "to enable it. The adapter will send only bounded context:\n"
                    f"{descriptor}"
                ),
            )
        capability = self.capability()
        if not capability.supported:
            return AdapterActionResult(
                host=self.host_id,
                action="install",
                ok=False,
                state=AdapterState.HOST_MECHANISM_UNAVAILABLE,
                message=(
                    f"Cannot install: {capability.detail} This host criterion "
                    "fails visibly rather than simulating support."
                ),
            )
        provenance = resolve_package_provenance()
        self._write_hook(provenance.runner_invocation)
        consent_summary = self.bounded_context_descriptor().render().splitlines()[0]
        metadata = AdapterMetadata(
            host=self.host_id,
            installed=True,
            enabled=True,
            contract_version=ADAPTER_CONTRACT_VERSION,
            runner_invocation=provenance.runner_invocation,
            consent=ConsentRecord.now(consent_summary),
            updated_at="",
            package_version=provenance.package_version,
            runner_executable=provenance.executable_path,
            runner_source=provenance.source,
        )
        self._store().save(metadata)
        return AdapterActionResult(
            host=self.host_id,
            action="install",
            ok=True,
            state=AdapterState.ENABLED,
            message=(
                f"Installed and enabled the {self.display_name} pre-work adapter "
                f"via {self.official_mechanism}. It invokes bicameral.preflight "
                "once per new session with bounded context only."
            ),
            package_provenance=provenance,
        )

    def update(self) -> AdapterActionResult:
        metadata = self._store().load()
        if metadata is None or not metadata.installed:
            return AdapterActionResult(
                host=self.host_id,
                action="update",
                ok=False,
                state=AdapterState.NOT_INSTALLED,
                message=(
                    f"The {self.display_name} adapter is not installed; nothing "
                    "to update. Install it first with explicit consent."
                ),
            )
        provenance = resolve_package_provenance()
        runner = provenance.runner_invocation
        if metadata.enabled:
            self._write_hook(runner)
        metadata.runner_invocation = runner
        metadata.contract_version = ADAPTER_CONTRACT_VERSION
        metadata.package_version = provenance.package_version
        metadata.runner_executable = provenance.executable_path
        metadata.runner_source = provenance.source
        self._store().save(metadata)
        return AdapterActionResult(
            host=self.host_id,
            action="update",
            ok=True,
            state=AdapterState.ENABLED if metadata.enabled else AdapterState.DISABLED,
            message=(
                f"Updated the {self.display_name} adapter to contract version "
                f"{ADAPTER_CONTRACT_VERSION}."
            ),
            package_provenance=provenance,
        )

    def disable(self) -> AdapterActionResult:
        metadata = self._store().load()
        if metadata is None or not metadata.installed:
            return AdapterActionResult(
                host=self.host_id,
                action="disable",
                ok=False,
                state=AdapterState.NOT_INSTALLED,
                message=f"The {self.display_name} adapter is not installed.",
            )
        provenance = _package_provenance_from_metadata(metadata)
        self._remove_hook()
        metadata.enabled = False
        self._store().save(metadata)
        return AdapterActionResult(
            host=self.host_id,
            action="disable",
            ok=True,
            state=AdapterState.DISABLED,
            message=(
                f"Disabled the {self.display_name} pre-work adapter. The hook "
                "entry was removed; consent metadata is retained. Automatic "
                "pre-work will not fire until re-enabled."
            ),
            package_provenance=provenance,
        )

    def uninstall(self) -> AdapterActionResult:
        metadata = self._store().load()
        provenance = _package_provenance_from_metadata(metadata)
        self._remove_hook()
        self._store().clear()
        return AdapterActionResult(
            host=self.host_id,
            action="uninstall",
            ok=True,
            state=AdapterState.NOT_INSTALLED,
            message=(
                f"Uninstalled the {self.display_name} pre-work adapter. The hook "
                "entry, consent record, and dedup markers were removed."
            ),
            package_provenance=provenance,
        )

    def status(self) -> AdapterStatus:
        metadata = self._store().load()
        config_error: HostConfigError | None = None
        config_data: dict[str, Any] = {}
        try:
            config_data = self._read_config()
            hook_present = self._hook_present_in(config_data)
        except HostConfigError as exc:
            hook_present = False
            config_error = exc
        capability = self.capability()
        if config_error is not None:
            state = AdapterState.CONFIG_INVALID_FAIL_CLOSED
        elif not capability.supported:
            state = AdapterState.HOST_MECHANISM_UNAVAILABLE
        elif metadata is None or not metadata.installed:
            state = AdapterState.NOT_INSTALLED
        elif metadata.enabled:
            state = AdapterState.ENABLED if hook_present else AdapterState.MANUAL_FALLBACK_REQUIRED
        else:
            state = AdapterState.DISABLED
        detail = capability.detail
        if config_error is not None:
            detail = str(config_error)
        elif metadata is not None and metadata.enabled and not hook_present:
            detail = (
                "Adapter metadata reports enabled but no managed hook entry is "
                "present in the host config; re-run update or install."
            )
        provenance = None
        package_matches = False
        if metadata is not None and metadata.installed:
            provenance = _package_provenance_from_metadata(metadata)
            exact_hook_matches = not metadata.enabled
            if metadata.enabled and config_error is None and hook_present:
                exact_hook_matches = self._hook_present_in(
                    config_data, expected_command=self._managed_command(metadata.runner_invocation)
                )
            package_matches = bool(
                metadata.contract_version == ADAPTER_CONTRACT_VERSION
                and metadata.package_version == SERVER_VERSION
                and metadata.runner_executable
                and Path(metadata.runner_executable).is_file()
                and exact_hook_matches
            )
            if not package_matches and config_error is None and hook_present:
                state = AdapterState.PACKAGE_MISSING_OR_MISMATCHED
                detail = (
                    "The installed Bicameral MCP command is missing or does not match the "
                    "recorded package version; re-run adapter update from the intended package."
                )
        fallback_states = {
            AdapterState.HOST_MECHANISM_UNAVAILABLE,
            AdapterState.CONFIG_INVALID_FAIL_CLOSED,
            AdapterState.PACKAGE_MISSING_OR_MISMATCHED,
            AdapterState.MANUAL_FALLBACK_REQUIRED,
        }
        fallback_state = AdapterState.MANUAL_FALLBACK_REQUIRED if state in fallback_states else None
        manual_preflight = (
            "Invoke bicameral.preflight explicitly from the host until automatic pre-work "
            "is available again."
            if fallback_state is not None
            else ""
        )
        return AdapterStatus(
            host=self.host_id,
            state=state,
            mechanism=self.official_mechanism,
            config_path=str(self.config_path()),
            hook_present=hook_present,
            capability_supported=capability.supported,
            contract_version=metadata.contract_version if metadata else None,
            consent_granted=bool(metadata and metadata.consent and metadata.consent.granted),
            detail=detail,
            package_provenance=provenance,
            package_matches=package_matches,
            fallback_state=fallback_state,
            manual_preflight=manual_preflight,
        )

    def is_prework_boundary(self, event: HostSessionEvent) -> bool:
        """True only for a genuine pre-work (new-session) boundary."""
        return event.source in PREWORK_SOURCES

    def build_context(self, event: HostSessionEvent) -> PreworkContext:
        workspace = event.cwd
        branch = _resolve_branch(workspace)
        return PreworkContext(
            host=self.host_id,
            correlation_id=f"{self.host_id}:{event.session_id}",
            task_boundary="session_start",
            workspace=workspace,
            branch=branch,
            transcript_included=False,
        )

    # --- host JSON hooks manipulation (shared shape) ---

    def _managed_command(self, runner_invocation: str) -> str:
        return f"{runner_invocation} {MANAGED_COMMAND_TOKEN} --host {self.host_id}"

    def _is_managed_command(self, command: str) -> bool:
        return MANAGED_COMMAND_TOKEN in command and f"--host {self.host_id}" in command

    def _read_config(self) -> dict[str, Any]:
        path = self.config_path()
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise HostConfigError(
                f"Cannot read host config {path}; refusing to modify it."
            ) from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HostConfigError(
                f"Host config {path} contains malformed JSON; refusing to modify it."
            ) from exc
        if not isinstance(data, dict):
            raise HostConfigError(
                f"Host config {path} must contain a JSON object; refusing to modify it."
            )
        return data

    def _write_config(self, data: dict[str, Any]) -> None:
        path = self.config_path()
        payload = (json.dumps(data, indent=2, sort_keys=True) + "\n").encode()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            existing = path.read_bytes() if path.exists() else None
            mode = stat.S_IMODE(path.stat().st_mode) if existing is not None else None
            if existing is not None:
                backup = path.with_name(f"{path.name}.bicameral-backup")
                _atomic_replace_bytes(backup, existing, mode=mode)
            _atomic_replace_bytes(path, payload, mode=mode)
        except OSError as exc:
            raise HostConfigError(
                f"Cannot atomically update host config {path}; the current config was not replaced."
            ) from exc

    def _session_start_groups(self, data: dict[str, Any]) -> list[Any]:
        hooks = data.get("hooks")
        if not isinstance(hooks, dict):
            return []
        groups = hooks.get("SessionStart")
        return groups if isinstance(groups, list) else []

    def _hook_present(self, *, expected_command: str | None = None) -> bool:
        return self._hook_present_in(self._read_config(), expected_command=expected_command)

    def _hook_present_in(
        self,
        data: dict[str, Any],
        *,
        expected_command: str | None = None,
    ) -> bool:
        for group in self._session_start_groups(data):
            if not isinstance(group, dict):
                continue
            for handler in group.get("hooks", []):
                if (
                    isinstance(handler, dict)
                    and handler.get("type") == "command"
                    and isinstance(handler.get("command"), str)
                    and self._is_managed_command(handler["command"])
                    and (expected_command is None or handler["command"] == expected_command)
                ):
                    return True
        return False

    def _write_hook(self, runner_invocation: str) -> None:
        data = self._read_config()
        self._remove_managed_from(data)
        hooks = data.setdefault("hooks", {})
        if not isinstance(hooks, dict):
            hooks = {}
            data["hooks"] = hooks
        session_start = hooks.setdefault("SessionStart", [])
        if not isinstance(session_start, list):
            session_start = []
            hooks["SessionStart"] = session_start
        session_start.append(
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": self._managed_command(runner_invocation),
                    }
                ]
            }
        )
        self._write_config(data)

    def _remove_hook(self) -> None:
        data = self._read_config()
        if self._remove_managed_from(data):
            self._write_config(data)

    def _remove_managed_from(self, data: dict[str, Any]) -> bool:
        hooks = data.get("hooks")
        if not isinstance(hooks, dict):
            return False
        groups = hooks.get("SessionStart")
        if not isinstance(groups, list):
            return False
        changed = False
        new_groups: list[Any] = []
        for group in groups:
            if not isinstance(group, dict):
                new_groups.append(group)
                continue
            handlers = group.get("hooks")
            if not isinstance(handlers, list):
                new_groups.append(group)
                continue
            kept = [
                handler
                for handler in handlers
                if not (
                    isinstance(handler, dict)
                    and handler.get("type") == "command"
                    and isinstance(handler.get("command"), str)
                    and self._is_managed_command(handler["command"])
                )
            ]
            if len(kept) != len(handlers):
                changed = True
            if kept:
                new_group = dict(group)
                new_group["hooks"] = kept
                new_groups.append(new_group)
        if changed:
            if new_groups:
                hooks["SessionStart"] = new_groups
            else:
                hooks.pop("SessionStart", None)
            if not hooks:
                data.pop("hooks", None)
        return changed


def _resolve_branch(workspace: str | None) -> str | None:
    import subprocess

    root = workspace or os.getcwd()
    try:
        branch = (
            subprocess.check_output(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=root,
                stderr=subprocess.DEVNULL,
            )
            .decode()
            .strip()
        )
    except Exception:
        return None
    return branch or None


def _package_provenance_from_metadata(
    metadata: AdapterMetadata | None,
) -> PackageProvenance | None:
    if metadata is None or not metadata.installed:
        return None
    return PackageProvenance(
        package_name="bicameral-mcp",
        package_version=metadata.package_version,
        runner_invocation=metadata.runner_invocation,
        executable_path=metadata.runner_executable,
        source=metadata.runner_source,
    )


def _atomic_replace_bytes(path: Path, payload: bytes, *, mode: int | None) -> None:
    """Write bytes beside ``path`` and atomically replace the destination."""
    fd, raw_temp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(raw_temp)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(temp_path, mode)
        os.replace(temp_path, path)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        temp_path.unlink(missing_ok=True)
        raise
