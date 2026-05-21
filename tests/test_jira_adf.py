"""Solitary unit tests for the ADF -> plain-text flattener (#337 Phase A).

``flatten_adf`` is a pure function — no I/O, no collaborators — so
solitary tests are correct here (per CLAUDE.md "solitary is correct for
pure helpers"). There is nothing to seam: the inputs are plain dicts and
the output is a string.

Coverage: malformed roots (None, non-dict, non-doc, wrong version),
every leaf inline node, nested lists, headings, code blocks, marks
ignored, hardBreak, unknown node recursion, and a realistic multi-block
issue description fixture.
"""

from __future__ import annotations

import pytest

from sources.jira.adf import flatten_adf


def _doc(*content: dict) -> dict:
    """Wrap block nodes in a valid ADF ``doc`` root."""
    return {"version": 1, "type": "doc", "content": list(content)}


def _para(*inline: dict) -> dict:
    return {"type": "paragraph", "content": list(inline)}


def _text(s: str, marks: list[dict] | None = None) -> dict:
    node: dict = {"type": "text", "text": s}
    if marks is not None:
        node["marks"] = marks
    return node


# ── Malformed / empty roots → "" ────────────────────────────────────────────


@pytest.mark.parametrize(
    "bad",
    [
        None,
        {},
        [],
        "a string",
        42,
        {"type": "paragraph", "content": []},  # non-doc root
        {"version": 1, "type": "panel", "content": []},  # non-doc root
        {"version": 2, "type": "doc", "content": []},  # wrong version
        {"type": "doc", "content": []},  # missing version
        {"version": "1", "type": "doc", "content": []},  # version is a string
    ],
)
def test_flatten_adf_returns_empty_for_malformed_root(bad):
    assert flatten_adf(bad) == ""


def test_flatten_adf_empty_doc():
    assert flatten_adf(_doc()) == ""


def test_flatten_adf_doc_with_non_list_content():
    assert flatten_adf({"version": 1, "type": "doc", "content": None}) == ""
    assert flatten_adf({"version": 1, "type": "doc"}) == ""


# ── Single block nodes ──────────────────────────────────────────────────────


def test_single_paragraph():
    doc = _doc(_para(_text("Ship the WARN posture.")))
    assert flatten_adf(doc) == "Ship the WARN posture."


def test_heading():
    doc = _doc(
        {"type": "heading", "attrs": {"level": 2}, "content": [_text("Decision")]},
        _para(_text("We chose option B.")),
    )
    assert flatten_adf(doc) == "Decision\nWe chose option B."


def test_two_paragraphs_separated_by_newline():
    doc = _doc(_para(_text("First.")), _para(_text("Second.")))
    assert flatten_adf(doc) == "First.\nSecond."


# ── Marks ignored, text emitted unchanged ───────────────────────────────────


def test_marks_are_ignored_text_emitted_unchanged():
    doc = _doc(
        _para(
            _text("plain "),
            _text("bold", [{"type": "strong"}]),
            _text(" "),
            _text("italic", [{"type": "em"}]),
            _text(" "),
            _text("mono", [{"type": "code"}]),
        )
    )
    assert flatten_adf(doc) == "plain bold italic mono"


def test_link_mark_text_emitted_without_href_v0():
    # v0: the link href is NOT appended — the text is emitted unchanged.
    doc = _doc(
        _para(
            _text(
                "see the docs",
                [{"type": "link", "attrs": {"href": "https://example.com"}}],
            )
        )
    )
    assert flatten_adf(doc) == "see the docs"


# ── Lists ───────────────────────────────────────────────────────────────────


def test_bullet_list():
    doc = _doc(
        {
            "type": "bulletList",
            "content": [
                {"type": "listItem", "content": [_para(_text("alpha"))]},
                {"type": "listItem", "content": [_para(_text("beta"))]},
            ],
        }
    )
    assert flatten_adf(doc) == "alpha\n\nbeta"


def test_ordered_list():
    doc = _doc(
        {
            "type": "orderedList",
            "content": [
                {"type": "listItem", "content": [_para(_text("one"))]},
                {"type": "listItem", "content": [_para(_text("two"))]},
            ],
        }
    )
    assert flatten_adf(doc) == "one\n\ntwo"


def test_nested_lists():
    inner_list = {
        "type": "bulletList",
        "content": [
            {"type": "listItem", "content": [_para(_text("child"))]},
        ],
    }
    doc = _doc(
        {
            "type": "bulletList",
            "content": [
                {"type": "listItem", "content": [_para(_text("parent")), inner_list]},
            ],
        }
    )
    assert "parent" in flatten_adf(doc)
    assert "child" in flatten_adf(doc)


# ── codeBlock preserves inner text ──────────────────────────────────────────


def test_code_block_preserves_inner_text():
    doc = _doc(
        {
            "type": "codeBlock",
            "attrs": {"language": "python"},
            "content": [_text("print('hi')\nreturn 0")],
        }
    )
    assert flatten_adf(doc) == "print('hi')\nreturn 0"


