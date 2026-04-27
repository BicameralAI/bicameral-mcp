---
name: gap_visualization
description: Visual rendering contract for all bicameral gap and signal outputs. Defines ASCII diagram templates for each gap type (infrastructure, edge cases, data policy, divergence, drift), actionability filter rules, and roll-up format. Vendor gap and acceptance criteria gap are not visualized (text only). Referenced by bicameral-ingest step 5/6 and bicameral-judge-gaps. Not user-triggered.
type: reference
---

# Gap Visualization

Rendering contract for gap and signal outputs. Each gap type maps to a specific ASCII diagram style drawn from the pico visual catalog. The goal: show the gap structurally, not describe it in prose.

Visual style: `┌─────┐` box borders, `═══` for specified/happy paths, `─ ─ ─` for missing/undefined paths, `▶` for flow, `│` for tree branches.

**Only render a visual for ask-gaps** — findings where the team hasn't resolved something and reasonable people could disagree. Mechanical findings (obvious resolution) are noted inline as `✓ resolved: <one line>` and skipped. Empty categories produce no output.

---

## Actionability Filter

- Render a visual only when the gap is an `ask` finding
- Skip the category entirely (no header, no note) when it has zero findings
- Max 3 visuals per rubric category; if more exist, surface the batched gate
- Roll-up line after all visuals: `N actionable gap(s) — M of 5 categories had findings.`
- Omit the roll-up entirely when N = 0

---

## 1. Edge Case Gap → Railway Diagram

**Rubric category**: `underdefined_edge_cases`

**When**: The happy path is specified but a user-state boundary, policy exception, or lifecycle event is not addressed.

**Visual style**: Q5 Railway Diagram — `═══` for the specified path, `─ ─ ─` for the missing path, `[not defined]` as the terminal for the gap.

```
┌─────────────────────────────────────────────────────────────┐
│ Edge Case Gap: <scenario not addressed>                     │
├─────────────────────────────────────────────────────────────┤
│ Decision: "<description>"  (<source_ref> · <date>)          │
│                                                             │
│  ══════════════════════════════════════ Specified           │
│  ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ Not addressed      │
│                                                             │
│  [<triggering action or state>]                             │
│       │                                                     │
│       ├════════▶ <happy path> ════▶ <specified outcome>     │
│       │                                                     │
│       └─ ─ ─ ─▶ <unaddressed scenario>                      │
│                     │                                       │
│                     ▼                                       │
│               [not defined]  ← <policy/user-state gap>      │
│                                                             │
│ SOURCE: "<source_excerpt verbatim>"                         │
└─────────────────────────────────────────────────────────────┘
```

**Variant — lifecycle gap (State Machine style, Q2)**: Use when the gap is a missing state transition (reactivation, reinstatement, account reopen):

```
┌─────────────────────────────────────────────────────────────┐
│ State Gap: no <State A> → <State B> transition              │
├─────────────────────────────────────────────────────────────┤
│ Decision: "<description>"  (<source_ref> · <date>)          │
│                                                             │
│  CURRENT STATES (implied by decision):                      │
│                                                             │
│          ┌─────────┐  <event>  ┌─────────┐                 │
│          │<State A>├──────────▶│<State B>│                 │
│          └─────────┘           └────┬────┘                 │
│                                     │ <event>               │
│                                     ▼                       │
│                                ┌─────────┐                 │
│                                │<State C>│ ← terminal       │
│                                └─────────┘                 │
│                                     x                       │
│                                     x  No return path       │
│               └ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘                      │
│                                                             │
│ SOURCE: "<source_excerpt verbatim>"                         │
└─────────────────────────────────────────────────────────────┘
```

**What goes in the unaddressed scenario**: user-state boundaries (free vs paid, anonymous vs logged-in), policy exceptions (refunds, overrides, escalations), or lifecycle events (churn, reactivation, account close). NOT technical failure modes (retries, timeouts) — engineering concerns only.

---

## 2. Infrastructure Commitment → Dependency Radar

**Rubric category**: `infrastructure_gap`

**When**: The decision implicitly commits the business to infrastructure carrying a cost, procurement, legal, or SLA consequence that was not discussed.

**Visual style**: Q4 Dependency Radar — show the new dependency at the top, draw arrows to affected services, call out the unsigned commitment at the bottom.

```
┌─────────────────────────────────────────────────────────────┐
│ Infra Commitment: <vendor / SLA / region / scale>           │
├─────────────────────────────────────────────────────────────┤
│ Decision: "<description>"  (<source_ref> · <date>)          │
│                                                             │
│              ┌─────────────────┐                            │
│              │ <new dependency>│  external / new cost       │
│              └────────┬────────┘                            │
│                       │ requires <contract/procurement/SLA> │
│                       ▼                                     │
│   ┌─────────────┐     ┌─────────────┐    ┌─────────────┐   │
│   │ <service A> │────▶│ <core svc>  │◀───│ <service B> │   │
│   └─────────────┘     └──────┬──────┘    └─────────────┘   │
│                              │                              │
│                              ▼                              │
│                       ┌─────────────┐                       │
│                       │  <data/DB>  │                       │
│                       └─────────────┘                       │
│                                                             │
│ ○ no sign-off: <what business stakeholder must approve>     │
│ SOURCE: "<source_excerpt verbatim>"                         │
└─────────────────────────────────────────────────────────────┘
```

