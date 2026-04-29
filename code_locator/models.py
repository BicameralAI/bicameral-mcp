"""Pydantic data models for Code Locator.

These models define the contract between all stages of the pipeline.
Every field is intentional — changes here affect the vocabulary bridge,
retrieval channels, RRF fusion, graph enrichment, and output format.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# ── Input (from Agent A: Transcript Extractor) ──────────────────────


class PlannedChange(BaseModel):
    """A planned code change extracted from a meeting transcript.

    Produced by Agent A (Transcript Extractor). Consumed by Code Locator
    as the starting point for code retrieval.
    """

    intent: str = Field(description="What the team wants to do")
    business_context: str = Field(
        default="", description="Why they want to do it (steelman interpretation)"
    )
    confidence: float = Field(
        default=1.0, ge=0.0, le=1.0, description="Agent A's confidence in the extraction"
    )
    discussion_participants: list[str] = Field(
        default_factory=list, description="Who was involved in the discussion"
    )


# ── Vocabulary Bridge ────────────────────────────────────────────────


class ValidatedSymbol(BaseModel):
    """A symbol validated against the real codebase index.

    The LLM proposes candidates; rapidfuzz validates them against the
    tree-sitter symbol index. Only validated symbols proceed to retrieval.
    """

    original_candidate: str = Field(description="What the LLM (or keyword extractor) proposed")
    matched_symbol: str = Field(description="The real symbol from the index that matched")
    match_score: float = Field(ge=0.0, le=100.0, description="rapidfuzz match score (0-100)")
    symbol_id: int | None = Field(default=None, description="SQLite row ID of the matched symbol")
    repo: str = Field(default="", description="Source repo for multi-repo support")
    bridge_method: str = Field(
        default="rapidfuzz_validate",
        description="How this symbol was found: keyword_extract, llm_propose, etc.",
    )


# ── Retrieval ────────────────────────────────────────────────────────


class RetrievalResult(BaseModel):
    """A single result from one retrieval channel.

    Produced by bm25s, tree-sitter graph, or cocoindex-code vector search.
    Fed into RRF fusion for cross-channel ranking.
    """

    file_path: str = Field(description="Path relative to repo root")
    line_number: int = Field(default=0, description="Line number (0 if file-level)")
    snippet: str = Field(default="", description="Code snippet around the match")
    score: float = Field(default=0.0, description="Channel-specific relevance score")
    method: str = Field(description="Which channel: bm25, graph, vector")
    repo: str = Field(default="", description="Source repo for multi-repo support")
    symbol_name: str = Field(default="", description="Matched symbol name if available")


# ── Provenance ───────────────────────────────────────────────────────


class Provenance(BaseModel):
    """Full provenance chain for a FoundComponent.

    Traces every claim back to its origin: which retrieval channels found it,
    what vocabulary bridge candidate led to it, and the fuzzy match score.
    Implements the 'selection not generation' principle — every output is auditable.
    """

    retrieval_channels: list[str] = Field(
        default_factory=list, description="Which channels contributed: bm25, graph, vector"
    )
    bridge_candidate: str = Field(
        default="", description="The original LLM/keyword candidate that led here"
    )
    bridge_match_score: float = Field(
        default=0.0, description="rapidfuzz score of the bridge match"
    )
    bridge_method: str = Field(default="", description="How the bridge candidate was generated")
    rrf_score: float = Field(default=0.0, description="Weighted RRF fusion score")


# ── Graph Enrichment ─────────────────────────────────────────────────


class NeighborInfo(BaseModel):
    """A structural neighbor discovered by 1-hop graph traversal."""

    symbol_name: str = Field(description="Qualified name of the neighbor")
    file_path: str = Field(description="Path relative to repo root")
    line_number: int = Field(default=0)
    edge_type: str = Field(description="Relationship: contains, imports, invokes, inherits")
    direction: str = Field(
        description="forward (this calls neighbor) or backward (neighbor calls this)"
    )


# ── Output (to Agent C: Evidence Gater) ──────────────────────────────


class FoundComponent(BaseModel):
    """A located code component with full provenance.

    Produced by Code Locator. Consumed by Agent C (Evidence Gater) to
    score evidence as SUFFICIENT / PARTIAL / NONE. Every field is
    grounded in real codebase data — no hallucinated paths.
    """

    repo: str = Field(default="", description="Source repo for multi-repo support")
    symbol: str = Field(description="Qualified symbol name")
    file: str = Field(description="file_path:line_number")
    confidence: float = Field(ge=0.0, le=1.0, description="Overall confidence score")
    provenance: Provenance = Field(default_factory=Provenance)
    neighbors: list[NeighborInfo] = Field(
        default_factory=list, description="1-hop structural neighbors"
    )
