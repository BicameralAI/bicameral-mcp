"""Sigstore-bundle verifier tests for ``release.manifest_verify`` (#292).

Locks the install-time hooks-manifest verification contract after the
switch from a ``(manifest, sig, cert)`` triple to a ``(manifest, bundle)``
pair where ``bundle`` is a single-file Sigstore ``.sigstore`` artifact:

- Missing manifest → ``SignatureError`` (via ``_sigstore_verify`` directly)
- Missing bundle → ``SignatureError`` (via ``_sigstore_verify`` directly)
- ``_VERIFIER_HOOK`` seam drives ``verify_hooks_or_bypass`` happy path
- Bypass env var ON → swallows + writes severity-3 ``verification_bypassed``
- One seam test pins the ``_sigstore_verify`` 2-arg call shape

The real ``Verifier.production()`` cannot run without an OIDC-signed
bundle (Plan #292 Open Question 3 authorizes the narrow ``_VERIFIER_HOOK``
seam for the wiring tests). The default ``_sigstore_verify`` is exercised
only for its fail-closed file-presence guards, which run before any
network/crypto.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from release import manifest_verify


def _write_manifest(tmp_path: Path, hooks: list[dict[str, str]]) -> Path:
    """Render a hooks-manifest.json carrying the SHA-256 of each command."""
    manifest_path = tmp_path / "hooks-manifest.json"
    entries = [
        {
            "event_type": h["event_type"],
            "command": h["command"],
            "sha256": hashlib.sha256(h["command"].encode("utf-8")).hexdigest(),
        }
        for h in hooks
    ]
    manifest_path.write_text(json.dumps({"manifest_version": 1, "hooks": entries}))
    return manifest_path


def _expected(hooks: list[dict[str, str]]) -> dict[str, str]:
    return {
        h["event_type"]: hashlib.sha256(h["command"].encode("utf-8")).hexdigest() for h in hooks
    }


def test_sigstore_verify_raises_when_manifest_absent(tmp_path: Path) -> None:
    """Fail-closed: a missing manifest path raises before any sigstore work."""
    manifest = tmp_path / "nope-hooks-manifest.json"
    bundle = tmp_path / "nope-hooks-manifest.json.sigstore"
    bundle.write_bytes(b"BUNDLE")
    with pytest.raises(manifest_verify.SignatureError, match="manifest not found"):
        manifest_verify._sigstore_verify(manifest, bundle)


def test_sigstore_verify_raises_when_bundle_absent(tmp_path: Path) -> None:
    """Fail-closed: a present manifest but missing `.sigstore` bundle raises."""
    manifest = _write_manifest(tmp_path, [{"event_type": "git:post-commit", "command": "git x"}])
    bundle = tmp_path / "hooks-manifest.json.sigstore"  # never created
    with pytest.raises(manifest_verify.SignatureError, match="sigstore bundle not found"):
        manifest_verify._sigstore_verify(manifest, bundle)


def test_sigstore_verify_raises_on_malformed_bundle(tmp_path: Path) -> None:
    """Fail-closed: a present manifest + a `.sigstore` file of non-JSON
    garbage raises ``SignatureError``. ``Bundle.from_json`` rejects
    malformed input locally — before any Rekor/Fulcio network call — so
    this exercises the REAL ``_sigstore_verify`` (no `_VERIFIER_HOOK`
    seam) and pins that a corrupt bundle cannot slip past verification."""
    manifest = _write_manifest(tmp_path, [{"event_type": "git:post-commit", "command": "git x"}])
    bundle = tmp_path / "hooks-manifest.json.sigstore"
    bundle.write_bytes(b"not-a-sigstore-bundle {{{ truncated garbage")
    with pytest.raises(manifest_verify.SignatureError):
        manifest_verify._sigstore_verify(manifest, bundle)


def test_verify_hooks_or_bypass_happy_path_via_seam(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the `_VERIFIER_HOOK` seam substituted for a no-op, a manifest
    that carries the expected command SHA-256s verifies cleanly (no
    exception → returns None)."""
    hooks = [{"event_type": "git:post-commit", "command": "git log -1"}]
    manifest = _write_manifest(tmp_path, hooks)
    bundle = tmp_path / "hooks-manifest.json.sigstore"
    bundle.write_bytes(b"FAKE-SIGSTORE-BUNDLE")

    monkeypatch.setattr(manifest_verify, "_VERIFIER_HOOK", lambda *_: None)
    monkeypatch.delenv("BICAMERAL_HOOKS_VERIFY_DISABLE", raising=False)

    result = manifest_verify.verify_hooks_or_bypass(manifest, bundle, _expected(hooks))
    assert result is None


def test_verify_hooks_or_bypass_writes_event_when_bypassed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bypass posture: a failing verifier + BICAMERAL_HOOKS_VERIFY_DISABLE=1
    swallows the SignatureError and writes a severity-3
    ``verification_bypassed`` event."""
    hooks = [{"event_type": "git:post-commit", "command": "git log -1"}]
    manifest = _write_manifest(tmp_path, hooks)
    bundle = tmp_path / "hooks-manifest.json.sigstore"
    bundle.write_bytes(b"FAKE-SIGSTORE-BUNDLE")

    captured: list[tuple[str, dict]] = []

    class StubWriter:
        def write(self, event_type, payload):
            captured.append((event_type, payload))
            return tmp_path / "stub.jsonl"

    def bad_verifier(*_args):
        raise manifest_verify.SignatureError("sigstore verification failed: tampered")

    monkeypatch.setattr(manifest_verify, "_VERIFIER_HOOK", bad_verifier)
    monkeypatch.setattr(manifest_verify, "_get_event_writer", lambda: StubWriter())
    monkeypatch.setenv("BICAMERAL_HOOKS_VERIFY_DISABLE", "1")

    result = manifest_verify.verify_hooks_or_bypass(manifest, bundle, _expected(hooks))
    assert result is None
    assert len(captured) == 1
    event_type, payload = captured[0]
    assert event_type == "verification_bypassed"
    assert payload["severity"] == 3
    assert payload["reason"] == "BICAMERAL_HOOKS_VERIFY_DISABLE=1"
    assert "manifest_sha256" in payload


def test_sigstore_verify_call_shape_is_two_arg(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the `_sigstore_verify` call shape: `verify_hooks_manifest` invokes
    `_VERIFIER_HOOK` with exactly `(manifest_path, bundle_path)` — the 2-arg
    contract the real `Verifier.production()` wiring depends on (#292)."""
    seen: list[tuple] = []

    def recording_hook(*args):
        seen.append(args)

    hooks = [{"event_type": "git:post-commit", "command": "git log -1"}]
    # Build a manifest in a tmp dir via pytest's tmp_path is unavailable here;
    # use a recording hook + a real on-disk manifest from a fixture dir.
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        manifest = _write_manifest(tmp, hooks)
        bundle = tmp / "hooks-manifest.json.sigstore"
        bundle.write_bytes(b"BUNDLE")
        monkeypatch.setattr(manifest_verify, "_VERIFIER_HOOK", recording_hook)
        manifest_verify.verify_hooks_manifest(manifest, bundle, _expected(hooks))

    assert len(seen) == 1
    args = seen[0]
    assert len(args) == 2, f"_VERIFIER_HOOK must be called with 2 args, got {len(args)}"
    assert isinstance(args[0], Path) and isinstance(args[1], Path)
    assert str(args[1]).endswith(".sigstore")
