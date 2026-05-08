"""Content-contract tests for `docs/policies/install-trust-model.md`
(#218 OWASP-03 + OWASP-05).

The unit-under-test is the rendered markdown content of the policy
document. These tests verify the install-time + update-time trust-model
commitments operators and auditors rely on. If a load-bearing section
is silently dropped in a future doc edit, these tests fail.

Per ``qor/references/doctrine-test-functionality.md`` and the locked
interpretation in Plan D's audit (round-1 PASS): the unit IS the doc
content; ``read_text() + assert "<commitment>" in content`` is genuine
unit invocation.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_TRUST = REPO_ROOT / "docs" / "policies" / "install-trust-model.md"
README = REPO_ROOT / "README.md"
RESEARCH_BRIEF = REPO_ROOT / "docs" / "research-brief-compliance-audit-2026-05-06.md"


def test_doc_declares_install_time_section() -> None:
    """Install-time section locks the OWASP-03 stance: no shipped
    lockfile; uv/pipx authority; pin-install path for reproducibility."""
    content = INSTALL_TRUST.read_text(encoding="utf-8")
    assert "Install-time" in content or "install-time" in content
    assert "uv tool install" in content
    assert "pipx install" in content
    assert "==" in content  # pin-install via ==<exact-version>


def test_doc_declares_update_time_section() -> None:
    """Update-time section locks the OWASP-05 stance: TLS-only active
    fetch + future-activation path + operator escape hatch."""
    content = INSTALL_TRUST.read_text(encoding="utf-8")
    assert "Update-time" in content or "update-time" in content
    assert "TLS" in content
    assert "RECOMMENDED_VERSION" in content
    assert "Future activation" in content or "future activation" in content
    assert "sigstore" in content.lower()


def test_doc_cross_references_related_policies() -> None:
    """Cross-references to SBOM (OWASP-01), RELEASE_EVIDENCE_PROCEDURE
    (SOC2-03), and sla.md (deployment model) — locks the bidirectional
    discoverability surface."""
    content = INSTALL_TRUST.read_text(encoding="utf-8")
    assert "RELEASE_EVIDENCE_PROCEDURE.md" in content
    assert "sla.md" in content
    assert "SBOM" in content


def test_readme_compliance_section_links_install_trust_model() -> None:
    """README's Compliance posture section must list the new policy doc
    alongside the three Plan D-shipped policies."""
    content = README.read_text(encoding="utf-8")
    assert "docs/policies/install-trust-model.md" in content
    # Sanity: the existing Plan D links must still be present.
    assert "docs/policies/host-trust-model.md" in content
    assert "docs/policies/acceptable-use.md" in content
    assert "docs/sla.md" in content


def test_research_brief_marks_owasp_03_and_owasp_05_closed() -> None:
    """The research brief must mark OWASP-03 and OWASP-05 as closed by
    the new trust-model doc — bidirectional cross-reference between
    gap analysis and closure document."""
    content = RESEARCH_BRIEF.read_text(encoding="utf-8")
    # Both gaps should carry a "Status (...)" closure-pointer line.
    assert "install-trust-model.md" in content
    # The closure-pointer pattern from Plan D / Plan C established
    # the "Status (YYYY-MM-DD): Closed by `<path>`" shape.
    assert "Status" in content and "Closed by" in content
