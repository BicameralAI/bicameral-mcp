"""Install-time verifier for ``skills/MANIFEST.toml`` (#218 LLM-06, #292).

Mirrors `release.manifest_verify` (#237 LLM-11) for the skills-content
surface. Loads the bundled manifest, verifies its keyless cosign
signature via ``_VERIFIER_HOOK`` (default: real ``sigstore-python``
verification), then cross-checks the SHA-256 of every skill file the
installer is about to copy against the verified manifest. Mismatch →
``SignatureError``.

Bypass posture: ``BICAMERAL_SKILLS_VERIFY_DISABLE=1`` swallows the
``SignatureError`` after writing a severity-3 ``verification_bypassed``
ledger event via ``EventFileWriter`` with ``manifest_kind: "skills"``
for disambiguation against the LLM-11 hooks-manifest bypass events.
Without the env var, the error propagates to the caller (fail-closed).

#292: the publish workflow signs the manifest with ``cosign sign-blob
--new-bundle-format --bundle`` and ships the single-file ``.sigstore``
bundle inside the wheel via hatch ``shared-data``. The verifier loads
that bundle with ``sigstore.models.Bundle.from_json`` and verifies it
with ``sigstore.verify.Verifier.production().verify_artifact()`` under
a composite GitHub-workflow identity policy.
"""

from __future__ import annotations

import hashlib
import os
import tomllib
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

_VERIFIER_FN = Callable[[Path, Path], None]  # (manifest_path, bundle_path)


class SignatureError(Exception):
    """Raised on any verification failure (bad sig, missing artifact,
    SHA-256 mismatch, malformed manifest)."""


def _sigstore_verify(manifest_path: Path, bundle_path: Path) -> None:
    """Default verifier: real sigstore-python keyless verification (#292).

    Loads the single-file Sigstore ``.sigstore`` bundle produced by the
    publish workflow's ``cosign sign-blob --new-bundle-format --bundle``
    step and verifies the manifest against it via
    ``Verifier.production().verify_artifact()``.

    The identity policy is composite — it binds the signing certificate
    to the ``Publish to PyPI`` workflow in ``BicameralAI/bicameral-mcp``,
    independent of the triggering release tag (``policy.Identity`` is
    exact-match, so a ``...@refs/tags/v*`` glob is not usable).

    Fail-closed: missing manifest, missing bundle, malformed bundle,
    tampered manifest bytes, or an identity mismatch all raise
    ``SignatureError``. Tests substitute ``_VERIFIER_HOOK`` to drive the
    wiring without an OIDC-signed bundle; see
    ``tests/test_skills_verify_sigstore.py``.
    """
    if not manifest_path.exists():
        raise SignatureError(f"manifest not found: {manifest_path}")
    if not bundle_path.exists():
        raise SignatureError(f"sigstore bundle not found: {bundle_path}")
    try:
        from sigstore.models import Bundle
        from sigstore.verify import Verifier, policy
    except ImportError as exc:
        raise SignatureError(
            f"sigstore-python not installed: {exc}. Install it (it is a "
            "first-class dependency) or set BICAMERAL_SKILLS_VERIFY_DISABLE=1 "
            "to bypass (writes a severity-3 verification_bypassed ledger event)."
        ) from exc
    try:
        verifier = Verifier.production()
        bundle = Bundle.from_json(bundle_path.read_bytes())
        verifier.verify_artifact(
            input_=manifest_path.read_bytes(),
            bundle=bundle,
            policy=policy.AllOf(
                [
                    policy.GitHubWorkflowRepository("BicameralAI/bicameral-mcp"),
                    policy.GitHubWorkflowName("Publish to PyPI"),
                ]
            ),
        )
    except SignatureError:
        raise
    except Exception as exc:
        raise SignatureError(f"sigstore verification failed: {exc}") from exc


_VERIFIER_HOOK: _VERIFIER_FN = _sigstore_verify


def verify_skills_manifest(
    manifest_path: Path,
    bundle_path: Path,
    expected_skills: dict[str, dict[str, str]],
) -> None:
    """Verify the manifest signature and cross-check per-file SHA-256.

    ``bundle_path`` is the single-file Sigstore ``.sigstore`` bundle that
    ships alongside the manifest in the wheel. ``expected_skills`` maps
    ``{skill_name: {filename: sha256_hex}}`` for every file the caller
    intends to copy. Every entry must appear in the verified manifest
    with matching SHA-256; any miss raises ``SignatureError``.
    """
    if not manifest_path.exists():
        raise SignatureError(f"manifest not found: {manifest_path}")
    _VERIFIER_HOOK(manifest_path, bundle_path)

    parsed = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_skills = parsed.get("skills", {})

    for skill_name, files in expected_skills.items():
        manifest_files = manifest_skills.get(skill_name)
        if manifest_files is None:
            raise SignatureError(f"skill {skill_name!r} absent from verified manifest")
        for filename, expected_sha in files.items():
            actual = manifest_files.get(filename)
            if actual is None:
                raise SignatureError(f"file {skill_name}/{filename} absent from verified manifest")
            if actual != expected_sha:
                raise SignatureError(
                    f"file {skill_name}/{filename} sha256 mismatch: "
                    f"manifest={actual!r} expected={expected_sha!r}"
                )


def _manifest_sha256(manifest_path: Path) -> str:
    if not manifest_path.exists():
        return "absent"
    return hashlib.sha256(manifest_path.read_bytes()).hexdigest()


def _get_event_writer():
    """Lazy EventFileWriter import — kept indirect so tests can monkeypatch
    cleanly without dragging the real writer into every call."""
    from events.writer import EventFileWriter

    return EventFileWriter()


def verify_skills_or_bypass(
    manifest_path: Path,
    bundle_path: Path,
    expected_skills: dict[str, dict[str, str]],
) -> None:
    """Verify the manifest; on failure, honor the bypass env var.

    Called by ``setup_wizard._install_skills`` immediately before any
    file copy. With ``BICAMERAL_SKILLS_VERIFY_DISABLE=1`` set, swallows
    ``SignatureError`` after writing a severity-3 ledger event. Otherwise
    re-raises (fail-closed).
    """
    try:
        verify_skills_manifest(manifest_path, bundle_path, expected_skills)
    except SignatureError:
        if os.environ.get("BICAMERAL_SKILLS_VERIFY_DISABLE") != "1":
            raise
        writer = _get_event_writer()
        writer.write(
            "verification_bypassed",
            {
                "ts": datetime.now(UTC).isoformat(),
                "manifest_kind": "skills",
                "manifest_path": str(manifest_path),
                "manifest_sha256": _manifest_sha256(manifest_path),
                "reason": "BICAMERAL_SKILLS_VERIFY_DISABLE=1",
                "severity": 3,
            },
        )
