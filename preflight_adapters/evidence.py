"""Sanitized product-side evaluation of authentic host activation receipts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .state import AdapterState


class HostEvidenceError(ValueError):
    """External host evidence is malformed, unsafe, or overclaims activation."""


_FORBIDDEN_KEY = re.compile(
    r"(^|_)(raw_?(transcript|output|prompt)|api_?key|token|password|secret|credentials?)($|_)",
    re.IGNORECASE,
)
_FORBIDDEN_CHALLENGE_KEY = re.compile(r"challenge.*(value|secret|token)", re.IGNORECASE)
_FORBIDDEN_RAW_KEYS = {
    "assistant_message",
    "completion",
    "messages",
    "model_output",
    "model_response",
    "prompt_text",
    "pty_output",
    "raw_output",
    "raw_transcript",
    "stderr",
    "stdout",
    "terminal_output",
    "transcript",
}
_ALLOWED_PRESENCE_TOKENS = {"[redacted]", "present", "absent"}
_SECRET_PATTERNS = (
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}\b", re.IGNORECASE),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9_-]{15,}\b"),
)


def _assert_sanitized(value: Any, path: str = "receipt") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            name = str(key)
            lowered = name.lower()
            if lowered == "credential_presence_only":
                if not isinstance(child, dict):
                    raise HostEvidenceError(
                        f"credential presence map must be an object: {path}.{name}"
                    )
                for alias, presence in child.items():
                    if alias == "note":
                        continue
                    if presence not in _ALLOWED_PRESENCE_TOKENS:
                        raise HostEvidenceError(
                            f"credential presence must not contain values: {path}.{name}.{alias}"
                        )
                continue
            if (
                lowered in _FORBIDDEN_RAW_KEYS
                or _FORBIDDEN_KEY.search(name)
                or _FORBIDDEN_CHALLENGE_KEY.search(name)
            ):
                raise HostEvidenceError(f"forbidden evidence field: {path}.{name}")
            _assert_sanitized(child, f"{path}.{name}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_sanitized(child, f"{path}[{index}]")
    elif isinstance(value, str):
        if any(pattern.search(value) for pattern in _SECRET_PATTERNS):
            raise HostEvidenceError(f"credential-shaped value detected: {path}")


@dataclass(frozen=True)
class HostActivationResult:
    host: str
    state: AdapterState
    authentic: bool
    exactly_once: bool
    invocation_count: int
    provenance_method: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "state": self.state.value,
            "authentic": self.authentic,
            "exactly_once": self.exactly_once,
            "invocation_count": self.invocation_count,
            "provenance_method": self.provenance_method,
        }


def evaluate_host_activation(host: str, receipt: dict[str, Any]) -> HostActivationResult:
    """Evaluate one host's product-primary lane without upgrading missing evidence."""
    _assert_sanitized(receipt)
    lanes = receipt.get("lanes")
    if not isinstance(lanes, list):
        raise HostEvidenceError("host receipt lanes must be an array")
    matching = [
        lane
        for lane in lanes
        if isinstance(lane, dict)
        and lane.get("classification") == "product_primary"
        and lane.get("provider") == host
    ]
    if len(matching) != 1:
        raise HostEvidenceError(f"expected exactly one product_primary lane for {host}")
    lane = matching[0]
    chain = lane.get("hook_chain")
    if not isinstance(chain, list):
        raise HostEvidenceError("host receipt hook_chain must be an array")
    pids = {
        entry.get("pid")
        for entry in chain
        if isinstance(entry, dict) and isinstance(entry.get("pid"), int)
    }
    count = len(pids)
    fired = lane.get("host_fired_managed_hook") is True
    if not fired:
        return HostActivationResult(
            host=host,
            state=AdapterState.INSTALLED_BUT_HOST_DID_NOT_FIRE,
            authentic=False,
            exactly_once=False,
            invocation_count=count,
            provenance_method=str(lane.get("provenance_method") or "unavailable"),
        )
    provenance = str(lane.get("provenance_method") or "unavailable")
    if receipt.get("sanitized") is not True:
        raise HostEvidenceError("host receipt must be marked sanitized")
    if lane.get("direct_hook_invocation") is not False:
        raise HostEvidenceError("direct hook invocation cannot prove authentic host firing")
    topology_gates = (
        lane.get("clean_home") is True,
        lane.get("consented_install") is True,
        lane.get("released_artifact") is True,
        lane.get("version_probe_ok") is True,
        lane.get("hook_written") is True,
        lane.get("interaction_surface") == "pty",
    )
    if not all(topology_gates):
        raise HostEvidenceError("authentic host topology gates are not all satisfied")
    host_versions = receipt.get("host_versions")
    mapped_host_version = host_versions.get(host) if isinstance(host_versions, dict) else None
    if not (isinstance(lane.get("host_version"), str) and lane["host_version"].strip()) and not (
        isinstance(mapped_host_version, str) and mapped_host_version.strip()
    ):
        raise HostEvidenceError("exact released host version is required")
    if provenance not in {"kernel-exec", "proc-ancestry", "kernel-exec+proc-ancestry"}:
        raise HostEvidenceError("independent host-subtree provenance is required")
    if lane.get("evidence_ceiling") not in {
        "authentic-host/synthetic-operator",
        "named-human-accepted",
    }:
        raise HostEvidenceError("external receipt does not authorize an authentic-host claim")
    if count != 1:
        raise HostEvidenceError("authentic host evidence must contain exactly one hook process")
    return HostActivationResult(
        host=host,
        state=AdapterState.AUTHENTIC_HOST_FIRED,
        authentic=True,
        exactly_once=True,
        invocation_count=1,
        provenance_method=provenance,
    )
