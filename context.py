"""Request-scoped snapshot pinning CodeGraph and Ledger to the same git ref."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


_TESTER_MODE_TRUTHY = frozenset({"1", "true", "yes", "on"})


@dataclass(frozen=True)
class BicameralContext:
    """Created once per MCP tool call. All services see the same commit."""

    repo_path: str
    head_sha: str
    ledger: object
    code_graph: object
    drift_analyzer: object
    authoritative_ref: str = "main"
    authoritative_sha: str = ""
    # v0.4.9 (Phase 2): tester mode enables blocking action_hints on
    # search/brief responses so agents must address drifted decisions,
    # unresolved open questions, and divergences before making code
    # changes. Off by default — opt-in via BICAMERAL_TESTER_MODE env var.
    tester_mode: bool = False
    # v0.4.8: mutable cache for within-call sync dedup. Frozen-dataclass-safe
    # because the reference stays pinned; only the dict's contents mutate.
    # Keys: ``last_sync_sha`` (str). Cleared by any handler that mutates
    # repo-state expectations before chaining downstream tools.
    _sync_state: dict = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> BicameralContext:
        from adapters.code_locator import get_code_locator
        from adapters.ledger import get_drift_analyzer, get_ledger
        from code_locator_runtime import detect_authoritative_ref, get_repo_index_state, resolve_ref_sha

        repo_path = os.getenv("REPO_PATH", ".")
        state = get_repo_index_state(repo_path)
        authoritative_ref = detect_authoritative_ref(repo_path)
        authoritative_sha = resolve_ref_sha(repo_path, authoritative_ref) or ""
        tester_mode = (
            os.getenv("BICAMERAL_TESTER_MODE", "").strip().lower()
            in _TESTER_MODE_TRUTHY
        )

        return cls(
            repo_path=repo_path,
            head_sha=state.head_commit,
            ledger=get_ledger(),
            code_graph=get_code_locator(),
            drift_analyzer=get_drift_analyzer(),
            authoritative_ref=authoritative_ref,
            authoritative_sha=authoritative_sha,
            tester_mode=tester_mode,
        )
