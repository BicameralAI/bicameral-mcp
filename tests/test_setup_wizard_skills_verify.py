"""Functionality tests for `release.skills_verify` + `setup_wizard`
integration (#218 LLM-06).

Locks the install-time skills-manifest verification contract:
- Valid signed manifest + matching expected skills → no exception
- Tampered signature → SignatureError
- Per-file SHA-256 mismatch → SignatureError
- Missing manifest file → SignatureError
- Bypass env var ON: SignatureError swallowed; severity-3
  ``verification_bypassed`` event written via EventFileWriter with
  ``manifest_kind: "skills"`` field
- Bypass env var OFF: SignatureError propagates
- ``_VERIFIER_HOOK`` is module-level swappable (function-pointer
  extension surface; mirrors release.manifest_verify)
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from release import skills_verify


def _signed_manifest(
    tmp_path: Path,
    skills: dict[str, dict[str, bytes]],
) -> tuple[Path, Path, Path]:
    """Write a fake signed manifest + sig + cert to tmp_path.

    `skills` is `{skill_name: {filename: file_bytes}}`. The test
    rendered manifest carries the SHA-256 of each file; the .sig and
    .crt are placeholder bytes (real verification is monkeypatched).
    """
    manifest_path = tmp_path / "skills-manifest.toml"
    sig_path = tmp_path / "skills-manifest.toml.sig"
    cert_path = tmp_path / "skills-manifest.toml.crt"

    lines = ["manifest_version = 1", ""]
    for skill_name in sorted(skills):
        lines.append(f"[skills.{skill_name}]")
        for filename in sorted(skills[skill_name]):
            digest = hashlib.sha256(skills[skill_name][filename]).hexdigest()
            lines.append(f'"{filename}" = "{digest}"')
        lines.append("")
    manifest_path.write_text("\n".join(lines), encoding="utf-8")
    sig_path.write_bytes(b"FAKE-SIG")
    cert_path.write_bytes(b"FAKE-CERT")
    return manifest_path, sig_path, cert_path


def _expected_skills(skills: dict[str, dict[str, bytes]]) -> dict[str, dict[str, str]]:
    """Cross-check shape: ``{skill_name: {filename: sha256_hex}}``."""
    return {
        skill_name: {
            filename: hashlib.sha256(file_bytes).hexdigest()
            for filename, file_bytes in files.items()
        }
        for skill_name, files in skills.items()
    }


def test_verify_skills_manifest_returns_none_for_valid_signed_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skills = {"bicameral-ingest": {"SKILL.md": b"ingest body"}}
    m, s, c = _signed_manifest(tmp_path, skills)
    monkeypatch.setattr(skills_verify, "_VERIFIER_HOOK", lambda *_: None)
    result = skills_verify.verify_skills_manifest(m, s, c, _expected_skills(skills))
    assert result is None


def test_verify_skills_manifest_raises_signature_error_when_sig_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skills = {"bicameral-ingest": {"SKILL.md": b"body"}}
    m, s, c = _signed_manifest(tmp_path, skills)

    def bad_verifier(*_args):
        raise skills_verify.SignatureError("sigstore: invalid signature")

    monkeypatch.setattr(skills_verify, "_VERIFIER_HOOK", bad_verifier)
    with pytest.raises(skills_verify.SignatureError):
        skills_verify.verify_skills_manifest(m, s, c, _expected_skills(skills))


def test_verify_skills_manifest_raises_when_file_sha256_mismatches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skills = {"bicameral-ingest": {"SKILL.md": b"body"}}
    m, s, c = _signed_manifest(tmp_path, skills)
    monkeypatch.setattr(skills_verify, "_VERIFIER_HOOK", lambda *_: None)
    wrong = {"bicameral-ingest": {"SKILL.md": hashlib.sha256(b"different").hexdigest()}}
    with pytest.raises(skills_verify.SignatureError):
        skills_verify.verify_skills_manifest(m, s, c, wrong)


def test_verify_skills_manifest_raises_when_manifest_file_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(skills_verify, "_VERIFIER_HOOK", lambda *_: None)
    nope = tmp_path / "absent.toml"
    sig = tmp_path / "absent.toml.sig"
    crt = tmp_path / "absent.toml.crt"
    with pytest.raises(skills_verify.SignatureError):
        skills_verify.verify_skills_manifest(nope, sig, crt, {})


def test_verifier_hook_is_swappable_at_module_level(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skills = {"bicameral-ingest": {"SKILL.md": b"body"}}
    m, s, c = _signed_manifest(tmp_path, skills)
    sentinel = MagicMock(return_value=None)
    monkeypatch.setattr(skills_verify, "_VERIFIER_HOOK", sentinel)
    skills_verify.verify_skills_manifest(m, s, c, _expected_skills(skills))
    sentinel.assert_called_once()


def test_install_skills_proceeds_with_bypass_event_when_env_var_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BICAMERAL_SKILLS_VERIFY_DISABLE=1 swallows SignatureError after
    writing severity-3 verification_bypassed event with
    manifest_kind=skills."""
    skills = {"bicameral-ingest": {"SKILL.md": b"body"}}
    m, s, c = _signed_manifest(tmp_path, skills)

    captured_events: list[tuple[str, dict]] = []

    class StubWriter:
        def write(self, event_type, payload):
            captured_events.append((event_type, payload))
            return tmp_path / "stub.jsonl"

    def bad_verifier(*_args):
        raise skills_verify.SignatureError("intentional test failure")

    monkeypatch.setattr(skills_verify, "_VERIFIER_HOOK", bad_verifier)
    monkeypatch.setenv("BICAMERAL_SKILLS_VERIFY_DISABLE", "1")
    monkeypatch.setattr(skills_verify, "_get_event_writer", lambda: StubWriter())

    result = skills_verify.verify_skills_or_bypass(m, s, c, _expected_skills(skills))
    assert result is None
    assert len(captured_events) == 1
    event_type, payload = captured_events[0]
    assert event_type == "verification_bypassed"
    assert payload.get("severity") == 3
    assert payload.get("manifest_kind") == "skills"
    assert "manifest_sha256" in payload
    assert payload.get("reason") == "BICAMERAL_SKILLS_VERIFY_DISABLE=1"


def test_install_skills_raises_signature_error_when_env_var_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bypass OFF: SignatureError propagates to caller (fail-closed)."""
    skills = {"bicameral-ingest": {"SKILL.md": b"body"}}
    m, s, c = _signed_manifest(tmp_path, skills)

    def bad_verifier(*_args):
        raise skills_verify.SignatureError("intentional test failure")

    monkeypatch.setattr(skills_verify, "_VERIFIER_HOOK", bad_verifier)
    monkeypatch.delenv("BICAMERAL_SKILLS_VERIFY_DISABLE", raising=False)

    with pytest.raises(skills_verify.SignatureError):
        skills_verify.verify_skills_or_bypass(m, s, c, _expected_skills(skills))
