"""Single-use, scoped approval gate for PII erasure requests.

MCP must not submit an erasure request to the bot daemon without explicit,
scoped user approval. Each approval is:
  - Scoped to a specific subject_id (and optional predicate).
  - Single-use: consumed on the first successful submission attempt.
  - Locally enforced: rejection happens before any network call to the daemon.
  - Fail-closed: no fallback on gate failure.

Implements GDPR Art.17 right-to-erasure approval surface.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ErasureScope:
    """Identifies what the user approved for erasure.

    subject_id is always required so the approval is meaningfully scoped.
    """

    subject_id: str
    predicate: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if not self.subject_id:
            raise ValueError("ErasureScope requires a non-empty subject_id")

    @property
    def key(self) -> str:
        """Content-addressed key for deduplication and lookup."""
        canonical = json.dumps(
            {
                "subject_id": self.subject_id,
                "predicate": self.predicate,
            },
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode()).hexdigest()

    def description(self) -> str:
        """Human-readable description of what this approval covers."""
        parts: list[str] = [f"subject_id={self.subject_id}"]
        if self.predicate:
            parts.append(f"predicate={self.predicate!r}")
        if self.reason:
            parts.append(f"reason={self.reason!r}")
        return ", ".join(parts)


@dataclass
class ErasureGate:
    """In-process approval state for privacy.erase_subject submissions.

    Thread-safety is not required: MCP runs single-threaded async.
    """

    _pending: dict[str, ErasureScope] = field(default_factory=dict)

    def grant(self, scope: ErasureScope) -> str:
        """Record a single-use approval for the given scope. Returns the scope key."""
        key = scope.key
        self._pending[key] = scope
        return key

    def consume(self, scope: ErasureScope) -> bool:
        """Consume and remove a pending approval. Returns True if consumed."""
        key = scope.key
        if key in self._pending:
            del self._pending[key]
            return True
        return False

    def has_approval(self, scope: ErasureScope) -> bool:
        """Check whether a pending approval exists for this scope."""
        return scope.key in self._pending

    def pending_count(self) -> int:
        """Number of unconsumed approvals."""
        return len(self._pending)

    def clear(self) -> None:
        """Revoke all pending approvals."""
        self._pending.clear()


def scope_from_params(params: dict[str, Any]) -> ErasureScope:
    """Build an ErasureScope from MCP tool call parameters."""
    subject_id = params.get("subject_id")
    if not subject_id:
        raise ValueError("subject_id is required for erasure scope")
    return ErasureScope(
        subject_id=subject_id,
        predicate=params.get("predicate"),
        reason=params.get("reason"),
    )
