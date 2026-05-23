"""Pydantic wire payloads + adapter Protocol classes.

`PROTOCOL_VERSION` is semver. Minor bumps are additive (new optional fields,
new methods); major bumps break wire compatibility and require coordinated
daemon + adapter release. Every grounding-related request carries
`(repo_id, ref)` because the daemon is user-scoped and repo-separated under
`~/.bicameral/projects/<repo_id>/`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

PROTOCOL_VERSION = "0.1.0"

BATCH_REGION_LIMIT = 1000

# Default tenant identifier when no explicit one is supplied (local deploy).
LOCAL_TENANT_ID = "local"


class ProtocolError(Exception):
    """Wire-level protocol failure (malformed frame, unknown method)."""


class ProtocolVersionError(ProtocolError):
    """Raised when client and server disagree on major version."""


class NotAttachedError(ProtocolError):
    """Raised when a tenant-scoped RPC is issued before ``system.attach``."""


# ── Session attach ─────────────────────────────────────────────────────


class AttachRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: str
    user_id: str | None = None  # within-tenant actor identity (e.g., signer email)


class AttachResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    tenant_id: str
    protocol_version: str = PROTOCOL_VERSION


@dataclass
class ConnectionContext:
    """Per-connection state set by ``system.attach`` and threaded into
    every adapter call. Adapters use ``tenant_id`` for scoping; ``user_id``
    captures the within-tenant actor for ratification provenance.

    Defined here (not in server.py) so adapter Protocols can reference it
    without importing the server module.
    """

    tenant_id: str | None = None
    user_id: str | None = None
    state: dict[str, Any] = field(default_factory=dict)

    @property
    def attached(self) -> bool:
        return self.tenant_id is not None


# ── Ingest ──────────────────────────────────────────────────────────────


class IngestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    adapter_name: str
    payload: str
    source_id: str
    source_ref: str
    mode: Literal["active", "passive"] = "active"
    repo_id: str | None = None


class IngestResult(BaseModel):
    # Server responses tolerate forward-additive fields; inputs stay strict.
    model_config = ConfigDict(extra="ignore")
    status: Literal["accepted", "refused", "duplicate"]
    decision_ids: list[str] = Field(default_factory=list)
    reason: str | None = None


class LinkCommitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repo_id: str
    commit_sha: str
    ref: str = "HEAD"


class LinkCommitResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    status: Literal["linked", "no_change", "refused"]
    regions_updated: int = 0


# ── Egress ──────────────────────────────────────────────────────────────


class NotificationEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_type: str
    decision_id: str | None = None
    feature_area: str | None = None
    summary: str = Field(max_length=200)
    severity: Literal["info", "warn", "error"] = "info"
    source_ref: str | None = None


class DeliveryResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    status: Literal["delivered", "queued", "failed"]
    detail: str | None = None


# ── Grounding ──────────────────────────────────────────────────────────


class ValidateSymbolsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repo_id: str
    ref: str = "HEAD"
    candidates: list[str]


class Symbol(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    file: str
    start_line: int
    end_line: int


class ExtractSymbolsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repo_id: str
    ref: str = "HEAD"
    file_path: str


class GetNeighborsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repo_id: str
    ref: str = "HEAD"
    symbol_id: int


class Neighbor(BaseModel):
    model_config = ConfigDict(extra="ignore")
    symbol_id: int
    name: str
    relation: Literal["calls", "called_by", "imports", "imported_by"]


class CodeRegion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file: str
    symbol: str
    start_line: int
    end_line: int
    stored_hash: str = ""


class AnalyzeRegionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repo_id: str
    ref: str = "HEAD"
    region: CodeRegion
    source_context: str = ""


class DriftResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    status: Literal["reflected", "drifted", "pending", "ungrounded"]
    content_hash: str
    confidence: float = 1.0
    explanation: str = ""


class BatchAnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repo_id: str
    ref: str = "HEAD"
    regions: list[CodeRegion] = Field(max_length=BATCH_REGION_LIMIT)


# ── Reads ───────────────────────────────────────────────────────────────


class HistoryRequest(BaseModel):
    """Wire payload for ``read.history``.

    The daemon owns the enriched SurrealQL traversal + PII archive
    resolution + feature-area grouping. MCP only sends the filters.
    """

    model_config = ConfigDict(extra="forbid")
    repo_id: str
    ref: str = "HEAD"
    feature_filter: str | None = None
    include_superseded: bool = True
    include_pruned: bool = False
    as_of: str | None = None


class UsageSummaryRequest(BaseModel):
    """Wire payload for ``read.usage_summary``."""

    model_config = ConfigDict(extra="forbid")
    repo_id: str
    days: int = Field(default=7, ge=0, le=365)


class UsageSummaryResult(BaseModel):
    """Aggregate usage stats. Flat by design — no nested envelope."""

    model_config = ConfigDict(extra="ignore")
    period_days: int
    ingest_calls: int
    bind_calls_total: int
    decisions_ingested: int
    decisions_ungrounded: int
    decisions_pending: int
    decisions_reflected: int
    decisions_drifted: int
    reflected_pct: float
    drift_pct: float
    cosmetic_drift_pct: float
    error_rate: float


# ``HistoryResponse`` (the wire shape for ``read.history`` results) is owned by
# ``contracts.HistoryResponse`` — MCP and the daemon share the exact same model,
# so we don't redefine it here. The protocol method returns it verbatim per
# the "flat envelopes" constraint.


# ── Writes: telemetry-only (no ledger mutation) ────────────────────────


class FeedbackRequest(BaseModel):
    """Wire payload for ``write.feedback`` — agent self-reported friction.

    ``server_version`` is on the wire because the telemetry event represents
    MCP's behavior, not the daemon's; MCP supplies its own version so events
    are correctly attributed.
    """

    model_config = ConfigDict(extra="forbid")
    server_version: str
    skill: str = ""
    trying_to: str = ""
    attempted: str = ""
    stuck_on: str = ""


class FeedbackResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    recorded: bool


class SkillBeginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    skill_name: str


class SkillBeginResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    session_id: str
    skill: str
    status: Literal["started"]


class SkillEndRequest(BaseModel):
    """Wire payload for ``write.skill_end``.

    ``diagnostic`` is intentionally typed as ``dict[str, Any] | None`` — the
    per-skill Pydantic validation lives server-side in ``handle_skill_end``,
    which looks up the schema in ``SKILL_DIAGNOSTIC_MODELS`` and tolerates
    unknown fields (warns + strips). Re-modeling that validation here would
    duplicate the contract.
    """

    model_config = ConfigDict(extra="forbid")
    session_id: str
    skill_name: str
    server_version: str
    errored: bool = False
    error_class: str | None = None
    diagnostic: dict[str, Any] | None = None


class SkillEndResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    session_id: str
    skill: str
    duration_ms: int
    status: Literal["recorded"]
    diagnostic_warning: str | None = None


# ── Adapter Protocols ──────────────────────────────────────────────────


@runtime_checkable
class IngestAdapter(Protocol):
    """Pulls intent + artifact events into the ledger.

    Adapters receive a ``ConnectionContext`` so they can scope writes by
    tenant + attribute the actor for ratification provenance.
    """

    name: str

    async def ingest(self, req: IngestRequest, ctx: ConnectionContext) -> IngestResult: ...

    async def link_commit(
        self, req: LinkCommitRequest, ctx: ConnectionContext
    ) -> LinkCommitResult: ...


@runtime_checkable
class EgressAdapter(Protocol):
    """Delivers notification events to humans where they live."""

    name: str

    async def deliver(self, event: NotificationEvent, ctx: ConnectionContext) -> DeliveryResult: ...


@runtime_checkable
class GroundingPort(Protocol):
    """Resolves symbols + analyzes drift against a ``(tenant_id, repo_id, ref)``
    tuple. ``tenant_id`` comes from the ConnectionContext; the request itself
    carries ``repo_id`` and ``ref``.
    """

    async def validate_symbols(
        self, req: ValidateSymbolsRequest, ctx: ConnectionContext
    ) -> list[Symbol]: ...

    async def extract_symbols(
        self, req: ExtractSymbolsRequest, ctx: ConnectionContext
    ) -> list[Symbol]: ...

    async def get_neighbors(
        self, req: GetNeighborsRequest, ctx: ConnectionContext
    ) -> list[Neighbor]: ...

    async def analyze_region(
        self, req: AnalyzeRegionRequest, ctx: ConnectionContext
    ) -> DriftResult: ...

    async def batch_analyze_regions(
        self, req: BatchAnalyzeRequest, ctx: ConnectionContext
    ) -> list[DriftResult]: ...
