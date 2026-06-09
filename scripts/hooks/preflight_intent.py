"""Preflight intent classifier.

Single source of truth for the verb list used by the bicameral-preflight
SKILL.md description and the UserPromptSubmit hook. Deterministic: no
LLM, no network, no I/O beyond a string scan.

#402 — Slash-command surface forms (``/qor-plan <issue-url>``) are
recognized via a dedicated implementation-intent slash-command set,
because the verb list alone misses commands like ``/qor-plan`` and
``/qor-debug`` where the implementation-intent verb is encoded in the
command name and not the prompt body. The classifier returns both a
``fire`` decision and a ``prompt_surface_form`` label so downstream
telemetry can spot future trigger-surface regressions before users do.
"""

from __future__ import annotations

import re
from typing import NamedTuple

IMPLEMENTATION_VERBS: frozenset[str] = frozenset(
    {
        "add",
        "build",
        "create",
        "implement",
        "modify",
        "refactor",
        "update",
        "fix",
        "change",
        "write",
        "edit",
        "move",
        "rename",
        "remove",
        "delete",
        "extract",
        "convert",
        "integrate",
        "deploy",
        "ship",
        "configure",
        "connect",
        "extend",
        "migrate",
        "wire",
        "hook up",
        "set up",
        "complete",
        "finish",
        "continue",
    }
)

INDIRECT_INTENT_PHRASES: tuple[str, ...] = (
    "how should i implement",
    "how do i build",
    "how should i write",
    "what's the best way to add",
    "what's the cleanest way to refactor",
)

# Slash-commands whose invocation implies imminent code-implementation work.
# #402: ``/qor-plan <issue-url>`` was silently skipping preflight because
# ``plan`` is not in the free-text verb list and the verb is encoded in the
# slash-command name. Slash-commands listed here short-circuit to ``fire`` no
# matter what their argument is (URL, plain text, or empty).
#
# Keep this in lockstep with the ``description:`` field in
# ``skills/bicameral-preflight/SKILL.md`` — that string is what Claude Code's
# skill-discovery layer reads for the Tier-2 (caller-LLM) gate. Tier-1
# (this hook) and Tier-2 (the skill description) must agree on the slash-
# command surface or the gates drift.
IMPL_INTENT_SLASH_COMMANDS: frozenset[str] = frozenset(
    {
        "qor-plan",
        "qor-implement",
        "qor-refactor",
        "qor-debug",
        "qor-remediate",
        "qor-organize",
        "qor-auto-dev-1",
        "qor-auto-dev",
    }
)

SKIP_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bfix\b.*\btypo\b", re.IGNORECASE),
    re.compile(r"\bbump\b.*\b(?:to|from)\b.*\d+\.\d+", re.IGNORECASE),
    re.compile(r"\bhow does\b", re.IGNORECASE),
    # #343 — suppress preflight for clearly non-decision-related work.
    re.compile(r"\b(?:lint|format|prettier|eslint|ruff)\b", re.IGNORECASE),
    re.compile(r"\breadme\b", re.IGNORECASE),
    re.compile(
        r"\b(?:fix|update|add|edit|configure)\b.*\b(?:ci|github.actions?|workflow)\b"
        r"|\b(?:ci|github.actions?|workflow)\b.*\b(?:fix|update|add|edit|configure)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\btest(?:s|ing)?\b.*\b(?:fix|add|update|write)\b", re.IGNORECASE),
    re.compile(r"\b(?:fix|add|update|write)\b.*\btest(?:s|ing)?\b", re.IGNORECASE),
    re.compile(r"\b(?:changelog|release.notes?)\b", re.IGNORECASE),
    re.compile(r"\b(?:docker|dockerfile|compose)\b", re.IGNORECASE),
)

_VERB_REGEX = re.compile(
    r"\b(?:" + "|".join(re.escape(v) for v in IMPLEMENTATION_VERBS) + r")\b",
    re.IGNORECASE,
)

_SLASH_COMMAND_RE = re.compile(r"^\s*/([A-Za-z][\w-]*)")
_URL_ONLY_RE = re.compile(r"^https?://\S+\s*$", re.IGNORECASE)