# ── hardBreak → newline ─────────────────────────────────────────────────────


def test_hard_break_becomes_newline():
    doc = _doc(_para(_text("line one"), {"type": "hardBreak"}, _text("line two")))
    assert flatten_adf(doc) == "line one\nline two"


# ── Inline leaf nodes ───────────────────────────────────────────────────────


def test_emoji_leaf_node():
    doc = _doc(_para(_text("ship it "), {"type": "emoji", "attrs": {"text": "🚀"}}))
    assert flatten_adf(doc) == "ship it 🚀"


def test_mention_leaf_node():
    doc = _doc(
        _para(
            {"type": "mention", "attrs": {"text": "@Dev", "id": "abc"}},
            _text(" please review"),
        )
    )
    assert flatten_adf(doc) == "@Dev please review"


def test_status_leaf_node():
    doc = _doc(_para({"type": "status", "attrs": {"text": "DONE", "color": "green"}}))
    assert flatten_adf(doc) == "DONE"


def test_inline_card_leaf_node():
    doc = _doc(_para({"type": "inlineCard", "attrs": {"url": "https://example.com/x"}}))
    assert flatten_adf(doc) == "https://example.com/x"


def test_date_leaf_node_omitted():
    doc = _doc(_para(_text("due "), {"type": "date", "attrs": {"timestamp": "1716200000000"}}))
    assert flatten_adf(doc) == "due"


def test_inline_leaf_missing_attrs_degrades_gracefully():
    # attrs absent / non-dict / missing key → "", never a crash.
    doc = _doc(
        _para(
            _text("a"),
            {"type": "emoji"},
            {"type": "mention", "attrs": None},
            {"type": "status", "attrs": {}},
            {"type": "inlineCard", "attrs": "not-a-dict"},
            _text("b"),
        )
    )
    assert flatten_adf(doc) == "ab"


# ── Unknown node types recurse ──────────────────────────────────────────────


def test_unknown_node_type_recurses_into_content():
    doc = _doc(
        {
            "type": "someFutureNodeType",
            "content": [_para(_text("still readable"))],
        }
    )
    assert flatten_adf(doc) == "still readable"


def test_non_dict_child_is_skipped():
    doc = {
        "version": 1,
        "type": "doc",
        "content": [_para(_text("kept")), None, "junk", 7],
    }
    assert flatten_adf(doc) == "kept"


def test_text_node_with_non_string_text():
    doc = _doc(_para({"type": "text", "text": None}, _text("real")))
    assert flatten_adf(doc) == "real"


# ── Realistic multi-block issue description fixture ──────────────────────────


def test_realistic_multi_block_description():
    doc = {
        "version": 1,
        "type": "doc",
        "content": [
            {
                "type": "heading",
                "attrs": {"level": 1},
                "content": [_text("Context")],
            },
            _para(
                _text("We need to decide the ingest gate posture for "),
                _text("public sources", [{"type": "strong"}]),
                _text("."),
            ),
            {
                "type": "bulletList",
                "content": [
                    {"type": "listItem", "content": [_para(_text("Option A: hard fail"))]},
                    {"type": "listItem", "content": [_para(_text("Option B: WARN + audit"))]},
                ],
            },
            {
                "type": "panel",
                "attrs": {"panelType": "info"},
                "content": [_para(_text("Decision: go with Option B."))],
            },
            {
                "type": "codeBlock",
                "attrs": {"language": "python"},
                "content": [_text("gate = 'warn'")],
            },
        ],
    }
    out = flatten_adf(doc)
    assert "Context" in out
    assert "public sources" in out
    assert "Option A: hard fail" in out
    assert "Option B: WARN + audit" in out
    assert "Decision: go with Option B." in out
    assert "gate = 'warn'" in out
    # No leading / trailing whitespace.
    assert out == out.strip()


def test_deeply_nested_adf_does_not_raise():
    """A pathologically deep ADF tree (thousands of nested block nodes —
    a few KB of JSON, well under the client's response-size cap) must NOT
    raise RecursionError. The flattener caps recursion and drops the
    subtree past the cap, honoring its documented "never raises" contract."""
    doc: dict = {"type": "doc", "version": 1, "content": []}
    node = doc
    for _ in range(5000):
        child: dict = {"type": "blockquote", "content": []}
        node["content"].append(child)
        node = child
    node["content"].append(_text("buried payload"))

    # Must not raise (RecursionError or anything else).
    out = flatten_adf(doc)
    assert isinstance(out, str)
    # Content past the depth cap is dropped — the buried text does not surface.
    assert "buried payload" not in out


def test_shallow_nesting_within_cap_is_preserved():
    """Nesting within the depth cap flattens normally — the guard does not
    clip legitimately-structured (shallow) documents."""
    doc: dict = {"type": "doc", "version": 1, "content": []}
    node = doc
    for _ in range(10):
        child: dict = {"type": "blockquote", "content": []}
        node["content"].append(child)
        node = child
    node["content"].append(_para(_text("reachable decision")))

    assert "reachable decision" in flatten_adf(doc)
