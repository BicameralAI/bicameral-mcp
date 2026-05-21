"""Sigstore-bundle verifier tests for ``release.skills_verify`` (#292).

Mirror of ``tests/test_manifest_verify_sigstore.py`` for the skills
surface. Locks the install-time skills-manifest verification contract
after the switch from a ``(manifest, sig, cert)`` triple to a
``(manifest, bundle)`` pair where ``bundle`` is a single-file Sigstore
``.sigstore`` artifact:

- Missing manifest → ``SignatureError`` (via ``_sigstore_verify`` directly)
- Missing bundle → ``SignatureError`` (via ``_sigstore_verify`` directly)
- ``_VERIFIER_HOOK`` seam drives ``verify_skills_or_bypass`` happy path
- Bypass env var ON → swallows + writes severity-3 ``verification_bypassed``
  with ``manifest_kind: "skills"``
- One seam test pins the ``_sigstore_verify`` 2-arg call shape

Plan #292 Open Question 3 authorizes the narrow ``_VERIFIER_HOOK`` seam:
real ``Verifier.production()`` cannot run without an OIDC-signed bundle.
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

import pytest

from release import skills_verify


def _write_manifest(tmp_path: Path, skills: dict[str, dict[str, bytes]]) -> Path:
    """Render a skills-manifest.toml carrying the SHA-256 of each file."""
    manifest_path = tmp_path / "skills-manifest.toml"
    lines = ["manifest_version = 1", ""]
    for skill_name in sorted(skills):
        lines.append(f"[skills.{skill_name}]")
        for filename in sorted(skills[skill_name]):
            digest = hashlib.sha256(skills[skill_name][filename]).hexdigest()
            lines.append(f'"{filename}" = "{digest}"')
        lines.append("")
    manifest_path.write_text("\n".join(lines), encoding="utf-8")
    return manifest_path


def _expected(skills: dict[str, dict[str, bytes]]) -> dict[str, dict[str, str]]:
    return {
        skill_name: {fn: hashlib.sha256(fb).hexdigest() for fn, fb in files.items()}
        for skill_name, files in skills.items()
    }


def test_sigstore_verify_raises_when_manifest_absent(tmp_path: Path) -> None:
    """Fail-closed: a missing manifest path raises before any sigstore work."""
    manifest = tmp_path / "nope-skills-manifest.toml"
    bundle = tmp_path / "nope-skills-manifest.toml.sigstore"
    bundle.write_bytes(b"BUNDLE")
    with pytest.raises(skills_verify.SignatureError, match="manifest not found"):
        skills_verify._sigstore_verify(manifest, bundle)


def test_sigstore_verify_raises_when_bundle_absent(tmp_path: Path) -> None:
    """Fail-closed: a present manifest but missing `.sigstore` bundle raises."""
    manifest = _write_manifest(tmp_path, {"bicameral-ingest": {"SKILL.md": b"body"}})
    bundle = tmp_path / "skills-manifest.toml.sigstore"  # never created
    with pytest.raises(skills_verify.SignatureError, match="sigstore bundle not found"):
        skills_verify._sigstore_verify(manifest, bundle)


def test_sigstore_verify_raises_on_malformed_bundle(tmp_path: Path) -> None:
    """Fail-closed: a present manifest + a `.sigstore` file of non-JSON
    garbage raises ``SignatureError``. ``Bundle.from_json`` rejects
    malformed input locally — before any Rekor/Fulcio network call — so
    this exercises the REAL ``_sigstore_verify`` (no `_VERIFIER_HOOK`
    seam) and pins that a corrupt bundle cannot slip past verification."""
    manifest = _write_manifest(tmp_path, {"bicameral-ingest": {"SKILL.md": b"body"}})
    bundle = tmp_path / "skills-manifest.toml.sigstore"
    bundle.write_bytes(b"not-a-sigstore-bundle {{{ truncated garbage")
    with pytest.raises(skills_verify.SignatureError):
        skills_verify._sigstore_verify(manifest, bundle)


def test_verify_skills_or_bypass_happy_path_via_seam(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the `_VERIFIER_HOOK` seam substituted for a no-op, a manifest
    that carries the expected per-file SHA-256s verifies cleanly."""
    skills = {"bicameral-ingest": {"SKILL.md": b"ingest body"}}
    manifest = _write_manifest(tmp_path, skills)
    bundle = tmp_path / "skills-manifest.toml.sigstore"
    bundle.write_bytes(b"FAKE-SIGSTORE-BUNDLE")

    monkeypatch.setattr(skills_verify, "_VERIFIER_HOOK", lambda *_: None)
    monkeypatch.delenv("BICAMERAL_SKILLS_VERIFY_DISABLE", raising=False)

    result = skills_verify.verify_skills_or_bypass(manifest, bundle, _expected(skills))
    assert result is None


def test_verify_skills_or_bypass_writes_event_when_bypassed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bypass posture: a failing verifier + BICAMERAL_SKILLS_VERIFY_DISABLE=1
    swallows the SignatureError and writes a severity-3
    ``verification_bypassed`` event with ``manifest_kind: "skills"``."""
    skills = {"bicameral-ingest": {"SKILL.md": b"body"}}
    manifest = _write_manifest(tmp_path, skills)
    bundle = tmp_path / "skills-manifest.toml.sigstore"
    bundle.write_bytes(b"FAKE-SIGSTORE-BUNDLE")

    captured: list[tuple[str, dict]] = []

    class StubWriter:
        def write(self, event_type, payload):
            captured.append((event_type, payload))
            return tmp_path / "stub.jsonl"

    def bad_verifier(*_args):
        raise skills_verify.SignatureError("sigstore verification failed: tampered")

    monkeypatch.setattr(skills_verify, "_VERIFIER_HOOK", bad_verifier)
    monkeypatch.setattr(skills_verify, "_get_event_writer", lambda: StubWriter())
    monkeypatch.setenv("BICAMERAL_SKILLS_VERIFY_DISABLE", "1")

    result = skills_verify.verify_skills_or_bypass(manifest, bundle, _expected(skills))
    assert result is None
    assert len(captured) == 1
    event_type, payload = captured[0]
    assert event_type == "verification_bypassed"
    assert payload["severity"] == 3
    assert payload["manifest_kind"] == "skills"
    assert payload["reason"] == "BICAMERAL_SKILLS_VERIFY_DISABLE=1"
    assert "manifest_sha256" in payload


def test_sigstore_verify_call_shape_is_two_arg(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the `_sigstore_verify` call shape: `verify_skills_manifest` invokes
    `_VERIFIER_HOOK` with exactly `(manifest_path, bundle_path)` — the 2-arg
    contract the real `Verifier.production()` wiring depends on (#292)."""
    seen: list[tuple] = []

    def recording_hook(*args):
        seen.append(args)

    skills = {"bicameral-ingest": {"SKILL.md": b"body"}}
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        manifest = _write_manifest(tmp, skills)
        bundle = tmp / "skills-manifest.toml.sigstore"
        bundle.write_bytes(b"BUNDLE")
        monkeypatch.setattr(skills_verify, "_VERIFIER_HOOK", recording_hook)
        skills_verify.verify_skills_manifest(manifest, bundle, _expected(skills))

    assert len(seen) == 1
    args = seen[0]
    assert len(args) == 2, f"_VERIFIER_HOOK must be called with 2 args, got {len(args)}"
    assert isinstance(args[0], Path) and isinstance(args[1], Path)
    assert str(args[1]).endswith(".sigstore")