# Public surface-form labels — also referenced by the telemetry event field
# ``prompt_surface_form`` so the dashboard query is stable.
SURFACE_FREE_TEXT = "free_text"
SURFACE_SLASH_WITH_URL = "slash_command_with_url"
SURFACE_SLASH_WITH_TEXT = "slash_command_with_text"
SURFACE_SLASH_BARE = "slash_command_bare"
SURFACE_EMPTY = "empty"


class ClassifyResult(NamedTuple):
    """Result of :func:`classify_prompt`.

    Attributes:
        fire: True when the hook should inject the preflight reminder.
        prompt_surface_form: One of ``SURFACE_*`` — recorded to telemetry
            so we can spot regressions in the trigger surface (#402).
        slash_command: Lowercased slash-command name when the prompt began
            with one (e.g. ``"qor-plan"``); ``None`` otherwise. Useful for
            triage but not part of the fire decision contract.
    """

    fire: bool
    prompt_surface_form: str
    slash_command: str | None


def _detect_surface_form(prompt: str) -> tuple[str, str | None, str]:
    """Return ``(surface_form, slash_command_or_None, remainder_text)``."""
    stripped = prompt.strip()
    if not stripped:
        return (SURFACE_EMPTY, None, "")
    match = _SLASH_COMMAND_RE.match(stripped)
    if not match:
        return (SURFACE_FREE_TEXT, None, stripped)
    command = match.group(1).lower()
    remainder = stripped[match.end() :].strip()
    if not remainder:
        return (SURFACE_SLASH_BARE, command, "")
    if _URL_ONLY_RE.match(remainder):
        return (SURFACE_SLASH_WITH_URL, command, remainder)
    return (SURFACE_SLASH_WITH_TEXT, command, remainder)


def classify_prompt(prompt: str) -> ClassifyResult:
    """Classify a user prompt for preflight auto-fire.

    The classifier is layered:
      1. Empty / whitespace-only prompts never fire.
      2. Slash-commands in :data:`IMPL_INTENT_SLASH_COMMANDS` fire
         unconditionally — the command name itself encodes implementation
         intent (e.g. ``/qor-plan``).
      3. Otherwise fall through to the free-text classifier: skip
         patterns suppress, verb regex or indirect-intent phrases fire.
    """
    if not prompt or not prompt.strip():
        return ClassifyResult(False, SURFACE_EMPTY, None)

    surface_form, command, _remainder = _detect_surface_form(prompt)

    if command is not None and command in IMPL_INTENT_SLASH_COMMANDS:
        return ClassifyResult(True, surface_form, command)

    for skip in SKIP_PATTERNS:
        if skip.search(prompt):
            return ClassifyResult(False, surface_form, command)

    if has_implementation_signal(prompt):
        return ClassifyResult(True, surface_form, command)

    return ClassifyResult(False, surface_form, command)


def has_implementation_signal(prompt: str) -> bool:
    """True iff the prompt carries any code-implementation signal — an
    implementation verb or an indirect-intent phrase — INDEPENDENT of the
    SKIP_PATTERNS.

    #170: the post-preflight capture gate suppresses its disambiguation
    reminder only when this is False (a genuinely read-only prompt cannot be
    refining a surfaced decision). It deliberately does NOT consult
    SKIP_PATTERNS: a skip-listed-but-verb-bearing prompt like "add tests for X"
    still carries implementation signal and must reach the user-disambiguation
    per #175 — never lexically pre-judged "compatible."
    """
    if not prompt or not prompt.strip():
        return False
    _surface_form, command, _remainder = _detect_surface_form(prompt)
    if command is not None and command in IMPL_INTENT_SLASH_COMMANDS:
        return True
    if _VERB_REGEX.search(prompt):
        return True
    lowered = prompt.lower()
    return any(phrase in lowered for phrase in INDIRECT_INTENT_PHRASES)


def should_fire_preflight(prompt: str) -> bool:
    """Return True iff prompt indicates code-implementation intent.

    Backward-compatible wrapper around :func:`classify_prompt` — preserves
    the original boolean contract for existing callers and tests.
    """
    return classify_prompt(prompt).fire
