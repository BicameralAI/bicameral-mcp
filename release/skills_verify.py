"""Install-time verifier for ``skills/MANIFEST.toml`` (#218 LLM-06).

Mirrors `release.manifest_verify` (#237 LLM-11) for the skills-content
surface. Loads the bundled manifest, verifies its keyless cosign
signature via ``_VERIFIER_HOOK`` (default: same deferred sigstore-python
stub as #237), then cross-checks the SHA-256 of every skill file the
installer is about to copy against the verified manifest. Mismatch →
``SignatureError``.

Bypass posture: ``BICAMERAL_SKILLS_VERIFY_DISABLE=1`` swallows the
``SignatureError`` after writing a severity-3 ``verification_bypassed``
ledger event via ``EventFileWriter`` with ``manifest_kind: "skills"``
for disambiguation against the LLM-11 hooks-manifest bypass events.
Without the env var, the error propagates to the caller (fail-closed).

Cosign keyless verification activation depends on the same deferred
sigstore-python wiring as #237 LLM-11; both verifiers activate together
when the stub at this module's `_sigstore_verify` is replaced with a
real `Verifier.production()` call.
"""

from __future__ import annotations

import hashlib
import os
import tomllib
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

_VERIFIER_FN = Callable[[Path, Path, Path], None]


class SignatureError(Exception):
    """Raised on any verification failure (bad sig, missing artifact,
    SHA-256 mismatch, malformed manifest)."""


def _sigstore_verify(manifest_path: Path, sig_path: Path, cert_path: Path) -> None:
    """Default verifier: sigstore-python keyless verification.

    v1 stub: sigstore-python integration is a deferred follow-up
    (mirrors `release/manifest_verify.py:_sigstore_verify` from #237).
    Wheel-bundling of `.sig` and `.crt` is not yet shipping, so this
    stub is unreachable in the current production install path —
    `setup_wizard._bundled_skills_manifest_paths()` returns None for
    all current installs.

    When the deferred sigstore-python wiring lands, this stub is
    replaced with a real `Verifier.production()` call against
    `sigstore.models.Bundle`. Both LLM-11 (hooks) and LLM-06 (skills)
    verification activate together at that point.
    """
    if not manifest_path.exists():
        raise SignatureError(f"manifest not found: {manifest_path}")
    if not sig_path.exists():
        raise SignatureError(f"signature not found: {sig_path}")
    if not cert_path.exists():
        raise SignatureError(f"certificate not found: {cert_path}")
    raise SignatureError(
        "sigstore-python keyless verification is a deferred #218 follow-up "
        "(shared stub with LLM-11 hooks-manifest verifier). Set "
        "BICAMERAL_SKILLS_VERIFY_DISABLE=1 to bypass (writes severity-3 "
        "verification_bypassed ledger event)."
    )


_VERIFIER_HOOK: _VERIFIER_FN = _sigstore_verify


def verify_skills_manifest(
    manifest_path: Path,
    sig_path: Path,
    cert_path: Path,
    expected_skills: dict[str, dict[str, str]],
) -> None:
    """Verify the manifest signature and cross-check per-file SHA-256.

    ``expected_skills`` maps ``{skill_name: {filename: sha256_hex}}`` for
    every file the caller intends to copy. Every entry must appear in
    the verified manifest with matching SHA-256; any miss raises
    ``SignatureError``.
    """
    if not manifest_path.exists():
        raise SignatureError(f"manifest not found: {manifest_path}")
    _VERIFIER_HOOK(manifest_path, sig_path, cert_path)

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
    sig_path: Path,
    cert_path: Path,
    expected_skills: dict[str, dict[str, str]],
) -> None:
    """Verify the manifest; on failure, honor the bypass env var.

    Called by ``setup_wizard._install_skills`` immediately before any
    file copy. With ``BICAMERAL_SKILLS_VERIFY_DISABLE=1`` set, swallows
    ``SignatureError`` after writing a severity-3 ledger event. Otherwise
    re-raises (fail-closed).
    """
    try:
        verify_skills_manifest(manifest_path, sig_path, cert_path, expected_skills)
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
