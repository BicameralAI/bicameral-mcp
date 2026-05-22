"""Pydantic wire payloads + adapter Protocol classes.

`PROTOCOL_VERSION` is semver. Minor bumps are additive (new optional fields,
new methods); major bumps break wire compatibility and require coordinated
daemon + adapter release. Every grounding-related request carries
`(repo_id, ref)` because the daemon is user-scoped and repo-separated under
`~/.bicameral/projects/<repo_id>/`.
"""

from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

PROTOCOL_VERSION = "0.1.0"

BATCH_REGION_LIMIT = 1000


class ProtocolError(Exception):
    """Wire-level protocol failure (malformed frame, unknown method)."""


class ProtocolVersionError(ProtocolError):
    """Raised when client and server disagree on major version."""


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


# ── Adapter Protocols ──────────────────────────────────────────────────


@runtime_checkable
class IngestAdapter(Protocol):
    """Pulls intent + artifact events into the ledger."""

    name: str

    async def ingest(self, req: IngestRequest) -> IngestResult: ...

    async def link_commit(self, req: LinkCommitRequest) -> LinkCommitResult: ...


@runtime_checkable
class EgressAdapter(Protocol):
    """Delivers notification events to humans where they live."""

    name: str

    async def deliver(self, event: NotificationEvent) -> DeliveryResult: ...


@runtime_checkable
class GroundingPort(Protocol):
    """Resolves symbols + analyzes drift against a `(repo_id, ref)` pair."""

    async def validate_symbols(
        self, req: ValidateSymbolsRequest
    ) -> list[Symbol]: ...

    async def extract_symbols(
        self, req: ExtractSymbolsRequest
    ) -> list[Symbol]: ...

    async def get_neighbors(
        self, req: GetNeighborsRequest
    ) -> list[Neighbor]: ...

    async def analyze_region(
        self, req: AnalyzeRegionRequest
    ) -> DriftResult: ...

    async def batch_analyze_regions(
        self, req: BatchAnalyzeRequest
    ) -> list[DriftResult]: ...
