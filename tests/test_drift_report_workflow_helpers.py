"""Issue #49 Phase 2 — sticky-comment poster helpers.

Pure-function tests on the comment-finder helper used by
``.github/scripts/post_drift_comment.py`` to decide between PATCH
(existing sticky) and POST (new comment). All HTTP is mocked; tests
do not touch the real GitHub API.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

# Load the script as a module so we can test internal helpers without
# requiring it to be a proper Python package (it's CI-only tooling).
_SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent / ".github" / "scripts" / "post_drift_comment.py"
)
_SPEC = importlib.util.spec_from_file_location(
    "post_drift_comment",
    _SCRIPT_PATH,
)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules["post_drift_comment"] = _MODULE
_SPEC.loader.exec_module(_MODULE)

_find_existing_comment = _MODULE._find_existing_comment
_MARKER = "<!-- bicameral-drift-report -->"


def test_comment_finder_returns_none_when_no_match() -> None:
    """When no comment carries the marker, the finder returns None
    so the poster knows to POST a new one."""
    comments = [
        {"id": 100, "body": "## Plain comment\nNothing here."},
        {"id": 101, "body": "Another comment"},
    ]
    assert _find_existing_comment(comments) is None


def test_comment_finder_returns_id_when_match() -> None:
    """When a comment carries the marker, the finder returns its ID
    so the poster can PATCH it."""
    comments = [
        {"id": 100, "body": "## Other comment"},
        {"id": 101, "body": f"{_MARKER}\n## Bicameral drift report"},
    ]
    assert _find_existing_comment(comments) == 101


def test_comment_finder_returns_first_match_when_duplicates() -> None:
    """Defensive: if duplicates exist (shouldn't, but might due to a
    racing PR run), use the oldest (lowest ID) so the same sticky is
    consistently updated."""
    comments = [
        {"id": 200, "body": f"{_MARKER}\n## Older sticky"},
        {"id": 100, "body": f"{_MARKER}\n## Even older sticky"},
        {"id": 300, "body": f"{_MARKER}\n## Newest sticky"},
    ]
    assert _find_existing_comment(comments) == 100


def test_comment_finder_handles_empty_list() -> None:
    """Brand-new PR with zero comments — finder returns None."""
    assert _find_existing_comment([]) is None
