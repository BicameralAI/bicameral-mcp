"""Deterministic v1 implementation of CodeGenomeAdapter.

No LLM, no embeddings. Reuses the existing tree-sitter + git stack.
Phase 1+2 (#59) implements only ``compute_identity``; the other adapter
methods inherit ``NotImplementedError`` from the ABC until Phase 3+.

Identity model (deterministic_location_v1):
    structural_signature = f"{file_path}:{start_line}:{end_line}"
    signature_hash       = blake2b(structural_signature, digest_size=32)
    address              = f"cg:{signature_hash}"
    content_hash         = ledger.status.hash_lines(body, s, e)
                           — sha256 with whitespace-normalized lines, the
                           same hash function used by code_region. By
                           construction, subject_identity.content_hash and
                           code_region.content_hash are byte-identical at
                           bind time (#59 exit criterion).
    confidence           = 0.65   (location-only fingerprint)
    identity_type        = "deterministic_location_v1"
    model_version        = "deterministic-location-v1"
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from .adapter import CodeGenomeAdapter, SubjectIdentity

IDENTITY_TYPE_V1 = "deterministic_location_v1"
MODEL_VERSION_V1 = "deterministic-location-v1"
DEFAULT_CONFIDENCE_V1 = 0.65


class DeterministicCodeGenomeAdapter(CodeGenomeAdapter):
    """Location-based identity, no semantic reasoning."""

    def __init__(self, repo_path: str | Path):
        self.repo_path = str(repo_path)

    def compute_identity(
        self,
        file_path: str,
        start_line: int,
        end_line: int,
        repo_ref: str = "HEAD",
    ) -> SubjectIdentity:
        # Lazy import so the codegenome package stays importable in test
        # environments that stub the git layer.
        from ledger.status import get_git_content, hash_lines

        structural_signature = f"{file_path}:{start_line}:{end_line}"
        signature_hash = hashlib.blake2b(
            structural_signature.encode("utf-8"),
            digest_size=32,
        ).hexdigest()
        address = f"cg:{signature_hash}"

        content = get_git_content(
            file_path, start_line, end_line, self.repo_path, ref=repo_ref,
        )
        if content is None or start_line < 1 or end_line < start_line:
            content_hash: str | None = None
        else:
            content_hash = hash_lines(content, start_line, end_line)

        return SubjectIdentity(
            address=address,
            identity_type=IDENTITY_TYPE_V1,
            structural_signature=structural_signature,
            behavioral_signature=None,
            signature_hash=signature_hash,
            content_hash=content_hash,
            confidence=DEFAULT_CONFIDENCE_V1,
            model_version=MODEL_VERSION_V1,
        )
