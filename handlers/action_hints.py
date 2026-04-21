"""v0.4.9+ — action hint generators for search and brief responses.

Hints fire whenever a response contains findings worth flagging
(drifted decisions, ungrounded matches, divergent pairs, open
questions). The ``guided_mode`` flag (v0.4.10) dials the **intensity**:

  - ``guided_mode=False`` (default) — hints are **advisory**. ``blocking``
    is False and the message uses softer, suggestive language ("heads
    up — N decision(s) drifted, review before editing near them if you
    can"). The agent is free to proceed; the hint is informational.

  - ``guided_mode=True`` — hints are **blocking**. ``blocking`` is True
    and the message uses imperative language ("N matched decision(s)
    have drifted — review BEFORE making any changes"). The
    ``bicameral-guided`` skill contract forbids write operations until
    each blocking hint is resolved.

Design anchors:
  - **Zero DB roundtrips.** Generators run after the handler has already
    paid its query cost. If a generator needs data that isn't already on
    the response, it doesn't fire. Tester-mode correctness at no server
    cost.
  - **Always-on, intensity-gated.** Pre-v0.4.10 these were gated on the
    whole tester_mode flag; non-tester mode saw zero hints. That was
    wrong — a reflected decision in a normal session should STILL get a
    heads-up if it's linked to a drifted region. The guided flag now
    only toggles ``blocking`` + message tone, not the existence of the
    hint.
  - **No LLM.** All heuristics deterministic, matches the
    ``handlers/brief.py`` invariant from v0.4.6.
"""

from __future__ import annotations

from contracts import (
    ActionHint,
    BriefResponse,
    ScanBranchResponse,
    SearchDecisionsResponse,
)


# ── Message variants ───────────────────────────────────────────────


def _drift_message(count: int, guided: bool) -> str:
    if guided:
        return (
            f"{count} matched decision(s) have drifted — the current code "
            f"no longer reflects what was decided. Review the drifted "
            f"regions and confirm the code still matches stored intent "
            f"BEFORE making changes."
        )
    return (
        f"Heads up — {count} matched decision(s) look drifted from their "
        f"recorded intent. Review the regions below before editing near "
        f"them; the stored baseline may be stale."
    )


def _ground_message(count: int, guided: bool) -> str:
    if guided:
        return (
            f"{count} matched decision(s) are recorded but have no code "
            f"grounding yet. Before implementing, confirm with the user "
            f"what should exist — or call bicameral_ingest with a "
            f"refreshed payload to ground them."
        )
    return (
        f"Note — {count} matched decision(s) are recorded but haven't been "
        f"grounded to code yet. If you're about to implement them, you "
        f"may want to call bicameral_ingest first to capture the actual "
        f"grounding."
    )


def _divergence_message(count: int, guided: bool) -> str:
    if guided:
        return (
            f"{count} divergent decision pair(s) detected on the same "
            f"symbol. Two non-superseded decisions contradict each other "
            f"— pick which wins with the user and mark the loser "
            f"superseded via bicameral_update BEFORE any code change."
        )
    return (
        f"Heads up — {count} divergent decision pair(s) detected on the "
        f"same symbol. Two non-superseded decisions contradict each "
        f"other; worth surfacing to the user before you act on either."
    )


def _open_questions_message(count: int, guided: bool) -> str:
    if guided:
        return (
            f"{count} unanswered open question(s) linked to this topic. "
            f"Surface them to the user and resolve them with a follow-up "
            f"bicameral_ingest BEFORE implementing any related code."
        )
    return (
        f"Note — {count} unanswered open question(s) are in scope for "
        f"this topic. Worth surfacing to the user if you're about to "
        f"implement something related."
    )


# ── Generators ──────────────────────────────────────────────────────


def generate_hints_for_search(
    response: SearchDecisionsResponse,
    guided_mode: bool,
) -> list[ActionHint]:
    """Inspect a ``SearchDecisionsResponse`` and emit action hints.

    Hints fire whenever findings exist, regardless of ``guided_mode``.
    The flag controls **intensity**: ``guided_mode=True`` sets
    ``blocking=True`` and swaps to imperative messages;
    ``guided_mode=False`` sets ``blocking=False`` with advisory tone.

    Kinds:
      - ``review_drift`` — matched decisions with status=drifted
      - ``ground_decision`` — matched decisions with status=ungrounded
    """
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
            message=_drift_message(len(drifted), guided_mode),
            blocking=guided_mode,
            refs=[m.decision_id for m in drifted] + files,
        ))

    ungrounded = [m for m in response.matches if m.status == "ungrounded"]
    if ungrounded:
        hints.append(ActionHint(
            kind="ground_decision",
            message=_ground_message(len(ungrounded), guided_mode),
            blocking=guided_mode,
            refs=[m.decision_id for m in ungrounded],
        ))

    return hints


def generate_hints_for_scan_branch(
    response: ScanBranchResponse,
    guided_mode: bool,
) -> list[ActionHint]:
    """Inspect a ``ScanBranchResponse`` and emit action hints.

    Hints fire whenever findings exist, regardless of ``guided_mode``.
    The flag controls intensity only (blocking + message tone).

    Kinds:
      - ``review_drift`` — at least one decision in the scan is drifted
      - ``ground_decision`` — at least one decision has no code grounding
    """
    hints: list[ActionHint] = []

    drifted = [d for d in response.decisions if d.status == "drifted"]
    if drifted:
        # Union files touched by each drifted entry. DriftEntry carries
        # a symbol but not a file_path directly — fall back to the
        # response-level files_changed list when per-entry file refs
        # aren't available.
        hints.append(ActionHint(
            kind="review_drift",
            message=_drift_message(len(drifted), guided_mode),
            blocking=guided_mode,
            refs=[d.decision_id for d in drifted] + response.files_changed,
        ))

    ungrounded = [d for d in response.decisions if d.status == "ungrounded"]
    if ungrounded:
        hints.append(ActionHint(
            kind="ground_decision",
            message=_ground_message(len(ungrounded), guided_mode),
            blocking=guided_mode,
            refs=[d.decision_id for d in ungrounded],
        ))

    return hints


def generate_hints_for_brief(
    response: BriefResponse,
    guided_mode: bool,
) -> list[ActionHint]:
    """Inspect a ``BriefResponse`` and emit action hints.

    Hints fire whenever findings exist, regardless of ``guided_mode``.
    The flag controls intensity only.

    Kinds:
      - ``resolve_divergence`` — brief.divergences non-empty
      - ``review_drift`` — brief.drift_candidates non-empty
      - ``answer_open_questions`` — brief.gaps contains open-question gaps
    """
    hints: list[ActionHint] = []

    if response.divergences:
        refs = [
            f"{d.symbol} ({d.file_path})"
            for d in response.divergences
        ]
        hints.append(ActionHint(
            kind="resolve_divergence",
            message=_divergence_message(len(response.divergences), guided_mode),
            blocking=guided_mode,
            refs=refs,
        ))

    if response.drift_candidates:
        hints.append(ActionHint(
            kind="review_drift",
            message=_drift_message(len(response.drift_candidates), guided_mode),
            blocking=guided_mode,
            refs=[d.decision_id for d in response.drift_candidates],
        ))

    open_q_gaps = [
        g for g in response.gaps
        if "open-question" in g.hint or "open question" in g.hint
    ]
    if open_q_gaps:
        hints.append(ActionHint(
            kind="answer_open_questions",
            message=_open_questions_message(len(open_q_gaps), guided_mode),
            blocking=guided_mode,
            refs=[g.description[:140] for g in open_q_gaps],
        ))

    return hints
