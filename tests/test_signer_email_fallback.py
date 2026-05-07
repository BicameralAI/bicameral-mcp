"""Behavioral tests for `events.writer._resolve_signer_email` (#200 Phase 2).

The signer-resolution policy applies one of three modes to a raw git
`user.email` fallback:
  - ``redact``: returns the literal ``"<REDACTED>"``
  - ``local-part-only``: returns the part before ``@`` (privacy-positive default)
  - ``full``: returns the email verbatim (legacy / explicit-opt-in path)

The mode comes from ``.bicameral/config.yaml: signer_email_fallback``;
config-load happens in ``context.py``. This test pins the policy
function's input/output contract independent of the config-load path.
"""

from __future__ import annotations

from events.writer import _resolve_signer_email


def test_redact_mode_returns_redacted_literal() -> None:
    result = _resolve_signer_email("user@example.com", mode="redact")
    assert result == "<REDACTED>"


def test_local_part_only_mode_strips_host() -> None:
    result = _resolve_signer_email("user@example.com", mode="local-part-only")
    assert result == "user"


def test_full_mode_returns_verbatim_email() -> None:
    result = _resolve_signer_email("user@example.com", mode="full")
    assert result == "user@example.com"
