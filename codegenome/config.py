"""CodeGenome feature flags. All flags default to ``False``.

Phase 1+2 (#59) adds the foundation but must not change any existing
MCP behavior unless ``write_identity_records`` is explicitly enabled.

Loaded from ``BICAMERAL_CODEGENOME_*`` environment variables so the same
build can run with the feature on or off without rebuilds.
"""

from __future__ import annotations

import os

from pydantic import BaseModel

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in _TRUTHY


class CodeGenomeConfig(BaseModel):
    """Conservative posture: every flag defaults off."""

    enabled: bool = False
    write_identity_records: bool = False
    enhance_drift: bool = False
    enhance_search: bool = False
    expose_evidence_packets: bool = False
    chamber_evaluations: bool = False
    benchmark_mode: bool = False

    @classmethod
    def from_env(cls) -> CodeGenomeConfig:
        return cls(
            enabled=_flag("BICAMERAL_CODEGENOME_ENABLED"),
            write_identity_records=_flag("BICAMERAL_CODEGENOME_WRITE_IDENTITY_RECORDS"),
            enhance_drift=_flag("BICAMERAL_CODEGENOME_ENHANCE_DRIFT"),
            enhance_search=_flag("BICAMERAL_CODEGENOME_ENHANCE_SEARCH"),
            expose_evidence_packets=_flag("BICAMERAL_CODEGENOME_EXPOSE_EVIDENCE_PACKETS"),
            chamber_evaluations=_flag("BICAMERAL_CODEGENOME_CHAMBER_EVALUATIONS"),
            benchmark_mode=_flag("BICAMERAL_CODEGENOME_BENCHMARK_MODE"),
        )

    def identity_writes_active(self) -> bool:
        """True iff bind-time identity writes should fire."""
        return self.enabled and self.write_identity_records
