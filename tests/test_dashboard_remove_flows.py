"""HTML-pattern tests for #278 Phase 2 — dashboard remove flows.

Matches the harness pattern of test_dashboard_unclassified_rendering.py and
test_dashboard_source_view.py: pure string assertions against
assets/dashboard-legacy.html (Dashboard v2 M1 moved this hand-written view
there; still shipped via the dashboard server's `/legacy` route).

Pins:
  1. "Remove decision" button in renderDec body (gated by signoff.state).
  2. "Remove source" button in the Phase 1 source-view panel.
  3. Two confirmation modals (remove-decision, remove-source) with markup-vs-
     JS state-attribute correlation (same dual-assertion pattern as Phase 1).
  4. XSS discipline: modal user-facing fields written via .textContent.
  5. MCP-call payload built via JSON.stringify (NOT raw template interpolation
     of server data) and copied via navigator.clipboard.
  6. Dashboard does NOT invoke writes directly — the modal's "confirm"
     button writes the canonical tool-call payload into a code block for the
     operator to run via their MCP-connected agent.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

DASHBOARD_HTML = Path(__file__).resolve().parent.parent / "assets" / "dashboard-legacy.html"


@pytest.fixture(scope="module")
def html() -> str:
    assert DASHBOARD_HTML.exists(), f"missing dashboard template at {DASHBOARD_HTML}"
    return DASHBOARD_HTML.read_text(encoding="utf-8")


# ── helpers (mirror test_dashboard_source_view.py) ────────────────────────


def _extract_function_body(html: str, fn_name: str) -> str:
    match = re.search(rf"function\s+{re.escape(fn_name)}\s*\([^)]*\)\s*\{{", html)
    if not match:
        raise AssertionError(f"function {fn_name} not found in dashboard-legacy.html")
    start = match.end() - 1
    depth = 0
    for i in range(start, len(html)):
        ch = html[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return html[start : i + 1]
    return html[start : start + 4000]


# ── Modals: markup + state-attribute correlation ──────────────────────────


def test_remove_decision_modal_markup_present(html: str) -> None:
    """Modal container has id, initial closed state, and matching CSS rule."""
    assert '<div id="rm-dec-modal"' in html, "expected remove-decision modal container"
    assert 'data-modal="closed"' in html, 'expected data-modal="closed" initial state'
    # CSS rule controlling visibility based on the attribute
    assert re.search(
        r'\.rm-modal\[data-modal\s*=\s*"open"\]\s*\{[^}]*'
        r"(display\s*:\s*(block|flex|grid)|visibility\s*:\s*visible)",
        html,
        re.DOTALL,
    ), 'expected .rm-modal[data-modal="open"] visible CSS rule'


def test_remove_source_modal_markup_present(html: str) -> None:
    """Source modal carries cascade-list block + matches state pattern."""
    assert '<div id="rm-src-modal"' in html, "expected remove-source modal container"
    assert 'class="cascade-list"' in html or "cascade-list" in html, (
        "remove-source modal must include a .cascade-list block to render the cascade"
    )


def test_remove_modal_state_attribute_match_js(html: str) -> None:
    """Modal open/close functions must reference the same `data-modal`
    attribute and the canonical "open"/"closed" string values that the CSS
    rule keys on. A silent rename on either side fails this test."""
    open_dec = _extract_function_body(html, "openRemoveDecisionModal")
    open_src = _extract_function_body(html, "openRemoveSourceModal")
    close_fn = _extract_function_body(html, "closeRemoveModal")

    for body in (open_dec, open_src):
        assert re.search(
            r"(setAttribute\(\s*['\"]data-modal['\"]\s*,\s*['\"]open['\"]"
            r"|dataset\.modal\s*=\s*['\"]open['\"])",
            body,
        ), "modal open function must set data-modal='open'"

    assert re.search(
        r"(setAttribute\(\s*['\"]data-modal['\"]\s*,\s*['\"]closed['\"]"
        r"|dataset\.modal\s*=\s*['\"]closed['\"])",
        close_fn,
    ), "closeRemoveModal must set data-modal='closed'"


# ── Buttons ───────────────────────────────────────────────────────────────


def test_remove_decision_button_in_render_dec(html: str) -> None:
    """The decision row's expanded body carries a 'rm-dec-btn' button bound
    to openRemoveDecisionModal. Gated so already-removed decisions render
    the button as disabled."""
    render_dec_body = _extract_function_body(html, "renderDec")
    assert "rm-dec-btn" in render_dec_body, "renderDec must include a remove-decision button"
    # Button invokes the modal opener
    assert re.search(
        r"openRemoveDecisionModal\(",
        render_dec_body,
    ), "remove-decision button must call openRemoveDecisionModal(...)"


def test_remove_decision_button_disabled_when_already_removed(html: str) -> None:
    """A decision with signoff.state==='removed' must render the button as
    disabled (or hidden) — the operation is a no-op and the UI should reflect
    that."""
    render_dec_body = _extract_function_body(html, "renderDec")
    # Look for some form of disabled gating tied to the removed state
    assert re.search(
        r"signoff_state\s*===?\s*['\"]removed['\"]|signoff\?\.state\s*===?\s*['\"]removed['\"]",
        render_dec_body,
    ), (
        "renderDec must gate the remove button on whether the decision is "
        "already removed (signoff.state === 'removed')"
    )


def test_remove_source_button_in_src_panel(html: str) -> None:
    """The Phase 1 source-view panel carries a remove-source button bound to
    openRemoveSourceModal. Phase 2's button rides on top of Phase 1's panel."""
    # Look for the button class anywhere in the file; the panel markup is
    # static so a global match is sufficient.
    assert "rm-src-btn" in html, "expected rm-src-btn class on the remove-source button"
    assert re.search(
        r'class\s*=\s*"[^"]*rm-src-btn[^"]*"[^>]*onclick\s*=\s*"openRemoveSourceModal',
        html,
    ), "remove-source button must be wired to openRemoveSourceModal(...)"


# ── XSS discipline carries from Phase 1 ───────────────────────────────────


def test_remove_modals_use_text_content_for_user_values(html: str) -> None:
    """XSS discipline: modal field population uses .textContent for any
    user-controlled value (decision summary, source quote, cascaded decision
    summaries). Phase 1's discipline carries verbatim."""
    open_dec = _extract_function_body(html, "openRemoveDecisionModal")
    open_src = _extract_function_body(html, "openRemoveSourceModal")

    # Both modal openers must populate at least one user value via .textContent
    assert ".textContent" in open_dec, (
        "openRemoveDecisionModal must populate user-facing fields via .textContent"
    )
    assert ".textContent" in open_src, (
        "openRemoveSourceModal must populate user-facing fields via .textContent"
    )
    # And must NOT write user-controlled fields via .innerHTML
    for body, name in [(open_dec, "openRemoveDecisionModal"), (open_src, "openRemoveSourceModal")]:
        assert not re.search(
            r"\.innerHTML\s*=\s*[^;]*\b(summary|quote|source_ref|source_type|cascade)\b",
            body,
        ), f"{name} must not write user-controlled fields via .innerHTML"


