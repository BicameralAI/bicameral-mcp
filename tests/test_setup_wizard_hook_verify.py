"""Functionality tests for `release.manifest_verify` + the
`_verify_hooks_or_bypass` integration into `setup_wizard` (#218 Phase 2).

Locks the install-time verification contract:
- Valid signed manifest + matching expected hooks → no exception
- Tampered signature → SignatureError
- SHA-256 mismatch (manifest doesn't carry the command we'd write) →
  SignatureError
- Missing manifest file → SignatureError
- Bypass env var ON: SignatureError swallowed; severity-3
  ``verification_bypassed`` event written via EventFileWriter
- Bypass env var OFF: SignatureError propagates to caller
- ``_VERIFIER_HOOK`` is module-level swappable (function-pointer
  extension surface)
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from release import manifest_verify


def _signed_manifest(tmp_path: Path, hooks: list[dict[str, str]]) -> tuple[Path, Path]:
    """Write a fake manifest + its `.sigstore` bundle to tmp_path (#292).

    Returns the `(manifest, bundle)` pair. Real sigstore verification is
    monkeypatched via `_VERIFIER_HOOK`, so the bundle bytes are a
    placeholder."""
    import json

    manifest_path = tmp_path / "hooks-manifest.json"
    bundle_path = tmp_path / "hooks-manifest.json.sigstore"
    entries = []
    for h in hooks:
        entries.append(
            {
                "event_type": h["event_type"],
                "command": h["command"],
                "sha256": hashlib.sha256(h["command"].encode("utf-8")).hexdigest(),
            }
        )
    manifest_path.write_text(json.dumps({"manifest_version": 1, "hooks": entries}))
    bundle_path.write_bytes(b"FAKE-SIGSTORE-BUNDLE")
    return manifest_path, bundle_path


def _expected_hooks(hooks: list[dict[str, str]]) -> dict[str, str]:
    """Cross-check shape: ``{event_type: sha256_of_command}``."""
    return {
        h["event_type"]: hashlib.sha256(h["command"].encode("utf-8")).hexdigest() for h in hooks
    }


def test_verify_hooks_manifest_returns_none_for_valid_signed_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hooks = [
        {"event_type": "claude:PostToolUse:Bash", "command": "echo a"},
        {"event_type": "git:post-commit", "command": "git log -1"},
    ]
    m, b = _signed_manifest(tmp_path, hooks)
    monkeypatch.setattr(manifest_verify, "_VERIFIER_HOOK", lambda *_: None)
    # No exception raised → contract satisfied (returns None).
    result = manifest_verify.verify_hooks_manifest(m, b, _expected_hooks(hooks))
    assert result is None


def test_verify_hooks_manifest_raises_signature_error_when_sig_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hooks = [{"event_type": "git:post-commit", "command": "git log -1"}]
    m, b = _signed_manifest(tmp_path, hooks)

    def bad_verifier(*_args):
        raise manifest_verify.SignatureError("sigstore: invalid signature")

    monkeypatch.setattr(manifest_verify, "_VERIFIER_HOOK", bad_verifier)
    with pytest.raises(manifest_verify.SignatureError):
        manifest_verify.verify_hooks_manifest(m, b, _expected_hooks(hooks))


def test_verify_hooks_manifest_raises_when_command_sha256_mismatches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hooks = [{"event_type": "git:post-commit", "command": "git log -1"}]
    m, b = _signed_manifest(tmp_path, hooks)
    monkeypatch.setattr(manifest_verify, "_VERIFIER_HOOK", lambda *_: None)
    # Caller claims to want a DIFFERENT command than what the manifest carries.
    wrong = {"git:post-commit": hashlib.sha256(b"git log -2").hexdigest()}
    with pytest.raises(manifest_verify.SignatureError):
        manifest_verify.verify_hooks_manifest(m, b, wrong)


def test_verify_hooks_manifest_raises_when_manifest_file_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(manifest_verify, "_VERIFIER_HOOK", lambda *_: None)
    nope = tmp_path / "absent.json"
    bundle = tmp_path / "absent.json.sigstore"
    with pytest.raises(manifest_verify.SignatureError):
        manifest_verify.verify_hooks_manifest(nope, bundle, {})


def test_verifier_hook_is_swappable_at_module_level(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hooks = [{"event_type": "git:post-commit", "command": "git log -1"}]
    m, b = _signed_manifest(tmp_path, hooks)
    sentinel = MagicMock(return_value=None)
    monkeypatch.setattr(manifest_verify, "_VERIFIER_HOOK", sentinel)
    manifest_verify.verify_hooks_manifest(m, b, _expected_hooks(hooks))
    # Function-pointer extension surface contract: the module-level pointer
    # is the verifier called by `verify_hooks_manifest`.
    sentinel.assert_called_once()


def test_install_claude_hooks_proceeds_with_bypass_event_when_env_var_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bypass posture: BICAMERAL_HOOKS_VERIFY_DISABLE=1 → SignatureError
    swallowed, severity-3 ``verification_bypassed`` event written, install
    proceeds. Captures the event-write call."""
    hooks = [{"event_type": "git:post-commit", "command": "git log -1"}]
    m, b = _signed_manifest(tmp_path, hooks)

    captured_events: list[tuple[str, dict]] = []

    class StubWriter:
        def write(self, event_type, payload):
            captured_events.append((event_type, payload))
            return tmp_path / "stub.jsonl"

    def bad_verifier(*_args):
        raise manifest_verify.SignatureError("intentional test failure")

    monkeypatch.setattr(manifest_verify, "_VERIFIER_HOOK", bad_verifier)
    monkeypatch.setenv("BICAMERAL_HOOKS_VERIFY_DISABLE", "1")
    monkeypatch.setattr(manifest_verify, "_get_event_writer", lambda: StubWriter())

    # The helper raises only when bypass is OFF; with bypass ON it returns None.
    result = manifest_verify.verify_hooks_or_bypass(m, b, _expected_hooks(hooks))
    assert result is None
    assert len(captured_events) == 1
    event_type, payload = captured_events[0]
    assert event_type == "verification_bypassed"
    assert payload.get("severity") == 3
    assert "manifest_sha256" in payload
    assert payload.get("reason") == "BICAMERAL_HOOKS_VERIFY_DISABLE=1"


def test_install_claude_hooks_raises_signature_error_when_env_var_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bypass OFF: SignatureError propagates to caller (fail-closed)."""
    hooks = [{"event_type": "git:post-commit", "command": "git log -1"}]
    m, b = _signed_manifest(tmp_path, hooks)

    def bad_verifier(*_args):
        raise manifest_verify.SignatureError("intentional test failure")

    monkeypatch.setattr(manifest_verify, "_VERIFIER_HOOK", bad_verifier)
    monkeypatch.delenv("BICAMERAL_HOOKS_VERIFY_DISABLE", raising=False)

    with pytest.raises(manifest_verify.SignatureError):
        manifest_verify.verify_hooks_or_bypass(m, b, _expected_hooks(hooks))
