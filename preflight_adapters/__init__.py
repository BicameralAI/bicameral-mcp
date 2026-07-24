"""MCP-distributed, consented pre-work adapters for coding hosts.

This package ships the *product* automation that lets a coding host (Claude
Code, Codex) run ``bicameral.preflight`` once at a genuine pre-work boundary,
with explicit consent and bounded context. It is owned and distributed by
``bicameral-mcp`` and never depends on the Bicameral Factory at runtime.

Repo/team development skills (when to run Bicameral in a repo, which ADRs to
read, contribution policy, factory attestation) are a separate concept and are
not owned by MCP.
"""

from __future__ import annotations

from .base import (
    AdapterActionResult,
    AdapterStatus,
    HostAdapter,
    HostCapability,
    HostConfigError,
    PackageProvenance,
)
from .claude import ClaudeCodeAdapter
from .codex import CodexAdapter
from .context import BoundedContextDescriptor, PreworkContext
from .evidence import (
    HostActivationResult,
    HostEvidenceError,
    evaluate_host_activation,
)
from .registry import get_adapter, supported_hosts
from .runner import PreworkOutcome, PreworkResult, run_prework
from .state import AdapterState

__all__ = [
    "AdapterActionResult",
    "AdapterState",
    "AdapterStatus",
    "BoundedContextDescriptor",
    "ClaudeCodeAdapter",
    "CodexAdapter",
    "HostAdapter",
    "HostActivationResult",
    "HostCapability",
    "HostConfigError",
    "HostEvidenceError",
    "PackageProvenance",
    "PreworkContext",
    "PreworkOutcome",
    "PreworkResult",
    "get_adapter",
    "evaluate_host_activation",
    "run_prework",
    "supported_hosts",
]
