"""Tests for ``pulse.render.render_pulse_text`` (#437 Phase 2).

``render_pulse_text`` is a **pure function** over a ``ProjectPulseSummary`` —
no ledger, no IO, no collaborator we ship to users. Per CLAUDE.md's testing
guidance ("solitary is correct for pure helpers"), these tests build
``ProjectPulseSummary`` instances by hand and assert on the rendered string.

The prompt-injection isolation in ``render_pulse_text`` (data-framing line,
control-char stripping, per-field length caps) is load-bearing — the brief is
embedded in a Claude SessionStart hook envelope — so it is pinned here.
"""

from __future__ import annotations

from pulse.render import (
    _ALL_CLEAR_BODY,
    _MAX_SUMMARY_LEN,
    _PREAMBLE,
    render_pulse_text,
)
from pulse.summary import (
    Health,
    LearnedItem,
    NeedsAttentionItem,
    ProjectPulseSummary,
)


def _all_clear_summary() -> ProjectPulseSummary:
    """An all-clear summary — no drift, no pending, nothing learned."""
    return ProjectPulseSummary(
        health=Health(decisions_reflected=5, last_sync="2026-05-21T09:00:00"),
        needs_attention=[],
        recently_learned=[],
        suggested_next_move="Project memory is current — no drift, no pending signoffs.",
        is_all_clear=True,
    )


def _busy_summary() -> ProjectPulseSummary:
    """A not-all-clear summary with needs-attention + recently-learned items."""
    return ProjectPulseSummary(
        health=Health(
            decisions_reflected=12,
            decisions_drifted=1,
            decisions_pending=2,
            decisions_ungrounded=0,
            drifted_regions=1,
            last_sync="2026-05-21T09:00:00",
        ),
        needs_attention=[
            NeedsAttentionItem(
                kind="awaiting_ratification",
                decision_id="decision:abc",
                summary="Adopt feature flags for checkout",
                signer="silong",
            ),
        ],
        recently_learned=[
            LearnedItem(
                decision_id="decision:def",
                summary="Use BM25 for code search",
                source_type="meeting",
                source_ref="Sprint Planning",
                date="2026-05-20T10:00:00",
            ),
        ],
        suggested_next_move="Review 1 decision awaiting ratification.",
        is_all_clear=False,
    )


# ── data-framing line (prompt-injection isolation) ───────────────────────────


def test_render_leads_with_data_framing_line() -> None:
    """Discipline #2: the brief begins with a data-framing line so a
    downstream LLM treats the body as read-only context, not instructions."""
    out = render_pulse_text(_all_clear_summary())
    assert out.splitlines()[0] == _PREAMBLE
    assert "read-only data" in out
    assert "not instructions" in out


# ── all-clear (#437: all-clear is a useful, friendly result) ─────────────────


def test_render_all_clear_emits_friendly_message() -> None:
    """An all-clear summary renders the explicit friendly message, not an
    empty or silent body."""
    out = render_pulse_text(_all_clear_summary())
    assert _ALL_CLEAR_BODY in out
    assert "Bicameral checked project memory." in out
    assert "memory is current" in out
    # All-clear collapses the body — no section headers.
    assert "Needs Attention" not in out
    assert "Recently Learned" not in out


# ── non-empty sections ───────────────────────────────────────────────────────


def test_render_busy_summary_includes_all_sections() -> None:
    """A not-all-clear summary renders all four sections + their content."""
    out = render_pulse_text(_busy_summary())
    assert "Health" in out
    assert "12 reflected decisions" in out
    assert "Needs Attention" in out
    assert "decision:abc" in out
    assert "Adopt feature flags for checkout" in out
    assert "Recently Learned" in out
    assert "decision:def" in out
    assert "Use BM25 for code search" in out
    assert "meeting: Sprint Planning" in out
    assert "Suggested Next Move" in out
    assert "Review 1 decision awaiting ratification." in out


def test_render_empty_needs_attention_is_explicit_not_silent() -> None:
    """A not-all-clear summary (drift present) with no pending ratifications
    still renders an explicit Needs Attention line, never a blank section."""
    summary = ProjectPulseSummary(
        health=Health(drifted_regions=1),
        needs_attention=[],
        recently_learned=[],
        suggested_next_move="Review 1 drifted region before further edits.",
        is_all_clear=False,
    )
    out = render_pulse_text(summary)
    assert "Needs Attention" in out
    assert "Nothing awaiting attention." in out
    assert "Recently Learned" in out
    assert "Nothing learned recently." in out


# ── control-char stripping (prompt-injection isolation) ──────────────────────


