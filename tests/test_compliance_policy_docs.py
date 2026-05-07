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
AUDIT_LOG_POLICY = REPO_ROOT / "docs" / "policies" / "audit-log.md"
DIAGNOSE_OUTPUT_POLICY = REPO_ROOT / "docs" / "policies" / "diagnose-output.md"
LEDGER_EXPORT_POLICY = REPO_ROOT / "docs" / "policies" / "ledger-export.md"


def test_host_trust_model_declares_required_sections() -> None:
    """`docs/policies/host-trust-model.md` must declare three load-bearing
    sections operators consult: server-side guarantees, host-side surfaces
    (the externalized assumptions), and per-host operator checklist."""
    content = HOST_TRUST.read_text(encoding="utf-8")
    assert "## Server-side guarantees" in content
    assert "## Host-side surfaces this design assumes" in content
    assert "## Per-host operator checklist" in content


def test_host_trust_model_includes_skills_manifest_row() -> None:
    """#218 LLM-06: the Server-side guarantees table must list the
    skills-manifest signature verification gate alongside the existing
    hooks-manifest verification gate. Locks the bidirectional
    cross-reference between the policy doc and the verifier surface."""
    content = HOST_TRUST.read_text(encoding="utf-8")
    assert "Skills-manifest signature verification" in content
    assert "_install_skills" in content
    assert "skills-manifest.toml" in content
    assert "LLM-06" in content


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


def test_audit_log_policy_doc_includes_channel_resolution_table() -> None:
    """#227 SOC2-06 + OWASP-06: the audit-log policy doc must include the
    operator-facing channel-resolution table. Locks the bidirectional
    cross-reference between the policy doc and the audit_log.py module's
    `_resolve_channel` semantics — if the channel resolution behavior
    changes silently, the doc-as-unit assertion catches the drift.
    """
    content = AUDIT_LOG_POLICY.read_text(encoding="utf-8")
    assert "## Channel resolution" in content
    assert "BICAMERAL_AUDIT_LOG" in content
    assert "BICAMERAL_AUDIT_LOG_LEVEL" in content
    assert "stderr" in content
    assert "disabled" in content
    assert "unwriteable" in content
    assert "SOC2-06" in content
    assert "OWASP-06" in content


def test_audit_log_policy_doc_documents_event_taxonomy() -> None:
    """The policy doc must enumerate every `AuditEventType` enum value so
    operators reading the doc see the closed event taxonomy. Locks
    drift between the enum and the operator-facing surface."""
    from audit_log import AuditEventType

    content = AUDIT_LOG_POLICY.read_text(encoding="utf-8")
    for event in AuditEventType:
        assert event.value in content, f"event_type {event.value!r} missing from policy doc"


def test_diagnose_output_policy_doc_lists_allowlisted_fields() -> None:
    """#252 Layer 3: every Diagnosis dataclass field must appear in
    `docs/policies/diagnose-output.md`. Locks doc/code drift between
    the `_ALLOWED_FIELDS` privacy-allowlist and the operator-facing
    policy doc; if a future field is added without updating the doc,
    this test fails."""
    from cli.diagnose import _ALLOWED_FIELDS

    content = DIAGNOSE_OUTPUT_POLICY.read_text(encoding="utf-8")
    for field in _ALLOWED_FIELDS:
        assert field in content, f"allowlisted field {field!r} missing from policy doc"


def test_diagnose_output_policy_doc_documents_suggestion_heuristics() -> None:
    """#252 Layer 3: the policy doc must enumerate the 5 suggestion
    heuristics by name so operators understand what triggers each
    recommendation. Locks the suggestion-engine catalog against drift."""
    content = DIAGNOSE_OUTPUT_POLICY.read_text(encoding="utf-8")
    for heuristic in (
        "drift detected",
        "recommended-version mismatch",
        "audit log disabled",
        "ledger > 100 MiB",
        "schema version old",
    ):
        assert heuristic in content, f"heuristic {heuristic!r} missing from policy doc"


def test_ledger_export_policy_doc_lists_canonical_record_fields() -> None:
    """#252 Layer 4: every canonical record-shape field must appear in the policy doc.
    Locks doc/code drift between the export-record format and the operator-facing
    documentation."""
    content = LEDGER_EXPORT_POLICY.read_text(encoding="utf-8")
    for field in ("_table", "_schema_version", "_record_version", "id", "created_at", "in", "out"):
        assert field in content, f"canonical-record field {field!r} missing from policy doc"


def test_ledger_export_policy_doc_documents_two_pass_import_and_gdpr_use_cases() -> None:
    """#252 Layer 4: policy doc must enumerate the two-pass import flow + GDPR
    workflow recipes. Locks the use-case catalog against drift."""
    content = LEDGER_EXPORT_POLICY.read_text(encoding="utf-8")
    for marker in (
        "Pass A — data records",
        "Pass B — edge records",
        "Art. 15",
        "Art. 17",
        "right-to-erasure",
        "migration vehicle",
    ):
        assert marker in content, f"marker {marker!r} missing from policy doc"
