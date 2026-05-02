"""Heuristic decision-level classifier (#77).

Pure-function port of the L1/L2/L3 rules documented at
``skills/bicameral-ingest/SKILL.md`` lines 178-217.

Public API:

    classify(description: str, source: str = "") -> tuple[str, str]
        -> (level, rationale)

``level`` is always one of ``"L1"``, ``"L2"``, ``"L3"``. The classifier never
returns ``None`` — gate-drop semantics live above this layer (the bicameral-
ingest skill applies hard-exclude / gate filters after level classification).

Pure: same input twice yields the same output. No IO, no network, no LLM.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Pattern tables
# ---------------------------------------------------------------------------

# Roles named in L1 grammar (subject of an L1 commitment).
# Source: SKILL.md line 201 ("A user role (Members, Users, Admins, Guests)").
_L1_ROLES = (
    "Members",
    "Users",
    "Admins",
    "Guests",
    "Customers",
    "Operators",
    "Agents",
)

# Modal / commitment verbs that tie a role to an observable behavior.
_L1_MODALS = (
    "can",
    "will",
    "must",
    "may",
    "are able to",
    "is able to",
    "receive",
    "receives",
    "see",
    "sees",
    "get",
    "gets",
)

# Compiled regexes for the role + modal + outcome shape.
# Matches "<Role> <modal> <verb-phrase>" anywhere in the source.
_L1_ROLE_MODAL_RE = re.compile(
    r"\b(?P<role>" + "|".join(_L1_ROLES) + r")\b\s+"
    r"(?P<modal>" + "|".join(re.escape(m) for m in _L1_MODALS) + r")\b",
    re.IGNORECASE,
)

# "the system supports/provides/exposes ..." — product contract framing
# (SKILL.md line 184).
_L1_SYSTEM_CONTRACT_RE = re.compile(
    r"\bthe\s+(system|product|app|platform)\s+"
    r"(supports|provides|exposes|offers|delivers)\b",
    re.IGNORECASE,
)

# Behavioral-trigger framing without a named role:
# "When <event>, the app/system/product <action>" (SKILL.md line 191 example).
_L1_BEHAVIORAL_TRIGGER_RE = re.compile(
    r"\bwhen\s+[^.]{1,80}?,\s*the\s+(app|system|product|platform|service)\b",
    re.IGNORECASE,
)

# L3 — named external limit / SLA / vendor cap (SKILL.md line 195).
# Patterns: "max <N>", "<= N", "limit of N", "<vendor> SDK limit".
_L3_LIMIT_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bmax(?:imum)?\s+\d+", re.IGNORECASE),
    re.compile(r"<=\s*\d+", re.IGNORECASE),
    re.compile(r"\blimit\s+of\s+\d+", re.IGNORECASE),
    re.compile(r"\b\w+\s+SDK\s+(?:hard\s+)?limit\b", re.IGNORECASE),
    re.compile(r"\b\w+\s+API\s+cap\b", re.IGNORECASE),
)

# Strategy-vs-L1 tiebreaker (SKILL.md line 188-191): a roadmap date with no
# observable behavior is strategy, not L1.
_DATE_LIKE_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bby\s+Q[1-4]\b", re.IGNORECASE),
    re.compile(r"\bin\s+Q[1-4]\b", re.IGNORECASE),
    re.compile(r"\bship(?:ping|ped)?\s+[^.]{0,40}\bQ[1-4]\b", re.IGNORECASE),
    re.compile(r"\bby\s+(?:end\s+of\s+)?(?:H[12]|FY\d+|20\d\d)\b", re.IGNORECASE),
)

# Roadmap-intent verbs that signal "we (the team) will ..." rather than
# user-observable behavior — the agent is the team, not the user.
_ROADMAP_VERBS = (
    "ship",
    "launch",
    "release",
    "deliver",
    "roll out",
    "rollout",
)
_ROADMAP_INTENT_RE = re.compile(
    r"\b(we|the team)\s+(will|are going to|plan to|intend to)\s+"
    r"(?:" + "|".join(_ROADMAP_VERBS) + r")\b",
    re.IGNORECASE,
)

# L2 architecture / approach signals — components, mechanisms, vendor names
# implying a technical choice with an alternative.
_L2_KEYWORD_RES: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(redis|postgres(?:ql)?|surrealdb|mysql|sqlite|kafka|rabbitmq|"
        r"sidekiq|celery|lambda|kubernetes|docker|nginx|envoy|graphql|"
        r"webhook|websocket|grpc)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(backed|backend|frontend|architecture|service|microservice|"
        r"middleware|adapter|driver|cache|queue|worker|sharding|replica|"
        r"horizontal scaling|vertical scaling|load balanc\w+)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(use|using|adopt|chose|chosen)\b\s+\w+", re.IGNORECASE),
    re.compile(r"\binstead\s+of\b", re.IGNORECASE),
    # Interface / contract specs — "the X API returns fields", "response
    # contract includes ..." — these are L2 spec material (failed Gate 2,
    # but the classifier still tags them L2).
    re.compile(
        r"\b(API|endpoint|response|request|contract|payload)\b[^.]*\b"
        r"(returns|includes|contains|exposes|fields)\b",
        re.IGNORECASE,
    ),
)


# Lines that mark a sentence as "already classified" upstream and should be
# stripped before our classifier runs. Used by ingest fixtures that bundle
# an L1 and an L2 in a single source excerpt with an annotation marker.
# The separator between "L1" and "already classified" is non-restrictive
# (any 1-3 chars that aren't a closing bracket) so an ASCII hyphen, en-dash,
# em-dash, or even cp1252-mojibake variant all match.
_ALREADY_CLASSIFIED_LINE_RE = re.compile(
    r"^.*\[\s*L[123]\s*[^\]]{0,5}?\s*already classified\s*\].*$",
    re.IGNORECASE | re.MULTILINE,
)


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------


def classify(description: str, source: str = "") -> tuple[str, str]:
    """Classify a decision into L1 / L2 / L3.

    Args:
        description: The decision text. Often the same as ``source`` for
            classifier-driven calls; ingest-time callers may pass a shorter
            framing in ``description`` and the raw excerpt in ``source``.
        source: Optional broader source excerpt. Both fields are searched.

    Returns:
        ``(level, rationale)`` where level is one of ``"L1"``, ``"L2"``,
        ``"L3"`` and rationale is a structured one-line explanation. The
        rationale string starts with ``"low confidence"`` when no positive
        signal matched and the classifier defaulted (the bulk-classify CLI
        renders these with a ``(low confidence)`` flag for human review).

    Pure function — no IO, no LLM, no network.
    """
    raw = f"{description}\n{source}".strip()
    if not raw:
        return ("L3", "low confidence: empty input -- defaulted to L3")

    # Strip any sentence pre-tagged "[L1 -- already classified]" so the
    # classifier focuses on the as-yet-unclassified portion. The ingest
    # fixture 05_l2_driver_inferred_from_l1 exercises this path: an L1
    # framing precedes the L2 we're meant to classify.
    text = _ALREADY_CLASSIFIED_LINE_RE.sub("", raw).strip()
    if not text:
        text = raw  # nothing left after stripping — fall back to original

    # ── Strategy-vs-L1 tiebreaker: roadmap date + no behavior → L3 ──────
    has_date = any(p.search(text) for p in _DATE_LIKE_RES)
    has_roadmap_verb = bool(_ROADMAP_INTENT_RE.search(text))
    has_role_modal = bool(_L1_ROLE_MODAL_RE.search(text))
    has_behavioral_trigger = bool(_L1_BEHAVIORAL_TRIGGER_RE.search(text))
    has_system_contract = bool(_L1_SYSTEM_CONTRACT_RE.search(text))

    if (has_date or has_roadmap_verb) and not (has_role_modal or has_behavioral_trigger):
        return (
            "L3",
            "L3 -- strategy/roadmap intent (date or 'we will ship' without observable behavior)",
        )

    # ── L1: role + modal + observable behavior ──────────────────────────
    m = _L1_ROLE_MODAL_RE.search(text)
    if m:
        return (
            "L1",
            f"L1 -- matches role {m.group('role')!r} + modal {m.group('modal')!r}",
        )

    if has_behavioral_trigger:
        return (
            "L1",
            "L1 -- matches behavioral trigger ('when <event>, the app ...')",
        )

    if has_system_contract:
        return (
            "L1",
            "L1 -- matches system-contract framing ('the system supports ...')",
        )

    # ── L3: hard external limit / SLA cap ───────────────────────────────
    for pat in _L3_LIMIT_RES:
        m3 = pat.search(text)
        if m3:
            return (
                "L3",
                f"L3 -- matches external limit/SLA pattern: {m3.group(0)!r}",
            )

    # ── L2: any technical / architectural keyword ───────────────────────
    for pat in _L2_KEYWORD_RES:
        m2 = pat.search(text)
        if m2:
            return (
                "L2",
                f"L2 -- matches technical/architectural signal: {m2.group(0)!r}",
            )

    # ── Fallback: no signal matched ─────────────────────────────────────
    return ("L3", "low confidence: no commitment or approach signal — defaulted to L3")
