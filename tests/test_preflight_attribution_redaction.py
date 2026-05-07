"""Functionality tests for `handlers.preflight._apply_attribution_policy`
refined positional-cue regex (#209).

Locks the preserved-vs-redacted boundaries against drift:
- Names following positional cues (`· `, `, ` adjacent to date, `Speaker:`,
  `From:`) are redacted to `<NAME_REDACTED>`
- Capitalized context tokens that don't follow positional cues survive
  (Sprint, Linear, GitHub, etc.) — no allowlist needed because the
  positional patterns require explicit cues by construction
- Dates `YYYY-MM-DD` are redacted to `<DATE_REDACTED>`
- The `_DEFAULT_RENDER_ATTRIBUTION_MODE` constant has flipped to `redacted`
- Fresh `_write_collaboration_config` invocations write `render_source_attribution: redacted` (not `full`)

Replaces the v1 overbroad `_NAME_PATTERN = re.compile(r"\b[A-Z][a-z]+\b")`
which redacted ALL capitalized tokens including platform/tool names.
"""

from __future__ import annotations

from pathlib import Path

import context
from contracts import DecisionMatch
from handlers.preflight import _apply_attribution_policy


def _match(source_ref: str) -> DecisionMatch:
    """Build a minimal DecisionMatch for redaction-policy testing.

    Only ``source_ref`` is exercised by `_apply_attribution_policy`;
    other fields satisfy the Pydantic schema's required contract.
    """
    return DecisionMatch(
        decision_id="d1",
        description="test decision",
        status="reflected",
        confidence=1.0,
        source_ref=source_ref,
        code_regions=[],
    )


# ── Redacted-mode positional-cue patterns ────────────────────────────


def test_redacted_mode_redacts_name_after_bullet_separator() -> None:
    inp = "Sprint 14 architecture review · Ian, 2026-03-12"
    out = _apply_attribution_policy([_match(inp)], "redacted")
    assert out[0].source_ref == "Sprint 14 architecture review · <NAME_REDACTED>, <DATE_REDACTED>"


def test_redacted_mode_preserves_platform_tokens() -> None:
    """Platform tokens in context-word position survive: `Linear board issue
    #143 · Bob` redacts only `Bob`."""
    inp = "Linear board issue #143 · Bob"
    out = _apply_attribution_policy([_match(inp)], "redacted")
    assert "Linear" in out[0].source_ref
    assert "Bob" not in out[0].source_ref
    assert "<NAME_REDACTED>" in out[0].source_ref


def test_redacted_mode_redacts_speaker_prefix() -> None:
    inp = "Speaker: Alice Bobson"
    out = _apply_attribution_policy([_match(inp)], "redacted")
    assert "Alice" not in out[0].source_ref
    assert "Bobson" not in out[0].source_ref
    assert "<NAME_REDACTED>" in out[0].source_ref


def test_redacted_mode_redacts_from_prefix() -> None:
    """`From: Charlie\\nBody text` — `Charlie` redacted, `Body` preserved
    (capitalization at start-of-line not after `From:` shouldn't trigger)."""
    inp = "From: Charlie\nBody text"
    out = _apply_attribution_policy([_match(inp)], "redacted")
    assert "Charlie" not in out[0].source_ref
    assert "Body" in out[0].source_ref


def test_redacted_mode_preserves_capitalized_context_words() -> None:
    """`GitHub PR #229 review notes · Eve, 2026-04-10` — only `Eve` and
    the date are redacted; `GitHub`, `PR`, etc. survive."""
    inp = "GitHub PR #229 review notes · Eve, 2026-04-10"
    out = _apply_attribution_policy([_match(inp)], "redacted")
    assert "GitHub" in out[0].source_ref
    assert "PR" in out[0].source_ref
    assert "review" in out[0].source_ref
    assert "notes" in out[0].source_ref
    assert "Eve" not in out[0].source_ref
    assert "<NAME_REDACTED>" in out[0].source_ref
    assert "<DATE_REDACTED>" in out[0].source_ref


def test_redacted_mode_handles_no_attribution_shape() -> None:
    """A source_ref with no positional cues passes through unchanged
    (modulo date redaction, which is unambiguous and doesn't need cues)."""
    inp = "Decision context: implement feature X"
    out = _apply_attribution_policy([_match(inp)], "redacted")
    assert out[0].source_ref == inp


# ── Default flip ──────────────────────────────────────────────────────


def test_default_render_attribution_mode_is_redacted() -> None:
    """Lock the default-flip contract; without the test, a future revert
    could silently regress."""
    assert context._DEFAULT_RENDER_ATTRIBUTION_MODE == "redacted"


def test_setup_wizard_fresh_install_writes_redacted_default(tmp_path: Path) -> None:
    """Functional contract: invoke `_write_collaboration_config` with a
    tmp_path data dir and verify the rendered YAML carries the redacted
    default. Tests the unit's behavior (file-write rendering), not a
    source-file substring presence."""
    from setup_wizard import _write_collaboration_config

    _write_collaboration_config(tmp_path, mode="standard", guided=False, telemetry=False)
    rendered = (tmp_path / ".bicameral" / "config.yaml").read_text(encoding="utf-8")
    assert "render_source_attribution: redacted" in rendered
    assert "render_source_attribution: full" not in rendered


# ── full / hidden modes (regression locks) ───────────────────────────


def test_full_mode_unchanged() -> None:
    inp = "Sprint 14 review · Ian, 2026-03-12"
    out = _apply_attribution_policy([_match(inp)], "full")
    assert out[0].source_ref == inp


def test_hidden_mode_blanks_source_ref() -> None:
    inp = "Sprint 14 review · Ian, 2026-03-12"
    out = _apply_attribution_policy([_match(inp)], "hidden")
    assert out[0].source_ref == ""
