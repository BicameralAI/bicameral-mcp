"""HTML-pattern tests for the dashboard's decision_level surfacing (#76 part 1).

The dashboard render path lives in `assets/dashboard.html` as inline JS, so
these tests assert that the source-of-truth template carries the markup,
classes, and JS branches the runtime relies on. No DOM/Playwright runtime is
booted — the tests are pure string-pattern assertions against the HTML file.
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


def test_unclassified_css_class_defined(html: str) -> None:
    """Amber `.lvl-unclassified` rule sits next to the L1/L2/L3 family."""
    pattern = (
        r"\.lvl-unclassified\s*\{[^}]*"
        r"background:\s*rgba\(249,\s*115,\s*22,\s*0\.15\)[^}]*"
        r"border-color:\s*rgb\(249,\s*115,\s*22\)[^}]*"
        r"color:\s*rgb\(249,\s*115,\s*22\)"
    )
    assert re.search(pattern, html, re.DOTALL), "expected amber .lvl-unclassified rule"


def test_render_branch_handles_null_decision_level(html: str) -> None:
    """`renderDec` must produce an Unclassified label + lvl-unclassified class
    when `decision_level` is falsy."""
    # The literal label string — used both as badge text and as a regression
    # canary against accidental rename.
    assert "'Unclassified'" in html, "expected the literal 'Unclassified' label"
    assert "lvl-unclassified" in html, "expected the lvl-unclassified class token"
    # The rendering branch should fall back to 'unclassified' as the data-level
    # so the filter dropdown's 'unclassified' option keys onto these rows.
    assert "'unclassified'" in html, "expected 'unclassified' data-level fallback"


def test_l1_l2_l3_decisions_unaffected_by_unclassified_branch(html: str) -> None:
    """Pre-existing L1/L2/L3 badge classes survive the patch unchanged."""
    assert re.search(r"\.lvl-l1\s*\{", html), ".lvl-l1 rule must remain"
    assert re.search(r"\.lvl-l2\s*\{", html), ".lvl-l2 rule must remain"
    assert re.search(r"\.lvl-l3\s*\{", html), ".lvl-l3 rule must remain"
    # The level computation continues to use `decision_level || ...depth fallback`.
    assert "d.decision_level ||" in html, "decision_level fallback chain must remain"


def test_decision_row_carries_data_level_attr(html: str) -> None:
    """Each decision row must emit `data-level=\"...\"` for filter targeting,
    and the row must carry the `decision-row` class the filter selects on."""
    assert 'data-level="${dataLevel}"' in html, "decision row must template data-level"
    assert "decision-row" in html, "decision row must carry .decision-row class"


def test_filter_dropdown_present_with_five_options(html: str) -> None:
    """`<select id=\"lvl-filter\">` exists with 5 options keyed to the data-level
    values."""
    assert re.search(
        r'<select\s+id="lvl-filter"\s+onchange="applyLevelFilter\(this\.value\)"',
        html,
    ), "expected #lvl-filter <select> wired to applyLevelFilter"
    for value, label in [
        ("all", "All levels"),
        ("L1", "L1 only"),
        ("L2", "L2 only"),
        ("L3", "L3 only"),
        ("unclassified", "Unclassified only"),
    ]:
        assert f'<option value="{value}">{label}</option>' in html, (
            f"expected filter option {value!r}/{label!r}"
        )


def test_apply_level_filter_function_defined(html: str) -> None:
    """`applyLevelFilter(value)` toggles row visibility based on dataset.level."""
    assert "function applyLevelFilter(value)" in html
    assert ".decision-row" in html
    # Show when filter is 'all' or matches; hide otherwise.
    assert "value === 'all' || value === level" in html
