"""Single-use, scoped approval gate for request_correction submissions.

MCP must not submit a correction request to the bot daemon without explicit,
scoped user approval. Each approval is:
  - Scoped to a specific packet item, excerpt, diff, or correction request.
  - Single-use: consumed on the first successful submission attempt.
  - Locally enforced: rejection happens before any network call to the daemon.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ApprovalScope:
    """Identifies what the user approved for correction submission.

    At least one of packet_id, excerpt, diff, or correction_request must be
    present so the approval is meaningfully scoped.
    """

    packet_id: str | None = None
    excerpt: str | None = None
    diff: str | None = None
    correction_request: str | None = None

    def __post_init__(self) -> None:
        if not any((self.packet_id, self.excerpt, self.diff, self.correction_request)):
            raise ValueError(
                "ApprovalScope requires at least one of: "
                "packet_id, excerpt, diff, correction_request"
            )

    @property
    def key(self) -> str:
        """Content-addressed key for deduplication and lookup."""
        canonical = json.dumps(
            {
                "packet_id": self.packet_id,
                "excerpt": self.excerpt,
                "diff": self.diff,
                "correction_request": self.correction_request,
            },
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode()).hexdigest()

    def description(self) -> str:
        """Human-readable description of what this approval covers."""
        parts: list[str] = []
        if self.packet_id:
            parts.append(f"packet_id={self.packet_id}")
        if self.excerpt:
            parts.append(f"excerpt={self.excerpt!r}")
        if self.diff:
            parts.append(f"diff={self.diff!r}")
        if self.correction_request:
            parts.append(f"correction_request={self.correction_request!r}")
        return ", ".join(parts)


@dataclass
class ApprovalGate:
    """In-process approval state for request_correction submissions.

    Thread-safety is not required: MCP runs single-threaded async.
    """

    _pending: dict[str, ApprovalScope] = field(default_factory=dict)

    def grant(self, scope: ApprovalScope) -> str:
        """Record a single-use approval for the given scope. Returns the scope key."""
        key = scope.key
        self._pending[key] = scope
        return key

    def consume(self, scope: ApprovalScope) -> bool:
        """Consume and remove a pending approval. Returns True if consumed."""
        key = scope.key
        if key in self._pending:
            del self._pending[key]
            return True
        return False

    def has_approval(self, scope: ApprovalScope) -> bool:
        """Check whether a pending approval exists for this scope."""
        return scope.key in self._pending

    def pending_count(self) -> int:
        """Number of unconsumed approvals."""
        return len(self._pending)

    def clear(self) -> None:
        """Revoke all pending approvals."""
        self._pending.clear()


def scope_from_params(params: dict[str, Any]) -> ApprovalScope:
    """Build an ApprovalScope from MCP tool call parameters."""
    return ApprovalScope(
        packet_id=params.get("packet_id"),
        excerpt=params.get("excerpt"),
        diff=params.get("diff"),
        correction_request=params.get("correction_request"),
    )
