"""Install-time verifier for ``hooks-manifest.json`` (#218 Phase 2).

Loads the bundled manifest, verifies its keyless cosign signature via
``_VERIFIER_HOOK`` (default: ``sigstore-python``), then cross-checks the
SHA-256 of every command the installer is about to write against the
verified manifest. Mismatch → ``SignatureError``.

Bypass posture: ``BICAMERAL_HOOKS_VERIFY_DISABLE=1`` swallows the
``SignatureError`` after writing a severity-3 ``verification_bypassed``
ledger event via ``EventFileWriter``. Without the env var, the error
propagates to the caller (fail-closed).

The ``_VERIFIER_HOOK`` is a module-level function pointer to enable
swapping in an offline-keypair verifier in a future #218 sub-task
without touching this module's call sites.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

_VERIFIER_FN = Callable[[Path, Path, Path], None]


class SignatureError(Exception):
    """Raised on any verification failure (bad sig, missing artifact,
    SHA-256 mismatch, malformed manifest)."""


def _sigstore_verify(manifest_path: Path, sig_path: Path, cert_path: Path) -> None:
    """Default verifier: sigstore-python keyless verification.

    Imported lazily so test environments without ``sigstore`` installed
    can still monkeypatch ``_VERIFIER_HOOK``. Production install path
    (real `cosign sign-blob` artifacts) requires ``sigstore>=3.0``.
    """
    try:
        from sigstore.models import Bundle  # type: ignore
        from sigstore.verify import Verifier, policy  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise SignatureError(
            f"sigstore-python not installed; cannot verify keyless cosign signature: {exc}"
        ) from exc

    if not manifest_path.exists():
        raise SignatureError(f"manifest not found: {manifest_path}")
    if not sig_path.exists():
        raise SignatureError(f"signature not found: {sig_path}")
    if not cert_path.exists():
        raise SignatureError(f"certificate not found: {cert_path}")

    verifier = Verifier.production()
    verification_policy = policy.Identity(
        identity="https://github.com/BicameralAI/bicameral-mcp/.github/workflows/publish.yml@refs/tags/v*",
        issuer="https://token.actions.githubusercontent.com",
    )
    bundle_input = {
        "cert": cert_path.read_bytes(),
        "sig": sig_path.read_bytes(),
        "blob": manifest_path.read_bytes(),
    }
    try:
        bundle = Bundle.from_parts(**bundle_input)
        verifier.verify_artifact(
            input_=manifest_path.read_bytes(), bundle=bundle, policy=verification_policy
        )
    except Exception as exc:
        raise SignatureError(f"cosign keyless verification failed: {exc}") from exc


_VERIFIER_HOOK: _VERIFIER_FN = _sigstore_verify


def verify_hooks_manifest(
    manifest_path: Path,
    sig_path: Path,
    cert_path: Path,
    expected_hooks: dict[str, str],
) -> None:
    """Verify the manifest signature and cross-check SHA-256 entries.

    ``expected_hooks`` maps ``event_type`` → ``sha256(command_bytes)`` for
    every hook the caller intends to write. Every entry must appear in
    the verified manifest with matching SHA-256; any miss raises
    ``SignatureError``.
    """
    if not manifest_path.exists():
        raise SignatureError(f"manifest not found: {manifest_path}")
    _VERIFIER_HOOK(manifest_path, sig_path, cert_path)

    parsed = json.loads(manifest_path.read_text(encoding="utf-8"))
    by_event = {h["event_type"]: h["sha256"] for h in parsed.get("hooks", [])}

    for event_type, expected_sha in expected_hooks.items():
        actual = by_event.get(event_type)
        if actual is None:
            raise SignatureError(f"hook {event_type!r} absent from verified manifest")
        if actual != expected_sha:
            raise SignatureError(
                f"hook {event_type!r} sha256 mismatch: "
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


def verify_hooks_or_bypass(
    manifest_path: Path,
    sig_path: Path,
    cert_path: Path,
    expected_hooks: dict[str, str],
) -> None:
    """Verify the manifest; on failure, honor the bypass env var.

    Called by ``setup_wizard._install_*_hooks`` immediately before any
    file write. With ``BICAMERAL_HOOKS_VERIFY_DISABLE=1`` set, swallows
    ``SignatureError`` after writing a severity-3 ledger event. Otherwise
    re-raises (fail-closed).
    """
    try:
        verify_hooks_manifest(manifest_path, sig_path, cert_path, expected_hooks)
    except SignatureError:
        if os.environ.get("BICAMERAL_HOOKS_VERIFY_DISABLE") != "1":
            raise
        writer = _get_event_writer()
        writer.write(
            "verification_bypassed",
            {
                "ts": datetime.now(UTC).isoformat(),
                "manifest_path": str(manifest_path),
                "manifest_sha256": _manifest_sha256(manifest_path),
                "reason": "BICAMERAL_HOOKS_VERIFY_DISABLE=1",
                "severity": 3,
            },
        )
