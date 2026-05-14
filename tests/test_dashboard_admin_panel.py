"""Phase 3B — static-HTML pattern tests for the dashboard admin SurrealQL panel.

Mirrors the harness from Phase 1 + Phase 2 dashboard tests: pure string
assertions against assets/dashboard.html. The panel is off-by-default at
the server level (env flag); these tests verify the UI two-step toggle +
XSS discipline carried from prior phases.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

DASHBOARD_HTML = Path(__file__).resolve().parent.parent / "assets" / "dashboard.html"


@pytest.fixture(scope="module")
def html() -> str:
    assert DASHBOARD_HTML.exists(), f"missing dashboard template at {DASHBOARD_HTML}"
    return DASHBOARD_HTML.read_text(encoding="utf-8")


def _extract_function_body(html: str, fn_name: str) -> str:
    match = re.search(rf"function\s+{re.escape(fn_name)}\s*\([^)]*\)\s*\{{", html)
    if not match:
        raise AssertionError(f"function {fn_name} not found in dashboard.html")
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


# ── Panel structure ───────────────────────────────────────────────────────


def test_admin_panel_container_present_and_default_closed(html: str) -> None:
    """The panel container exists with data-state='closed' as the initial
    state, and a CSS rule keys visibility on the attribute."""
    assert re.search(
        r'<[^>]*id\s*=\s*"adm-panel"[^>]*data-state\s*=\s*"closed"',
        html,
    ), 'expected <... id="adm-panel" data-state="closed">'
    assert re.search(
        r'#adm-panel\[data-state\s*=\s*"open"\]\s*\{[^}]*'
        r"(display\s*:\s*(block|flex|grid)|visibility\s*:\s*visible)",
        html,
        re.DOTALL,
    ), 'expected #adm-panel[data-state="open"] visible CSS rule'


def test_admin_panel_advanced_toggle_calls_toggleAdvancedPanel(html: str) -> None:
    """The advanced toggle is wired to toggleAdvancedPanel()."""
    assert "toggleAdvancedPanel" in html, "expected toggleAdvancedPanel function"
    # The toggle UI element calls it
    assert re.search(
        r'onclick\s*=\s*"toggleAdvancedPanel\(\)"|onchange\s*=\s*"toggleAdvancedPanel\(\)"',
        html,
    ), "expected the advanced toggle to call toggleAdvancedPanel()"


def test_admin_panel_query_textarea_and_execute_button(html: str) -> None:
    assert re.search(r'<textarea[^>]*id\s*=\s*"adm-sql"', html), (
        "expected <textarea id='adm-sql'> for the SurrealQL input"
    )
    assert re.search(
        r'<button[^>]*onclick\s*=\s*"runAdminQuery\(\)"',
        html,
    ), "expected an Execute button bound to runAdminQuery()"


def test_admin_panel_quickref_dropdown_with_starter_queries(html: str) -> None:
    """A select#adm-quickref carries at least three starter queries from
    the plan's curated list."""
    assert re.search(r'<select[^>]*id\s*=\s*"adm-quickref"', html), (
        "expected <select id='adm-quickref'> dropdown"
    )
    for needle in (
        "SELECT * FROM decision",
        "SELECT count() FROM decision",
        "INFO FOR DB",
    ):
        assert needle in html, f"expected quickref to include {needle!r}"


# ── Write-mode gating ─────────────────────────────────────────────────────


def test_admin_panel_writes_toggle_default_off(html: str) -> None:
    """The write-mode toggle starts disabled (operator must complete typed
    confirmation to enable). Pins by asserting the global state variable
    initializes to false in the JS."""
    assert re.search(r"let\s+_writeModeEnabled\s*=\s*false", html), (
        "expected _writeModeEnabled to initialize to false"
    )


def test_admin_panel_write_mode_requires_typed_confirmation(html: str) -> None:
    """confirmWriteRisk() must check that an input value equals the literal
    'I accept the risk' before flipping _writeModeEnabled to true."""
    assert "confirmWriteRisk" in html, "expected confirmWriteRisk function"
    body = _extract_function_body(html, "confirmWriteRisk")
    # The magic string must appear as a literal in the comparison
    assert "I accept the risk" in body, (
        "confirmWriteRisk must check for the literal 'I accept the risk' phrase"
    )
    # And the body must set _writeModeEnabled = true under the comparison
    assert "_writeModeEnabled" in body and "true" in body


def test_admin_panel_warning_banner_keyed_on_write_mode(html: str) -> None:
    """The warning banner CSS shows when write mode is active."""
    assert "adm-write-warn" in html, "expected .adm-write-warn warning banner class"


# ── runAdminQuery + result rendering ──────────────────────────────────────


def test_admin_panel_runAdminQuery_posts_to_admin_endpoint(html: str) -> None:
    body = _extract_function_body(html, "runAdminQuery")
    assert "fetch(" in body, "runAdminQuery must use fetch()"
    assert "'/admin/query'" in body or '"/admin/query"' in body, (
        "runAdminQuery must POST to /admin/query"
    )
    assert "POST" in body, "runAdminQuery must use POST method"


def test_admin_panel_runAdminQuery_includes_mode_based_on_write_toggle(
    html: str,
) -> None:
    body = _extract_function_body(html, "runAdminQuery")
    # The function must read _writeModeEnabled to pick mode
    assert "_writeModeEnabled" in body, (
        "runAdminQuery must consult _writeModeEnabled when choosing mode"
    )


def test_admin_panel_results_use_text_content_xss_canary(html: str) -> None:
    """renderAdminResult writes rows via .textContent — never .innerHTML.
    A row containing <script> must not be parsed as HTML."""
    body = _extract_function_body(html, "renderAdminResult")
    assert ".textContent" in body, (
        "renderAdminResult must write rows via .textContent (XSS discipline)"
    )
    # And must NOT write user-controlled row data via .innerHTML
    assert not re.search(
        r"\.innerHTML\s*=\s*[^;]*\b(rows|row|JSON\.stringify)\b",
        body,
    ), "renderAdminResult must not write user-row data via .innerHTML"


def test_admin_panel_results_render_mode_and_elapsed(html: str) -> None:
    body = _extract_function_body(html, "renderAdminResult")
    # The render function reads response.mode and response.elapsed_ms
    assert ".mode" in body and ".elapsed_ms" in body, (
        "renderAdminResult must surface response.mode and response.elapsed_ms"
    )
    # The DOM contains explicit slots for these
    assert re.search(r'id\s*=\s*"adm-result-mode"', html), (
        "expected <... id='adm-result-mode'> slot in the panel"
    )
    assert re.search(r'id\s*=\s*"adm-result-elapsed"', html), (
        "expected <... id='adm-result-elapsed'> slot in the panel"
    )
