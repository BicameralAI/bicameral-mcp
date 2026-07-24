"""MCP-local adapter install state and consent records.

These are MCP-owned local host configuration facts, not Decision lifecycle
state. ``installed``/``enabled`` describe whether an operator has consented to,
and switched on, a host pre-work adapter on this machine. The daemon owns all
canonical Decision, candidate, signoff, evidence, and compliance state.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

#: Contract version for the installed adapter configuration. ``update`` rewrites
#: an installed adapter when this differs from the recorded value.
ADAPTER_CONTRACT_VERSION = "2"

#: Directory (relative to a host config home) that holds MCP-owned adapter
#: metadata, consent records, and dedup markers. Kept separate from the host's
#: own configuration file so uninstalling never disturbs unrelated host state.
MCP_STATE_DIRNAME = "bicameral-mcp"


class AdapterState(StrEnum):
    """Local install state of a host pre-work adapter."""

    NOT_INSTALLED = "not_installed"
    INSTALLED_ENABLED = "installed_enabled"
    INSTALLED_DISABLED = "installed_disabled"
    MANUAL_FALLBACK_REQUIRED = "manual_fallback_required"
    HOST_MECHANISM_UNAVAILABLE = "host_mechanism_unavailable"
    CONFIG_INVALID_FAIL_CLOSED = "config_invalid_fail_closed"
    PACKAGE_MISSING_OR_MISMATCHED = "package_missing_or_mismatched"
    INSTALLED_BUT_HOST_DID_NOT_FIRE = "installed_but_host_did_not_fire"
    AUTHENTIC_HOST_FIRED = "authentic_host_fired"

    # Source compatibility for callers that imported the original enum names.
    ENABLED = INSTALLED_ENABLED
    DISABLED = INSTALLED_DISABLED


@dataclass(frozen=True)
class ConsentRecord:
    """Explicit operator consent to enable a host pre-work adapter."""

    granted: bool
    granted_at: str
    descriptor_summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "granted": self.granted,
            "granted_at": self.granted_at,
            "descriptor_summary": self.descriptor_summary,
        }

    @classmethod
    def now(cls, descriptor_summary: str) -> ConsentRecord:
        return cls(
            granted=True,
            granted_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            descriptor_summary=descriptor_summary,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConsentRecord:
        return cls(
            granted=bool(data.get("granted", False)),
            granted_at=str(data.get("granted_at", "")),
            descriptor_summary=str(data.get("descriptor_summary", "")),
        )


@dataclass
class AdapterMetadata:
    """Persisted MCP-owned metadata for one installed host adapter."""

    host: str
    installed: bool
    enabled: bool
    contract_version: str
    runner_invocation: str
    consent: ConsentRecord | None
    updated_at: str
    package_version: str = ""
    runner_executable: str = ""
    runner_source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "installed": self.installed,
            "enabled": self.enabled,
            "contract_version": self.contract_version,
            "runner_invocation": self.runner_invocation,
            "consent": self.consent.to_dict() if self.consent else None,
            "updated_at": self.updated_at,
            "package_version": self.package_version,
            "runner_executable": self.runner_executable,
            "runner_source": self.runner_source,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AdapterMetadata:
        consent_raw = data.get("consent")
        consent = ConsentRecord.from_dict(consent_raw) if isinstance(consent_raw, dict) else None
        return cls(
            host=str(data.get("host", "")),
            installed=bool(data.get("installed", False)),
            enabled=bool(data.get("enabled", False)),
            contract_version=str(data.get("contract_version", "")),
            runner_invocation=str(data.get("runner_invocation", "")),
            consent=consent,
            updated_at=str(data.get("updated_at", "")),
            package_version=str(data.get("package_version", "")),
            runner_executable=str(data.get("runner_executable", "")),
            runner_source=str(data.get("runner_source", "")),
        )


class AdapterStore:
    """Reads/writes MCP-owned adapter metadata and dedup markers on disk."""

    def __init__(self, state_dir: Path) -> None:
        self._state_dir = state_dir

    @property
    def state_dir(self) -> Path:
        return self._state_dir

    @property
    def _metadata_path(self) -> Path:
        return self._state_dir / "adapter.json"

    @property
    def _fired_dir(self) -> Path:
        return self._state_dir / "prework-fired"

    def load(self) -> AdapterMetadata | None:
        path = self._metadata_path
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        return AdapterMetadata.from_dict(data)

    def save(self, metadata: AdapterMetadata) -> None:
        metadata.updated_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._metadata_path.write_text(
            json.dumps(metadata.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def clear(self) -> None:
        """Remove all MCP-owned adapter metadata and dedup markers."""
        if self._metadata_path.is_file():
            self._metadata_path.unlink()
        if self._fired_dir.is_dir():
            for marker in self._fired_dir.iterdir():
                if marker.is_file():
                    marker.unlink()
            self._fired_dir.rmdir()
        if self._state_dir.is_dir() and not any(self._state_dir.iterdir()):
            self._state_dir.rmdir()

    # --- dedup / idempotency markers (operational witnesses only) ---

    def _marker_path(self, correlation_id: str) -> Path:
        safe = "".join(ch if ch.isalnum() or ch in "-._" else "_" for ch in correlation_id)
        return self._fired_dir / f"{safe}.json"

    def has_fired(self, correlation_id: str) -> bool:
        return self._marker_path(correlation_id).is_file()

    def mark_fired(self, correlation_id: str, detail: dict[str, Any]) -> None:
        self._fired_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "correlation_id": correlation_id,
            "fired_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            **detail,
        }
        self._marker_path(correlation_id).write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
