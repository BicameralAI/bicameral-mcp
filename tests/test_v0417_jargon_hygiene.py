"""v0.4.17 — jargon hygiene lint.

Blocks backend implementation terminology from leaking into the
user-facing skill files and tool descriptions. Every new skill and
every new tool must be clean at write time — this test fails CI if
any of the forbidden terms below appear in:

  1. ``skills/**/SKILL.md`` (canonical skill tree)
  2. ``.claude/skills/**/SKILL.md`` (local Claude Code mirror)
  3. ``server.py`` — extracted string literals from the ``list_tools()``
     return value's ``description`` fields

Rationale: during the v0.4.16 dogfood, multiple skills and tool
descriptions had quietly accumulated references like "BM25",
"SurrealDB", "tree-sitter", "RRF fusion" over six releases. These
terms leak into agent output when the agent paraphrases or cites the
tool / skill description back to the user. Blocking at CI time is
cheaper than auditing by hand every release.

What's allowed (appears legitimately in user-facing text):
  - git, commit, file, symbol, function, class — universal dev vocab
  - "auto-grounding", "semantic search" — user-facing abstractions
  - "range_diff", "sweep_scope" — wire-contract field names the agent
    needs to read; surfaced intentionally in skill contracts

Out of scope (Option B, deferred to a later polish pass):
  - Runtime strings in ``handlers/action_hints.py`` message builders
  - Pydantic field comments in ``contracts.py``
  - Handler return values with computed descriptions
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_MCP_ROOT = Path(__file__).parent.parent

# Case-insensitive forbidden terms. Whole-word match to avoid false
# positives on things like "bm25_index" variable names in code examples.
FORBIDDEN_TERMS = [
    "BM25",
    "tree-sitter",
    "treesitter",
    "SurrealDB",
    "SurrealKV",
    "RRF",
    "Jaccard",
    "graph-fusion",
    "graph fusion",
    "canonical_id",
    "UUIDv5",
    "JCS",
    "@0@",
    "Pydantic",
]


def _all_skill_files() -> list[Path]:
    return sorted(
        [
            *_MCP_ROOT.glob("skills/**/SKILL.md"),
            *_MCP_ROOT.glob(".claude/skills/**/SKILL.md"),
        ]
    )


def _compile_patterns() -> list[tuple[str, re.Pattern]]:
    patterns: list[tuple[str, re.Pattern]] = []
    for term in FORBIDDEN_TERMS:
        # Whole-word match, case-insensitive. Use custom word boundaries
        # because @0@ and a few others include non-word chars.
        if re.match(r"^[A-Za-z0-9]+$", term):
            pattern = re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE)
        else:
            pattern = re.compile(re.escape(term), re.IGNORECASE)
        patterns.append((term, pattern))
    return patterns


def test_no_backend_jargon_in_skill_files():
    """Every skill file must be free of backend terminology.

    Fires over both the canonical ``skills/`` tree and the
    ``.claude/skills/`` local Claude Code mirror so a fix to one
    isn't a regression on the other.

    v0.6.4: bicameral-ingest no longer needs a BM25/RRF exception — all
    retrieval jargon was removed when the server stopped performing
    BM25/RRF code search. Callers now own retrieval end-to-end.
    """
    patterns = _compile_patterns()
    offenders: list[str] = []
    for path in _all_skill_files():
        rel = str(path.relative_to(_MCP_ROOT))
        body = path.read_text()
        for term, pattern in patterns:
            for match in pattern.finditer(body):
                # Find the line number for a useful error message
                line_no = body.count("\n", 0, match.start()) + 1
                offenders.append(f"{rel}:{line_no}: '{match.group()}' (term: '{term}')")
    assert not offenders, (
        "Backend jargon found in user-facing skill files:\n"
        + "\n".join(f"  - {o}" for o in offenders)
        + "\n\nReplace with user-facing terminology. See "
        "thoughts/shared/plans/2026-04-15-v0.4.17-... for the lint "
        "rationale and the list of allowed user-facing abstractions."
    )


def test_no_backend_jargon_in_tool_descriptions():
    """Every ``Tool(description=...)`` string in ``server.py`` must
    be free of backend terminology.

    Parses the AST and walks every ``Tool(...)`` call, extracting the
    ``description`` keyword argument. Handles both plain string
    literals and concatenated string literals (the common pattern in
    server.py where descriptions span multiple quoted lines).
    """
    server_src = (_MCP_ROOT / "server.py").read_text()
    tree = ast.parse(server_src)
    patterns = _compile_patterns()

    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Match Tool(...) — plain Name or attribute reference
        func = node.func
        is_tool = (isinstance(func, ast.Name) and func.id == "Tool") or (
            isinstance(func, ast.Attribute) and func.attr == "Tool"
        )
        if not is_tool:
            continue

        tool_name = "<unknown>"
        desc_text: str | None = None
        for kw in node.keywords:
            if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                tool_name = kw.value.value
            if kw.arg == "description":
                try:
                    desc_text = ast.literal_eval(kw.value)
                except Exception:
                    desc_text = None

        if not isinstance(desc_text, str):
            continue

        for term, pattern in patterns:
            for match in pattern.finditer(desc_text):
                offenders.append(f"Tool '{tool_name}': '{match.group()}' (term: '{term}')")

    assert not offenders, "Backend jargon found in Tool descriptions:\n" + "\n".join(
        f"  - {o}" for o in offenders
    )


def test_lint_catches_synthetic_jargon():
    """Smoke test: a hand-constructed string containing forbidden
    terms must be caught by the same compiled patterns the test uses.

    Guards against "the patterns compiled silently to a no-op" bugs.
    """
    patterns = _compile_patterns()
    synthetic = "This skill uses BM25 over the SurrealDB index via tree-sitter."
    hits = [term for term, pat in patterns if pat.search(synthetic)]
    assert "BM25" in hits
    assert "SurrealDB" in hits
    assert "tree-sitter" in hits


def test_lint_allows_user_facing_vocabulary():
    """Smoke test: legitimate user-facing vocabulary must NOT trigger
    the patterns. Guards against over-eager regexes."""
    patterns = _compile_patterns()
    legitimate = (
        "The tool auto-grounds decisions via semantic search over the "
        "symbol graph. It checks every file git diff --name-only "
        "reports as changed, deduplicates by intent_id, and surfaces "
        "drift evidence. No LLM in the critical path."
    )
    hits = [term for term, pat in patterns if pat.search(legitimate)]
    assert not hits, f"Unexpected hits on legitimate vocabulary: {hits}"
