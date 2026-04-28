"""CodeGenome adapter factory — parallels adapters/ledger and adapters/code_locator.

Returns a per-call ``DeterministicCodeGenomeAdapter`` bound to the
current ``REPO_PATH``. Phase 1+2 (#59) only ships the deterministic
implementation; future phases may swap in embedding-backed or
LLM-augmented adapters behind the same factory.
"""

from __future__ import annotations

import os

from codegenome.adapter import CodeGenomeAdapter
from codegenome.deterministic_adapter import DeterministicCodeGenomeAdapter


def get_codegenome() -> CodeGenomeAdapter:
    """Return the CodeGenome adapter for the current repo."""
    repo_path = os.getenv("REPO_PATH", ".")
    return DeterministicCodeGenomeAdapter(repo_path=repo_path)