# ── Architectural boundary: dashboard does NOT call MCP tools itself ──────


def test_remove_modals_emit_mcp_tool_call_payload_via_json_stringify(html: str) -> None:
    """On confirm, modals populate a `<code class="mcp-call">` block with
    the canonical tool-call JSON built via JSON.stringify (NOT raw template
    interpolation). The operator copies this block and runs it in their
    MCP-connected agent.

    This pins the architectural boundary: dashboard server is read-only;
    write tools go through the agent layer.
    """
    assert "JSON.stringify" in html, (
        "modal confirm must build the MCP tool-call payload via JSON.stringify"
    )
    assert re.search(
        r'class\s*=\s*"[^"]*mcp-call[^"]*"',
        html,
    ), "expected a <code class='mcp-call'> block to hold the canonical tool-call payload"
    # The buildMcpCall helper (or equivalent) must write into the mcp-call
    # block via .textContent so the JSON payload is not interpreted as HTML.
    build = _extract_function_body(html, "buildMcpCall")
    assert ".textContent" in build, (
        "buildMcpCall must write the JSON.stringify result via .textContent, "
        "not innerHTML (XSS canary)"
    )


def test_copy_mcp_call_uses_clipboard_api(html: str) -> None:
    """Copy-to-clipboard uses navigator.clipboard.writeText, not document.execCommand
    or other deprecated approaches. Read-only access pattern."""
    body = _extract_function_body(html, "copyMcpCall")
    assert "navigator.clipboard.writeText" in body, (
        "copyMcpCall must use navigator.clipboard.writeText"
    )


# ── Reason capture (Phase 2 audit obligation) ─────────────────────────────


def test_remove_modals_capture_reason_via_textarea(html: str) -> None:
    """Each modal includes a `<textarea>` for the operator's reason input.
    The reason is mandatory at the handler layer; the dashboard makes that
    obvious by requiring a textarea entry."""
    # Both modals carry a reason textarea
    assert re.search(
        r"<textarea[^>]*id\s*=\s*\"rm-dec-reason\"",
        html,
    ), "remove-decision modal must include a reason textarea"
    assert re.search(
        r"<textarea[^>]*id\s*=\s*\"rm-src-reason\"",
        html,
    ), "remove-source modal must include a reason textarea"
