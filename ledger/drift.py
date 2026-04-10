"""HashDriftAnalyzer — Layer 1 drift detection via content hash comparison.

Wraps the existing status.py functions (resolve_symbol_lines, compute_content_hash,
derive_status) into the DriftAnalyzerPort interface.

This is the baseline implementation. Future implementations:
  - SemanticDriftAnalyzer: L1 → L2 (AST pre-filter) → L3 (LLM compliance)
  - CodeGenomeDriftAnalyzer: CodeGenome overlay confidence fusion
"""

from __future__ import annotations

from ports import DriftResult
from .status import compute_content_hash, derive_status, resolve_symbol_lines


class HashDriftAnalyzer:
    """Layer 1 only — pure hash comparison, no AST or LLM.

    Always returns confidence=1.0 and explanation="".
    source_context is accepted but ignored (plumbing for L3).
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
        source_context: str = "",
    ) -> DriftResult:
        # Try symbol-name resolution first (survives line shifts + renames)
        resolved = resolve_symbol_lines(file_path, symbol_name, repo_path, ref=ref)
        if resolved:
            start_line, end_line = resolved

        # Compute actual hash at this ref
        actual_hash = compute_content_hash(
            file_path, start_line, end_line, repo_path, ref=ref
        )

        status = derive_status(stored_hash, actual_hash)
        new_hash = actual_hash or stored_hash

        return DriftResult(
            status=status,
            content_hash=new_hash,
            confidence=1.0,
            explanation="",
        )
