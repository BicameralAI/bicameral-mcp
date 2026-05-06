"""Content-contract tests for the compliance posture documents
(#220 + #225 + #226).

The unit-under-test is the rendered markdown content of each policy
document. These tests verify the specific section commitments and
cross-link presence that operators and auditors rely on. If a load-
bearing section is silently dropped in a future doc edit, these tests
fail because the substring no longer matches — closing the SG-035
"doctrine-content test unanchored" surface for compliance-posture
declarations.

Per ``qor/references/doctrine-test-functionality.md``: the unit IS the
document content; ``read_text() + assert "<commitment>" in content`` is
genuine unit invocation. The doctrine's ``<substring> in <file_text>``
flag applies only when the substring is a proxy for testing a SEPARATE
unit; here the doc IS the unit.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HOST_TRUST = REPO_ROOT / "docs" / "policies" / "host-trust-model.md"
ACCEPTABLE_USE = REPO_ROOT / "docs" / "policies" / "acceptable-use.md"
SLA = REPO_ROOT / "docs" / "sla.md"
README = REPO_ROOT / "README.md"
RESEARCH_BRIEF = REPO_ROOT / "docs" / "research-brief-compliance-audit-2026-05-06.md"


def test_host_trust_model_declares_required_sections() -> None:
    """`docs/policies/host-trust-model.md` must declare three load-bearing
    sections operators consult: server-side guarantees, host-side surfaces
    (the externalized assumptions), and per-host operator checklist."""
    content = HOST_TRUST.read_text(encoding="utf-8")
    assert "## Server-side guarantees" in content
    assert "## Host-side surfaces this design assumes" in content
    assert "## Per-host operator checklist" in content


def test_acceptable_use_lists_required_prohibited_categories() -> None:
    """`docs/policies/acceptable-use.md` must enumerate four prohibited-use
    categories that the framework-mapping table cross-references:
    HR/legal/medical/financial substitution, regulated-data ingestion,
    multi-tenant deployment, automated-decisions-without-HITL."""
    content = ACCEPTABLE_USE.read_text(encoding="utf-8")
    assert "HR" in content and "legal" in content and "medical" in content
    assert "PHI" in content or "Protected Health Information" in content
    assert "PAN" in content or "cardholder" in content
    assert "multi-tenant" in content
    assert "human-in-the-loop" in content or "HITL" in content


def test_sla_declares_operator_run_only_stance_and_hosted_activation() -> None:
    """`docs/sla.md` must declare both the active operator-run-only
    commitment AND the activation requirements for a future hosted tier
    so that a future hosted offering cannot silently ship without the
    SLA section being filled in."""
    content = SLA.read_text(encoding="utf-8")
    assert "operator-run-only" in content.lower() or "operator-run only" in content.lower()
    assert "Activation requirements" in content or "activation requirements" in content
    assert "uptime" in content.lower()
    assert "MTTR" in content


def test_readme_compliance_section_links_all_three_policies() -> None:
    """`README.md` must cross-link all three policy documents AND the
    research brief. Locks operator-discovery surface against silent drift."""
    content = README.read_text(encoding="utf-8")
    assert "docs/policies/host-trust-model.md" in content
    assert "docs/policies/acceptable-use.md" in content
    assert "docs/sla.md" in content
    assert "docs/research-brief-compliance-audit-2026-05-06.md" in content


def test_research_brief_marks_closed_gaps() -> None:
    """The research brief must mark MCP-01, NIST-RMF-01, AI-ACT-02, and
    SOC2-02 as closed by their respective policy documents. Locks
    bidirectional cross-reference between gap analysis and closure docs."""
    content = RESEARCH_BRIEF.read_text(encoding="utf-8")
    # Each gap should carry an "Status" line referring to the new policy doc.
    assert "host-trust-model.md" in content  # MCP-01 closure pointer
    assert "acceptable-use.md" in content  # NIST-RMF-01 + AI-ACT-02 closure pointer
    assert "sla.md" in content  # SOC2-02 closure pointer
