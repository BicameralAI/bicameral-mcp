"""Phase 1+2 unit tests — codegenome.adapter ABC + dataclasses + deterministic adapter."""

from __future__ import annotations

import dataclasses
import hashlib
from unittest.mock import patch

import pytest

from codegenome.adapter import (
    CodeGenomeAdapter,
    EvidencePacket,
    EvidenceRecord,
    SubjectCandidate,
    SubjectIdentity,
)
from codegenome.deterministic_adapter import (
    DEFAULT_CONFIDENCE_V1,
    IDENTITY_TYPE_V1,
    MODEL_VERSION_V1,
    DeterministicCodeGenomeAdapter,
)

# ── Phase 1: ABC + dataclasses ──────────────────────────────────────────────


def test_base_adapter_resolve_subjects_raises():
    with pytest.raises(NotImplementedError):
        CodeGenomeAdapter().resolve_subjects("query")


def test_base_adapter_compute_identity_raises():
    with pytest.raises(NotImplementedError):
        CodeGenomeAdapter().compute_identity("a.py", 1, 5)


def test_base_adapter_evaluate_drift_raises():
    with pytest.raises(NotImplementedError):
        CodeGenomeAdapter().evaluate_drift("decision:1")


def test_base_adapter_build_evidence_packet_raises():
    with pytest.raises(NotImplementedError):
        CodeGenomeAdapter().build_evidence_packet("query")