def test_render_strips_control_chars_in_user_text() -> None:
    """Control characters in user-sourced fields are stripped from output."""
    summary = ProjectPulseSummary(
        health=Health(),
        needs_attention=[
            NeedsAttentionItem(
                kind="awaiting_ratification",
                decision_id="decision:x",
                summary="before\x00after\x07end",
            ),
        ],
        recently_learned=[
            LearnedItem(
                decision_id="decision:y",
                summary="learned\x1bthing",
                source_ref="ref\x08value",
            ),
        ],
        suggested_next_move="ladder move",
        is_all_clear=False,
    )
    out = render_pulse_text(summary)
    assert "\x00" not in out
    assert "\x07" not in out
    assert "\x1b" not in out
    assert "\x08" not in out
    assert "beforeafterend" in out
    assert "learnedthing" in out
    assert "refvalue" in out


# ── per-field length caps (prompt-injection isolation) ───────────────────────


def test_render_caps_long_summary() -> None:
    """A summary longer than the per-field cap is truncated with an ellipsis,
    bounding the injection mass any single decision can contribute."""
    long = "x" * 5000
    summary = ProjectPulseSummary(
        health=Health(),
        needs_attention=[
            NeedsAttentionItem(
                kind="awaiting_ratification",
                decision_id="decision:x",
                summary=long,
            ),
        ],
        recently_learned=[],
        suggested_next_move="move",
        is_all_clear=False,
    )
    out = render_pulse_text(summary)
    # The 5000-char run is gone; only the capped run remains.
    assert "x" * 5000 not in out
    assert "…" in out
    # The needs-attention line carries at most the per-field cap of x's.
    attention_line = next(ln for ln in out.splitlines() if "decision:x" in ln)
    assert attention_line.count("x") <= _MAX_SUMMARY_LEN


# ── team-sync footer ─────────────────────────────────────────────────────────


def test_render_appends_team_sync_footer_when_provided() -> None:
    """When ``team_sync`` is supplied, a one-line team-sync footer appears."""
    out = render_pulse_text(
        _all_clear_summary(),
        team_sync={"peer_files_pulled": 3, "my_file_pushed": True},
    )
    assert "Team Sync" in out
    assert "peer files pulled: 3" in out
    assert "my file pushed: yes" in out


def test_render_omits_team_sync_footer_when_none() -> None:
    """Solo mode (no ``team_sync``) renders no team-sync footer."""
    out = render_pulse_text(_all_clear_summary(), team_sync=None)
    assert "Team Sync" not in out


def test_render_team_sync_footer_handles_zero_and_false() -> None:
    """Zero peers + not-pushed renders cleanly, not 'None'."""
    out = render_pulse_text(
        _all_clear_summary(),
        team_sync={"peer_files_pulled": 0, "my_file_pushed": False},
    )
    assert "peer files pulled: 0" in out
    assert "my file pushed: no" in out


# ── signer fallback (no raw email leak) ──────────────────────────────────────


def test_render_signer_fallback_local_part_only_default() -> None:
    """The default fallback strips the email domain — no mailable address."""
    summary = ProjectPulseSummary(
        health=Health(),
        needs_attention=[
            NeedsAttentionItem(
                kind="awaiting_ratification",
                decision_id="decision:x",
                summary="needs signoff",
                signer="kim@example.com",
            ),
        ],
        recently_learned=[],
        suggested_next_move="move",
        is_all_clear=False,
    )
    out = render_pulse_text(summary)
    assert "kim@example.com" not in out
    assert "kim" in out


def test_render_signer_fallback_redact() -> None:
    """``redact`` mode emits ``<REDACTED>`` for an email signer."""
    summary = ProjectPulseSummary(
        health=Health(),
        needs_attention=[
            NeedsAttentionItem(
                kind="awaiting_ratification",
                decision_id="decision:x",
                summary="needs signoff",
                signer="kim@example.com",
            ),
        ],
        recently_learned=[],
        suggested_next_move="move",
        is_all_clear=False,
    )
    out = render_pulse_text(summary, signer_fallback_mode="redact")
    assert "kim@example.com" not in out
    assert "<REDACTED>" in out


def test_render_non_email_signer_passes_through() -> None:
    """A non-email signer (a bare name) is rendered as-is under any mode."""
    summary = ProjectPulseSummary(
        health=Health(),
        needs_attention=[
            NeedsAttentionItem(
                kind="awaiting_ratification",
                decision_id="decision:x",
                summary="needs signoff",
                signer="silong",
            ),
        ],
        recently_learned=[],
        suggested_next_move="move",
        is_all_clear=False,
    )
    out = render_pulse_text(summary)
    assert "signer: silong" in out


# ── newline termination ──────────────────────────────────────────────────────


def test_render_output_is_newline_terminated() -> None:
    """The rendered brief ends with a trailing newline."""
    assert render_pulse_text(_all_clear_summary()).endswith("\n")
