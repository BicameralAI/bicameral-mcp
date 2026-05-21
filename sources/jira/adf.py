"""ADF -> plain-text flattener (#337 Phase A).

In Jira Cloud REST API v3, an issue ``description`` and a comment ``body``
arrive as Atlassian Document Format (ADF) JSON — a recursive tree rooted
at a ``doc`` node — not plain strings. ``flatten_adf`` collapses that tree
to plain text so downstream decision-extraction sees readable content.

This is the single genuinely novel component of the Jira integration; it
has no in-repo precedent. It is implemented directly from the reference
algorithm in ``docs/vendor/jira/atlassian-document-format.md`` §6.

Design constraints:
- **Pure function.** No I/O, no globals, deterministic. Solitary unit
  tests are correct here (per CLAUDE.md "solitary is correct for pure
  helpers").
- **Defensive.** Per-node ``attrs`` key names are only PARTIALLY VERIFIED
  in the grounding doc, so every lookup uses ``.get()`` with a default.
  A non-``doc`` root, ``version != 1``, ``None``, or a non-dict input all
  return ``""``. Unknown node types recurse into ``content``. The function
  never raises on malformed ADF — worst case it returns ``""`` or partial
  text.
- **v0 minimal.** ``link``-mark href appending is deliberately out of
  scope: a ``text`` node's string is emitted unchanged. Recorded as a
  deferred nicety.
"""

from __future__ import annotations

# Block / container nodes whose flattened inner content is followed by a
# newline (a paragraph / structural break in the plain-text output).
_BLOCK_NEWLINE_TYPES = frozenset(
    {
        "paragraph",
        "heading",
        "blockquote",
        "panel",
        "codeBlock",
        "rule",
        "listItem",
        "tableRow",
    }
)

# Container nodes whose inner content is emitted verbatim with no extra
# break (their children supply their own structure).
_BLOCK_PASSTHROUGH_TYPES = frozenset({"bulletList", "orderedList", "table"})

# Recursion-depth cap. A legitimate ADF document nests only a handful of
# levels deep (lists in table cells in panels, etc.); 100 is far beyond
# any real content. A hostile payload with thousands of nested nodes —
# a few KB of JSON, well under the client's response-size cap — would
# otherwise raise RecursionError and break this function's "never raises"
# contract. Past the cap a node contributes "" (its subtree is dropped).
_MAX_DEPTH = 100


def _flatten_node(node: object, _depth: int = 0) -> str:
    """Flatten a single ADF node (and its subtree) to plain text.

    Tolerates anything: a non-dict node contributes ``""``. ``_depth`` is
    internal — recursion past ``_MAX_DEPTH`` yields ``""`` so a
    pathologically nested payload cannot raise ``RecursionError``.
    """
    if _depth > _MAX_DEPTH:
        return ""
    if not isinstance(node, dict):
        return ""

    node_type = node.get("type")

    if node_type == "text":
        # v0: emit the literal text unchanged; marks (strong/em/code/link
        # /strike/underline/...) are presentational and dropped.
        text = node.get("text")
        return text if isinstance(text, str) else ""

    if node_type == "hardBreak":
        return "\n"

    if node_type in {"emoji", "mention", "status"}:
        attrs = node.get("attrs")
        if isinstance(attrs, dict):
            value = attrs.get("text")
            if isinstance(value, str):
                return value
        return ""

    if node_type == "inlineCard":
        attrs = node.get("attrs")
        if isinstance(attrs, dict):
            value = attrs.get("url")
            if isinstance(value, str):
                return value
        return ""

    if node_type == "date":
        # The epoch-ms timestamp carries no decision content; omit it.
        return ""

    # Block / container / unknown node: recurse into ``content``.
    content = node.get("content")
    children = content if isinstance(content, list) else []
    inner = "".join(_flatten_node(child, _depth + 1) for child in children)

    if node_type in _BLOCK_NEWLINE_TYPES:
        return inner + "\n"
    # bulletList / orderedList / table, ``doc``, and any unknown type all
    # just pass their inner content through unchanged.
    return inner


def flatten_adf(doc: object) -> str:
    """Flatten an ADF document to plain text.

    Args:
        doc: An ADF document — normally ``{"version": 1, "type": "doc",
            "content": [...]}``. ``None``, a non-dict, a non-``doc`` root,
            or ``version != 1`` all yield ``""``.

    Returns:
        The flattened plain-text content, ``.strip()``-ed. Never raises.
    """
    if not isinstance(doc, dict):
        return ""
    if doc.get("type") != "doc":
        return ""
    if doc.get("version") != 1:
        return ""

    content = doc.get("content")
    children = content if isinstance(content, list) else []
    return "".join(_flatten_node(child) for child in children).strip()