def test_subject_identity_is_frozen():
    identity = SubjectIdentity(
        address="cg:abc",
        identity_type=IDENTITY_TYPE_V1,
        structural_signature="a.py:1:5",
        behavioral_signature=None,
        signature_hash="abc",
        content_hash="def",
        confidence=0.65,
        model_version=MODEL_VERSION_V1,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        identity.confidence = 0.99  # type: ignore[misc]


def test_subject_candidate_dataclass_fields():
    candidate = SubjectCandidate(
        address="cg:abc",
        file_path="a.py",
        start_line=1,
        end_line=5,
        symbol_name="foo",
        symbol_kind="function",
        confidence=0.8,
        reason="bm25 match",
    )
    assert candidate.address == "cg:abc"
    assert candidate.confidence == 0.8


def test_evidence_packet_holds_subjects_and_evidence():
    packet = EvidencePacket(
        packet_id="ep_1",
        query_text="test",
        repo_ref="HEAD",
        subjects=[],
        supporting_evidence=[
            EvidenceRecord(
                evidence_type="code",
                source_ref="src/x.py:1-10",
                summary="x",
                confidence=0.7,
            ),
        ],
        contradicting_evidence=[],
        uncertainties=[],
        confidence_summary={"overall": 0.7},
    )
    assert len(packet.supporting_evidence) == 1
    assert packet.confidence_summary["overall"] == 0.7


# ── Phase 2: DeterministicCodeGenomeAdapter.compute_identity ────────────────


def _stub_git_content(text):
    return patch("ledger.status.get_git_content", return_value=text)


def test_compute_identity_deterministic_address():
    adapter = DeterministicCodeGenomeAdapter(repo_path="/tmp/r")
    body = "def foo():\n    return 1\n"
    with _stub_git_content(body):
        a = adapter.compute_identity("src/foo.py", 1, 2)
        b = adapter.compute_identity("src/foo.py", 1, 2)
    assert a.address == b.address
    assert a.signature_hash == b.signature_hash
    assert a.address.startswith("cg:")


def test_compute_identity_different_span_different_address():
    adapter = DeterministicCodeGenomeAdapter(repo_path="/tmp/r")
    with _stub_git_content("line1\nline2\nline3\n"):
        a = adapter.compute_identity("src/foo.py", 1, 2)
        b = adapter.compute_identity("src/foo.py", 1, 3)
    assert a.address != b.address
    assert a.signature_hash != b.signature_hash


def test_compute_identity_different_file_different_address():
    adapter = DeterministicCodeGenomeAdapter(repo_path="/tmp/r")
    with _stub_git_content("x"):
        a = adapter.compute_identity("src/foo.py", 1, 2)
        b = adapter.compute_identity("src/bar.py", 1, 2)
    assert a.address != b.address


def test_compute_identity_signature_hash_is_blake2b():
    adapter = DeterministicCodeGenomeAdapter(repo_path="/tmp/r")
    structural = "src/foo.py:10:20"
    expected = hashlib.blake2b(structural.encode("utf-8"), digest_size=32).hexdigest()
    with _stub_git_content("body"):
        identity = adapter.compute_identity("src/foo.py", 10, 20)
    assert identity.signature_hash == expected
    assert identity.address == f"cg:{expected}"
    assert identity.structural_signature == structural


def test_compute_identity_content_hash_matches_ledger_hash_lines():
    """#59 exit criterion: identity.content_hash == ledger.status.hash_lines(body, s, e)."""
    from ledger.status import hash_lines

    adapter = DeterministicCodeGenomeAdapter(repo_path="/tmp/r")
    body = "def foo():\n    return 1\n    \n"
    expected = hash_lines(body, 1, 3)
    with _stub_git_content(body):
        identity = adapter.compute_identity("src/foo.py", 1, 3)
    assert identity.content_hash == expected


def test_compute_identity_returns_constants_for_deterministic_v1():
    adapter = DeterministicCodeGenomeAdapter(repo_path="/tmp/r")
    with _stub_git_content("body"):
        identity = adapter.compute_identity("a.py", 1, 5)
    assert identity.identity_type == IDENTITY_TYPE_V1
    assert identity.model_version == MODEL_VERSION_V1
    assert identity.confidence == DEFAULT_CONFIDENCE_V1
    assert identity.behavioral_signature is None


def test_compute_identity_missing_file_returns_none_content_hash():
    adapter = DeterministicCodeGenomeAdapter(repo_path="/tmp/r")
    with patch("ledger.status.get_git_content", return_value=None):
        identity = adapter.compute_identity("missing.py", 1, 5)
    assert identity.content_hash is None
    assert identity.address.startswith("cg:")
    assert identity.signature_hash is not None


def test_compute_identity_invalid_range_returns_none_content_hash():
    adapter = DeterministicCodeGenomeAdapter(repo_path="/tmp/r")
    with _stub_git_content("body"):
        identity = adapter.compute_identity("a.py", 5, 1)
    assert identity.content_hash is None
    assert identity.address.startswith("cg:")


# ── Phase 3 (#60): compute_identity_with_neighbors ──────────────────────────


class _StubLocator:
    """Minimal code_locator stub returning fixed neighbor addresses."""

    def __init__(self, neighbor_addresses):
        self._neighbor_addresses = tuple(neighbor_addresses)

    def neighbors_for(self, file_path, start_line, end_line):
        return self._neighbor_addresses


def test_compute_identity_with_neighbors_populates_field():
    """When code_locator supplies neighbors, identity carries them as a tuple."""
    adapter = DeterministicCodeGenomeAdapter(repo_path="/tmp/r")
    locator = _StubLocator(["cg:foo", "cg:bar"])
    with _stub_git_content("def f(): pass\n"):
        identity = adapter.compute_identity_with_neighbors(
            "src/foo.py",
            1,
            1,
            code_locator=locator,
        )
    assert identity.neighbors_at_bind == ("cg:bar", "cg:foo")  # sorted


def test_compute_identity_with_neighbors_falls_back_to_empty_tuple_on_none_locator():
    """No locator → no neighbors; field is empty tuple, not None."""
    adapter = DeterministicCodeGenomeAdapter(repo_path="/tmp/r")
    with _stub_git_content("def f(): pass\n"):
        identity = adapter.compute_identity_with_neighbors(
            "src/foo.py",
            1,
            1,
            code_locator=None,
        )
    assert identity.neighbors_at_bind == ()


def test_compute_identity_with_neighbors_locator_returning_empty_yields_empty_tuple():
    adapter = DeterministicCodeGenomeAdapter(repo_path="/tmp/r")
    locator = _StubLocator([])
    with _stub_git_content("body"):
        identity = adapter.compute_identity_with_neighbors(
            "src/foo.py",
            1,
            5,
            code_locator=locator,
        )
    assert identity.neighbors_at_bind == ()


def test_compute_identity_signature_unchanged_for_existing_callers():
    """Existing compute_identity contract must not change (Phase 1+2 callers)."""
    adapter = DeterministicCodeGenomeAdapter(repo_path="/tmp/r")
    with _stub_git_content("body"):
        identity = adapter.compute_identity("a.py", 1, 5)
    assert identity.neighbors_at_bind is None  # never set by the v1 path


def test_compute_identity_with_neighbors_sorted_for_stable_jaccard():
    """Neighbor list must be sorted so equal sets compare equal regardless of input order."""
    adapter = DeterministicCodeGenomeAdapter(repo_path="/tmp/r")
    a = _StubLocator(["cg:a", "cg:b", "cg:c"])
    b = _StubLocator(["cg:c", "cg:b", "cg:a"])
    with _stub_git_content("body"):
        ia = adapter.compute_identity_with_neighbors("x.py", 1, 5, code_locator=a)
        ib = adapter.compute_identity_with_neighbors("x.py", 1, 5, code_locator=b)
    assert ia.neighbors_at_bind == ib.neighbors_at_bind
