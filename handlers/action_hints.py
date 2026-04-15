"""v0.4.9 (Phase 2) — tester mode action hint generators.

When ``ctx.tester_mode`` is True, search and brief responses are
augmented with ``ActionHint`` objects that tell the agent what MUST
be addressed before any write operation. The generators are pure,
post-compute, zero extra DB calls — they inspect the already-computed
response object and emit hints derived from its contents.

Design anchors:
  - **Zero DB roundtrips.** Hint generation runs after the handler has
    already paid its query cost. If a generator needs data that isn't
    already on the response, it doesn't fire. This keeps tester mode
    free-at-cost for the server.
  - **Hints are advisory at the wire, blocking at the skill.** MCP
    can't force the agent to do anything. We set ``blocking=True``
    and rely on the ``bicameral-tester`` skill contract to teach the
    agent to stop until each blocking hint is resolved.
  - **No LLM here.** All heuristics are deterministic. Matches the
    ``handlers/brief.py`` invariant from v0.4.6.
"""

from __future__ import annotations

from contracts import (
    ActionHint,
    BriefResponse,
    SearchDecisionsResponse,
)


# ── Generators ──────────────────────────────────────────────────────


def generate_hints_for_search(
    response: SearchDecisionsResponse,
    tester_mode: bool,
) -> list[ActionHint]:
    """Inspect a ``SearchDecisionsResponse`` and emit blocking hints.

    Fires:
      - ``review_drift`` when any matched decision has status=drifted.
        Refs: the drifted decisions' intent_ids.
      - ``ground_decision`` when the response has ungrounded matches
        (already surfaced via ``suggested_review`` but we promote it
        to a blocking hint so the agent can't quietly ignore it).

    Returns [] when ``tester_mode`` is False.
    """
    if not tester_mode:
        return []

    hints: list[ActionHint] = []

    drifted = [m for m in response.matches if m.status == "drifted"]
    if drifted:
        files = sorted({
            r.file_path
            for m in drifted
            for r in m.code_regions
            if r.file_path
        })
        hints.append(ActionHint(
            kind="review_drift",
            message=(
                f"{len(drifted)} matched decision(s) have drifted — the "
                f"current code no longer reflects what was decided. "
                f"Review the drifted regions and confirm the code still "
                f"matches stored intent BEFORE making changes."
            ),
            blocking=True,
            refs=[m.intent_id for m in drifted] + files,
        ))

    ungrounded = [m for m in response.matches if m.status == "ungrounded"]
    if ungrounded:
        hints.append(ActionHint(
            kind="ground_decision",
            message=(
                f"{len(ungrounded)} matched decision(s) are recorded but "
                f"have no code grounding yet. Before implementing, confirm "
                f"with the user what should exist — or call "
                f"bicameral_ingest with a refreshed payload to ground them."
            ),
            blocking=True,
            refs=[m.intent_id for m in ungrounded],
        ))

    return hints


def generate_hints_for_brief(
    response: BriefResponse,
    tester_mode: bool,
) -> list[ActionHint]:
    """Inspect a ``BriefResponse`` and emit blocking hints.

    Fires:
      - ``resolve_divergence`` when ``response.divergences`` is non-empty.
        Highest-stakes signal — the brief already leads with it, but
        the hint makes it blocking.
      - ``answer_open_questions`` when ``response.gaps`` contains
        open_question-shaped entries. Refs: the gap description texts.
      - ``review_drift`` when ``response.drift_candidates`` is non-empty
        (subset of matched decisions with status=drifted).

    Returns [] when ``tester_mode`` is False.
    """
    if not tester_mode:
        return []

    hints: list[ActionHint] = []

    if response.divergences:
        refs = [
            f"{d.symbol} ({d.file_path})"
            for d in response.divergences
        ]
        hints.append(ActionHint(
            kind="resolve_divergence",
            message=(
                f"{len(response.divergences)} divergent decision pair(s) "
                f"detected on the same symbol. Two non-superseded decisions "
                f"contradict each other — pick which wins with the user "
                f"and mark the loser superseded via bicameral_update BEFORE "
                f"any code change."
            ),
            blocking=True,
            refs=refs,
        ))

    if response.drift_candidates:
        hints.append(ActionHint(
            kind="review_drift",
            message=(
                f"{len(response.drift_candidates)} decision(s) in scope have "
                f"drifted — the code no longer reflects intent. Surface each "
                f"drifted region to the user before editing near it."
            ),
            blocking=True,
            refs=[d.intent_id for d in response.drift_candidates],
        ))

    open_q_gaps = [
        g for g in response.gaps
        if "open-question" in g.hint or "open question" in g.hint
    ]
    if open_q_gaps:
        hints.append(ActionHint(
            kind="answer_open_questions",
            message=(
                f"{len(open_q_gaps)} unanswered open question(s) linked to "
                f"this topic. Surface them to the user and resolve them "
                f"with a follow-up bicameral_ingest BEFORE implementing "
                f"any related code."
            ),
            blocking=True,
            refs=[g.description[:140] for g in open_q_gaps],
        ))

    return hints
