---
name: bicameral-brief
description: Pre-meeting context gatherer. Fires before 1:1s, syncs, PM reviews, and any moment the user says "brief me", "before my meeting with X", or "what do I need to know about Y". Returns a structured one-pager with decisions, drift candidates, divergences, gaps, and 3-5 suggested meeting questions.
---

# Bicameral Brief

Generate a pre-meeting one-pager so the user walks into a conversation already knowing **what was decided, what has drifted, what contradicts itself, and what's still open.** The whole point is to depersonalize hard conversations — bicameral cites prior decisions, the user doesn't have to.

## When to use

Fires on any of these phrasings (not exhaustive — match the intent):

- *"brief me on [topic]"*
- *"what do I need to know about [topic]"*
- *"before my 1:1 with [person]"*
- *"prep for my meeting with [person]"*
- *"sync with [person] about [topic]"*
- *"what's been decided about [area]"*
- *"give me the context on [feature]"*

Fires for both **topic-scoped** briefs ("Google Calendar integration") and **person-scoped** briefs ("meeting with Brian"). If the user names a person, pass them in `participants`.

## When NOT to use

- If the user is asking about a specific file or symbol, prefer `bicameral.drift` or `bicameral.search` — they're more targeted.
- If the user is mid-implementation and asking "what does X touch," use `bicameral.search` — brief is for pre-meeting prep, not blast-radius inspection.
- If the user's topic is empty or generic ("the project," "everything"), ask for a narrower topic before calling.

## Tool call

```
bicameral.brief(
  topic="<the topic or feature area>",
  participants=[<names if user mentioned specific people>],  # optional
  max_decisions=10,                                          # default
)
```

## How to present the response

The `BriefResponse` has six fields. Present them **in this order**, and respect the rule below:

1. **`divergences` — ALWAYS FIRST if non-empty.** Two contradictory decisions on the same symbol is the highest-stakes signal the brief can carry. The meeting's first agenda item should be picking which one wins. Surface each divergence as a bold warning with the symbol, file, and summary line.
2. **`drift_candidates`** — decisions whose code diverged from recorded intent. Present each with status badge (`⚠ DRIFTED`), file:line, and drift evidence.
3. **`decisions`** — the full set of in-scope decisions, grouped by status. Skip any that already appear in `drift_candidates` to avoid duplication.
4. **`gaps`** — open questions and ungrounded decisions. Present as a bulleted list.
5. **`suggested_questions`** — the depersonalization hook. **Surface these VERBATIM**, not paraphrased. They're templated so bicameral is the one asking, not the user.

Skip any bucket that's empty. If everything is empty, say so plainly — that itself is useful information.

## Examples

### Topic-scoped brief

**User:** "Brief me on Google Calendar integration before my sync with Ian."

**Call:** `bicameral.brief(topic="Google Calendar integration", participants=["Ian"])`

**Present:**
```
Brief — Google Calendar integration (prep for sync with Ian)
Generated 2026-04-14 · ref main@7f3a12c

⚠ DIVERGENCE
  `SessionCache` (src/lib/session.ts) has 2 non-superseded decisions
  that contradict — Redis (arch review 2026-03-24) vs local memory
  (PR #171 comment 2026-04-02). Resolve before next deploy.

DRIFT CANDIDATES (1)
  ⚠ `CalendarEventModal.render` — threshold raised 100 → 500
    src/components/calendar/CommunityEventDetailModal.tsx:27-32
    Source: Sprint 12 planning

DECISIONS IN SCOPE (4)
  ✓ Gmail-only beta gate — reflected
  ◐ OAuth one-click add to calendar — pending
  ✓ Coach Ian insights on Weekly Reflection — reflected
  ◯ Outlook calendar parity — ungrounded (open question)

GAPS
  - RSVP sync direction (app → calendar, or bidirectional?)
  - Multi-calendar users: which calendar wins?

QUESTIONS FOR IAN
  1. Which decision on SessionCache is authoritative going forward —
     we have 2 non-superseded decisions that contradict each other?
     (divergence — must resolve before next deploy)
  2. Is the drift in CalendarEventModal.render intentional, and should
     we update the decision or revert the code?
     (drift candidate — threshold raised 100→500)
  3. Can we close the gap on RSVP sync direction?
     (gap — open-question phrasing)
```

### Person-scoped brief (empty result)

**User:** "What's outstanding with Brian?"

**Call:** `bicameral.brief(topic="Brian", participants=["Brian"])`

**Present:** (response has empty decisions / divergences / gaps)

```
Brief — Brian (nothing in scope)

I couldn't find any decisions, drift, or gaps filed against "Brian" as
a topic in the ledger. If you meant a specific project or area Brian
owns, try briefing on that instead — e.g.
`bicameral.brief(topic="subscription flow")`.
```

## Rules

1. **Divergences are load-bearing.** Never bury a divergence below decisions or questions. Lead with it.
2. **Suggested questions verbatim.** The phrasings are templated to be neutral-voice; paraphrasing them reintroduces the "me vs you" framing the tool exists to remove.
3. **No LLM summarization in v0.4.6.** The heuristics under the hood are deterministic. Don't layer your own LLM interpretation on top of the response — present it as-is and let the user ask follow-up questions.
4. **Short is better than comprehensive.** If the brief response has 30 decisions, show the top 10 by status severity (drifted → reflected → pending → ungrounded) and note the full count.