**Commitment categories** (only these warrant a visual — not engineering hygiene):
- New SaaS dependency → cost center, procurement, renewal risk
- Specific cloud vendor/region → lock-in, data portability
- Data residency jurisdiction → legal / compliance review
- Implicit SLA (uptime, latency, throughput externally committed)
- Scale assumption (traffic, storage, concurrent users) validated by product?

---

## 3. Data Policy Gap → Data Flow

**Rubric category**: `missing_data_requirements`

**When**: The decision implies collecting, storing, or transmitting personal/regulated/sensitive data without a stated retention, consent, or audit policy.

**Visual style**: Q3 Data Flow — show the current pipeline with `x x x x` marking where the policy should be but isn't. Show an existing similar pattern (if one exists in related decisions) as contrast.

```
┌─────────────────────────────────────────────────────────────┐
│ Data Policy Gap: <PII / retention / consent / audit>        │
├─────────────────────────────────────────────────────────────┤
│ Decision: "<description>"  (<source_ref> · <date>)          │
│                                                             │
│  DATA FLOW (implied by decision):                           │
│                                                             │
│  [<source/event>] ──▶ [<processor>] ──▶ [store: <data>]    │
│                             │                               │
│                             │     x x x x x x x x           │
│                             │     (no <policy> defined)     │
│                             ▼                               │
│                      [<downstream>]                         │
│                                                             │
│  POLICY NEEDED:                                             │
│  - <retention: how long kept / what triggers deletion?>     │
│  - <consent: captured when / revocable how?>                │
│  - <residency / cross-border: any GDPR / CCPA scope?>       │
│                                                             │
│ ○ no policy stated                                          │
│ SOURCE: "<source_excerpt verbatim>"                         │
└─────────────────────────────────────────────────────────────┘
```

Only fill in the POLICY NEEDED lines that are actually implied by the decision. Omit lines that don't apply. Do NOT surface schema mechanics (column types, migration scripts) — only policy that a legal or privacy reviewer would flag.

---

## 4. Divergence → Railway with Two Competing Paths

**Source**: `brief.divergences[]` in the ingest response (step 5 of `bicameral-ingest`).

**When**: Two non-superseded decisions contradict on the same symbol. Always rendered — highest-stakes signal, fires regardless of guided mode.

**Visual style**: Extended Q5 Railway — two parallel tracks stemming from the same symbol, each labeled with its decision. The split makes the conflict obvious.

```
┌─────────────────────────────────────────────────────────────┐
│ ⚡ DIVERGENCE: <symbol> in <file>                           │
├─────────────────────────────────────────────────────────────┤
│ Two non-superseded decisions contradict on the same symbol. │
│                                                             │
│  ════════════════════════════════════ Decision A            │
│  ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ Decision B            │
│                                                             │
│  [<symbol / trigger>]                                       │
│       │                                                     │
│       ├════════▶  A: "<decision A description>"             │
│       │              <decision_id_A_short>                  │
│       │                                                     │
│       └─ ─ ─ ─▶  B: "<decision B description>"             │
│                      <decision_id_B_short>                  │
│                                                             │
│  → Resolve: bicameral.resolve_collision                     │
└─────────────────────────────────────────────────────────────┘
```

---

## 5. Drift → Callout (no diagram)

**Source**: `brief.drift_candidates[]` in the ingest response (step 5 of `bicameral-ingest`).

**When**: A decision's code region has diverged from the recorded intent.

**Visual style**: Plain callout — no diagram. Name the drifted decision, cite the file, and point the user to the dashboard for details. Keep it to 2 lines max per drift so it doesn't dominate the output.

```
⚠ DRIFTED · "<decision description>" (<source_ref> · <date>)
  <file_path>:<line> — open the dashboard for full drift details.
```

If multiple decisions drifted, list them in a tight block:

```
⚠ N drifted decision(s):
  · "<decision A>" — <file>:<line>  (<source_ref>)
  · "<decision B>" — <file>:<line>  (<source_ref>)
  Open bicameral.dashboard to inspect and resolve.
```

---

## Anti-patterns

- Using labeled prose boxes instead of structural diagrams — the point is to show the gap visually
- Rendering a visual for a mechanical gap (obvious resolution) — resolve inline, no visual
- Rendering a visual for an empty category — skip the category entirely
- Paraphrasing `source_excerpt` — always quote verbatim
- Putting engineering gaps in the rubric visuals (retry logic, race conditions, schema migrations, wire protocol, Dockerfile) — out of scope for all 5 business-requirement categories
- Editorializing in WHY lines ("this is concerning", "the team should…") — keep factual
- Showing the roll-up line when N = 0
- Making up service names, states, or flow steps that the decision didn't name or clearly imply — fill in only what the source excerpt actually says; use `<?>` for genuinely unknown parts
