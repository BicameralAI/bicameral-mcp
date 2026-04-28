"""Pydantic contracts for the MCP-boundary objects per upstream issue #59.

Three models exactly as the issue specifies — no symmetric mirror of
``SubjectIdentity`` (which has no caller in #59; future phases that
need MCP serialization can introduce its mirror at point-of-need).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SubjectCandidateModel(BaseModel):
    address: str
    file_path: str
    start_line: int
    end_line: int
    symbol_name: str | None = None
    symbol_kind: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str


class EvidenceRecordModel(BaseModel):
    evidence_type: str
    source_ref: str
    summary: str
    confidence: float = Field(ge=0.0, le=1.0)
    payload: dict[str, Any] = Field(default_factory=dict)


class EvidencePacketModel(BaseModel):
    packet_id: str
    query_text: str
    repo_ref: str
    subjects: list[SubjectCandidateModel]
    supporting_evidence: list[EvidenceRecordModel]
    contradicting_evidence: list[EvidenceRecordModel]
    uncertainties: list[str]
    confidence_summary: dict[str, float]
