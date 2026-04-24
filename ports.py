"""Port interfaces for code intelligence and drift analysis.

Decouples handlers from concrete backends so that:
- Phase 1 (event-sourced collaboration) can proceed without touching drift internals
- CodeGenome can replace the code analysis backend post-Phase 1
- Drift intelligence (AST + LLM layers) can be developed independently

See docs/architecture/port-interfaces.md for the full design rationale.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


# ── Drift Analysis ──────────────────────────────────────────────────────


@dataclass
class DriftResult:
    """Result of analyzing whether a code region still matches its intent.

    Produced by DriftAnalyzerPort implementations at each layer:
      L1 (hash):     status + content_hash, confidence=1.0, explanation=""
      L2 (AST):      filters cosmetic changes → keeps reflected, confidence=1.0
      L3 (semantic):  LLM compliance check → variable confidence + explanation
    """

    status: str  # reflected | drifted | pending | ungrounded
    content_hash: str  # updated hash for ledger storage
    confidence: float = 1.0  # 1.0 for hash-only, variable for AST/LLM
    explanation: str = ""  # "" for L1/L2, human-readable for L3


@runtime_checkable
class DriftAnalyzerPort(Protocol):
    """Port for drift detection — determines if code still matches intent.

    Drift detection pipeline (3 layers, each progressively more expensive):

    ┌─────────────────────────────────────────────────────────────────┐
    │ Layer │ What                │ Cost     │ Status                  │
    │───────│─────────────────────│──────────│─────────────────────────│
    │  L1   │ Content hash compare│ O(1)     │ ✅ Implemented           │
    │  L2   │ AST structural diff │ O(parse) │ ⬜ Pending (collaborator)│
    │  L3   │ LLM semantic check  │ O(LLM)   │ ⬜ Pending (needs L2)    │
    └─────────────────────────────────────────────────────────────────┘

    L1 → hash differs?
       no  → reflected
       yes → L2 → structural change?
                no (whitespace only) → reflected
                yes → L3 → still implements decision?
                         yes → reflected (with explanation)
                         no  → drifted (with explanation)

    Implementations:
      HashDriftAnalyzer       — L1 only (current)
      SemanticDriftAnalyzer   — L1 → L2 → L3 (collaborator WIP)
      CodeGenomeDriftAnalyzer — CodeGenome overlays + confidence fusion (future)
    """

    async def analyze_region(
        self,
        file_path: str,
        symbol_name: str,
        start_line: int,
        end_line: int,
        stored_hash: str,
        repo_path: str,
        ref: str = "HEAD",
        source_context: str = "",  # source_span text — ignored by L1, used by L3
    ) -> DriftResult: ...


# ── Code Intelligence ───────────────────────────────────────────────────


@runtime_checkable
class CodeIntelligencePort(Protocol):
    """Port for symbol resolution and structural graph traversal.

    The server no longer performs BM25/vector code search — callers resolve
    code regions themselves (Grep/Read) and hand file paths to the server.
    This port exposes only deterministic primitives: symbol lookup, symbol
    extraction, and 1-hop graph traversal.
    """

    def validate_symbols(self, candidates: list[str]) -> list[dict]: ...

    async def extract_symbols(self, file_path: str) -> list[dict]: ...

    def get_neighbors(self, symbol_id: int) -> list[dict]: ...
