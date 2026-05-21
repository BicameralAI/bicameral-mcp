"""HTML-pattern tests for #278 Phase 1 — dashboard source view + side-by-side.

The dashboard render path lives in `assets/dashboard-legacy.html` as inline JS
(Dashboard v2 M1 moved this hand-written view there; it is still shipped via
the dashboard server's `/legacy` route), so
these tests assert that the source-of-truth template carries the markup,
classes, and JS branches the runtime relies on. No DOM/Playwright runtime is
booted — the tests are pure string-pattern assertions against the HTML file,
matching the harness pattern established by
`tests/test_dashboard_unclassified_rendering.py`.

Two security disciplines are pinned by these tests:

1. Panel field population uses `.textContent` (not `.innerHTML`) for every
   user-controlled value (`source_type`, `source_ref`, `date`, `quote`).
2. The "Decisions from this source" list uses event delegation reading from
   `data-decision-id`; no inline `onclick` interpolates the decision id or
   summary into an HTML attribute string.
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


# ── Phase A: source-view panel (decision → source) ────────────────────────


def test_source_panel_css_class_has_hidden_default(html: str) -> None:
    """`.src-panel` rule exists with a hidden default state, and a
    `[data-src-panel="open"]` companion rule flips it visible.

    Pins the toggle contract: the JS setter must write 'open' (not some other
    value) to make the panel render.
    """
    assert re.search(
        r"\.src-panel\s*\{[^}]*(display\s*:\s*none|visibility\s*:\s*hidden)",
        html,
        re.DOTALL,
    ), "expected .src-panel default-hidden rule (display:none or visibility:hidden)"
    assert re.search(
        r'\.src-panel\[data-src-panel\s*=\s*"open"\]\s*\{[^}]*'
        r"(display\s*:\s*(block|flex|grid)|visibility\s*:\s*visible)",
        html,
        re.DOTALL,
    ), 'expected .src-panel[data-src-panel="open"] visible rule'


def test_source_panel_markup_and_state_attribute_match_js(html: str) -> None:
    """The panel markup declares `data-src-panel="closed"` AND the JS setter
    references the same attribute name with the matching values.

    A silent rename on either side (markup attribute or JS setter) fails this
    test — that's the point. Tests the toggle behavior, not just markup.
    """
    # Markup canary.
    assert '<div id="src-panel"' in html, 'expected <div id="src-panel" panel container'
    assert 'data-src-panel="closed"' in html, (
        'expected initial state attribute data-src-panel="closed"'
    )
    # JS setter canary — both 'open' and 'closed' must appear inside the
    # source-view function bodies.
    open_fn = _extract_function_body(html, "openSourceView")
    close_fn = _extract_function_body(html, "closeSourceView")
    assert re.search(
        r"(setAttribute\(\s*['\"]data-src-panel['\"]\s*,\s*['\"]open['\"]"
        r"|dataset\.srcPanel\s*=\s*['\"]open['\"])",
        open_fn,
    ), "openSourceView must set data-src-panel to 'open' (matching the CSS rule)"
    assert re.search(
        r"(setAttribute\(\s*['\"]data-src-panel['\"]\s*,\s*['\"]closed['\"]"
        r"|dataset\.srcPanel\s*=\s*['\"]closed['\"])",
        close_fn,
    ), "closeSourceView must set data-src-panel to 'closed'"


def test_view_source_button_injected_in_render_detail_sources(html: str) -> None:
    """`renderDetailSources` emits a `class="src-view-btn"` button per source
    that calls `openSourceView(featureIdx, decIdx, srcIdx)` with three numeric
    indices (safe to interpolate; not user data)."""
    body = _extract_function_body(html, "renderDetailSources")
    assert 'class="src-view-btn"' in body, (
        'expected src-view-btn class on the new "View source" button'
    )
    assert re.search(
        r'onclick\s*=\s*"openSourceView\(\$\{[^}]+\},\s*\$\{[^}]+\},\s*\$\{[^}]+\}\)"',
        body,
    ), (
        "expected openSourceView(featureIdx, decIdx, srcIdx) three-arg call "
        "inside the new button's onclick"
    )


def test_open_source_view_function_defined(html: str) -> None:
    """`openSourceView(featureIdx, decIdx, srcIdx)` is defined and addresses
    the response via the three-index path `_currentData.features[*].decisions[*].sources[*]`."""
    assert "function openSourceView(featureIdx, decIdx, srcIdx)" in html, (
        "expected openSourceView function with three index args"
    )
    body = _extract_function_body(html, "openSourceView")
    # The body must look up the source via the three-index path.
    assert re.search(
        r"_currentData\.features\[\s*featureIdx\s*\]\.decisions\[\s*decIdx\s*\]"
        r"\.sources\[\s*srcIdx\s*\]",
        body,
    ), (
        "openSourceView must look up "
        "_currentData.features[featureIdx].decisions[decIdx].sources[srcIdx]"
    )


def test_open_source_view_uses_text_content_for_user_values(html: str) -> None:
    """XSS discipline #1: every user-controlled source field is written to
    the DOM via `.textContent =`, never `.innerHTML =`.

    Regression canary against future edits that swap textContent for innerHTML
    on a user-controlled value, which would re-introduce the dashboard XSS
    surface.
    """
    body = _extract_function_body(html, "openSourceView")
    for field in ("source_type", "source_ref", "date", "quote"):
        assert re.search(
            rf"\.textContent\s*=\s*[^;]*\bsrc\.{field}\b",
            body,
        ), f"openSourceView must populate {field} via .textContent (not .innerHTML)"
    # And no innerHTML write of any src.* field inside the function body.
    assert not re.search(
        r"\.innerHTML\s*=\s*[^;]*\bsrc\.(source_type|source_ref|date|quote)\b",
        body,
    ), "openSourceView must not write any src.* user value via .innerHTML"


def test_panel_close_handler_defined(html: str) -> None:
    """`closeSourceView` exists and is wired up to a close button in the panel
    markup (Ghost UI canary — button must have a handler)."""
    assert "function closeSourceView()" in html, "expected closeSourceView function"
    # The panel markup carries a close button bound to the handler.
    assert re.search(
        r'<[^>]*class\s*=\s*"[^"]*src-panel-close[^"]*"[^>]*onclick\s*=\s*"closeSourceView',
        html,
    ), "expected a .src-panel-close button with onclick=closeSourceView()"


# ── Phase B: reverse navigation (source → decisions) ──────────────────────


def test_source_signature_function_defined(html: str) -> None:
    """`sourceSignature(src)` concatenates the four canonical fields with the
    quote prefix at exactly 40 chars (signature stability is a correctness
    property; the slice constant is pinned)."""
    assert "function sourceSignature(src)" in html
    body = _extract_function_body(html, "sourceSignature")
    for field in ("source_type", "source_ref", "date", "quote"):
        assert f"src.{field}" in body, f"sourceSignature must read src.{field}"
    assert re.search(
        r"\.slice\(\s*0\s*,\s*40\s*\)",
        body,
    ), "sourceSignature must take .slice(0, 40) of the quote (signature stability)"


def test_build_source_index_function_defined(html: str) -> None:
    """`buildSourceIndex(features)` walks features → decisions → sources and
    returns a `Map` (or plain object) keyed by signature."""
    assert "function buildSourceIndex(features)" in html
    body = _extract_function_body(html, "buildSourceIndex")
    # Must walk the three-level structure.
    assert "decisions" in body, "buildSourceIndex must traverse feature.decisions"
    assert "sources" in body, "buildSourceIndex must traverse decision.sources"
    assert "sourceSignature" in body, (
        "buildSourceIndex must call sourceSignature(src) to key the map"
    )


def test_open_source_view_renders_related_decisions_with_event_delegation(
    html: str,
) -> None:
    """Phase B extension to openSourceView: writes a `.src-panel-related` block
    where each link uses `data-decision-id` (no inline onclick) and the link
    text is escaped via `esc(...)`."""
    body = _extract_function_body(html, "openSourceView")
    assert "src-panel-related" in body, "openSourceView must render a .src-panel-related list"
    # XSS discipline #2: the related-list link must not use inline onclick
    # interpolating the decision id.
    assert "jumpToDecision('${" not in body, (
        "related-list link must not interpolate id into onclick — use data-decision-id"
    )
    assert 'onclick="jumpToDecision(' not in body, (
        "related-list must use event delegation, not inline onclick"
    )
    # The data-decision-id attribute carries the id (escaped).
    assert re.search(
        r'data-decision-id\s*=\s*"\$\{esc\(',
        body,
    ), 'related-list links must carry data-decision-id="${esc(...)}"'


def test_related_list_delegated_handler_defined(html: str) -> None:
    """A single delegated click handler reads `data-decision-id` and calls
    `jumpToDecision`. Pins XSS discipline #2.
    """
    # The delegated handler reads dataset.decisionId after closest() match.
    # Allow either bare `[data-decision-id]` selector or a scoped form like
    # `.src-panel-related [data-decision-id]` — both honor discipline #2.
    assert re.search(
        r"closest\(\s*['\"][^'\"]*\[data-decision-id\][^'\"]*['\"]",
        html,
    ), "expected event delegation via closest(...) on a selector containing [data-decision-id]"
    assert (
        re.search(
            r"dataset\.decisionId\s*\)?\s*\)?\s*;?\s*$"
            r"|jumpToDecision\(\s*[\w.]*\.dataset\.decisionId",
            html,
            re.MULTILINE,
        )
        or "dataset.decisionId" in html
    ), "delegated handler must read dataset.decisionId and call jumpToDecision"


def test_jump_to_decision_function_defined(html: str) -> None:
    """`jumpToDecision(id)` uses `document.getElementById(id)` (DOM API, no
    HTML concatenation), adds the `.open` class, and calls `scrollIntoView`."""
    assert "function jumpToDecision(" in html, "expected jumpToDecision function"
    body = _extract_function_body(html, "jumpToDecision")
    assert "document.getElementById(" in body, (
        "jumpToDecision must look up via document.getElementById "
        "(not string concatenation into HTML)"
    )
    assert "classList.add('open')" in body or 'classList.add("open")' in body, (
        "jumpToDecision must add the .open class to the target row"
    )
    assert "scrollIntoView" in body, "jumpToDecision must call scrollIntoView"


def test_render_builds_source_index(html: str) -> None:
    """The existing `render(data)` is modified to: (a) cache `data` in
    `_currentData`, (b) rebuild `_sourceIndex` on every call so SSE updates
    keep the index current."""
    body = _extract_function_body(html, "render")
    assert re.search(r"_currentData\s*=\s*data\b", body), (
        "render(data) must assign _currentData = data so openSourceView can resolve indices later"
    )
    assert re.search(
        r"_sourceIndex\s*=\s*buildSourceIndex\(\s*(features|data\.features\s*\|\|\s*\[\])",
        body,
    ), "render(data) must rebuild _sourceIndex = buildSourceIndex(features) on every call"


# ── helpers ───────────────────────────────────────────────────────────────


def _extract_function_body(html: str, fn_name: str) -> str:
    """Extract the body of a top-level JS function by brace-matching.

    The dashboard's inline JS uses `function name(args) { ... }` form
    consistently; this helper locates the opening brace after `function name`
    and returns the substring up to the matching close brace. Falls back to a
    100-line slice on parse failure (still useful as a regex haystack).
    """
    match = re.search(rf"function\s+{re.escape(fn_name)}\s*\([^)]*\)\s*\{{", html)
    if not match:
        raise AssertionError(f"function {fn_name} not found in dashboard-legacy.html")
    start = match.end() - 1  # position of the opening brace
    depth = 0
    for i in range(start, len(html)):
        ch = html[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return html[start : i + 1]
    # Unbalanced braces — return a generous slice so the test still has
    # something to grep against rather than crashing.
    return html[start : start + 4000]
