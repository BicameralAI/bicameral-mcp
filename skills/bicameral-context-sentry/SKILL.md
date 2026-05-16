---
name: bicameral-context-sentry
description: Flow-independent knowledge graph reconciliation and user-probing primitive. Two responsibilities — (1) graph reconciliation: given any topic or set of decisions, search the ledger, map relationships (supersessions, parallel decisions, context-for opportunities, naming conflicts), return a structured reconciliation report; (2) user probing: when the reconciliation finds ambiguities (naming conflicts, decision collisions, missing business context), emit targeted questions and wait for resolution before the calling flow proceeds. Called by bicameral-ingest, bicameral-preflight, and bicameral-capture-corrections ONLY — never triggered directly by the user.
---

# Bicameral Context Sentry

The context sentry is the **knowledge graph reconciliation and HITL probe**
primitive for the bicameral skill ecosystem. Any flow that introduces new
decisions or needs to name concepts in relation to what already exists
should pass through the sentry first.

It does two things — in order, always:

1. **Graph reconciliation** — search the knowledge graph to understand how
   the incoming material relates to what's already tracked. Produce a
   structured reconciliation report: matches, conflicts, naming candidates,
   missing context signals.

2. **User probing** — when reconciliation surfaces ambiguities that can't be
   resolved automatically, ask the user targeted questions. Collect answers
   before handing control back to the calling flow. Mechanical resolutions
   (clear parallel scope, exact name match) happen silently.

---

## Phase 1 — Graph Reconciliation

**Input**: one of —
- A raw topic string (2–6 words, from a source title or user prompt)
- A list of candidate decision descriptions (pre-ingest)
- An `IngestResponse` object (post-ingest, for collision/context-for handling)

### Step R1 — Derive search probes

From the input, extract 1–3 search probes. Each probe is a short
noun phrase that captures a distinct theme in the input:

| Input | Probes |
|---|---|
| topic string "abandoned checkout" | `["abandoned checkout"]` |
| 4 decisions about auth + payments | `["auth middleware", "payment flow"]` |
| IngestResponse with collision candidates | `[decision description from each candidate]` |

### Step R2 — Search the ledger

For each probe:
```
bicameral.search(query=<probe>, top_k=10, min_confidence=0.25)
```

Aggregate all results, dedup by `decision_id`.

If all searches return zero results: emit nothing — this is a new area
with no priors. Skip to Phase 2 (nothing to probe).

### Step R3 — Map relationships

For each unique result, classify its relationship to the incoming material:

| Relationship | Signal | Resolution type |
|---|---|---|
| **naming match** | result's `feature_group` shares ≥ 2 content words with incoming topic | silent — reuse existing name |
| **parallel decision** | result covers a different code path, team, or lifecycle phase | silent — no conflict |
| **supersession candidate** | result makes a contradictory claim about the same behavior | user probe required |
| **context-for candidate** | result is `context_pending` and incoming material likely answers it | user probe required |
| **fork settled** | result resolves a prior alternative ("chose X over Y") | silent — flag as prior context |
| **business driver established** | result cites a named driver (SOC2, enterprise contract, SLA) that applies to incoming material | silent — enrich naming |

### Step R4 — Build the reconciliation report

Return a structured report:

```
Reconciliation report — <probe(s)>

NAMING
  Existing feature_group(s): <names and decision counts>
  Recommended: "<ExactName>" (reuse) | "<NewName>" (new area)
  Business driver context: <drivers established in this area, if any>

CONFLICTS (require probing)
  <decision_id>: <supersession candidate description> [source: <source_ref>]
  ...

CONTEXT-FOR (require probing)
  <decision_id>: <context_pending decision> — may be answered by incoming material
  ...

PRIOR FORKS (informational)
  <decision text that settled a prior choice>
  ...
```

When no conflicts or context-for candidates exist, omit those sections.
The NAMING section is always present when any results were found.

---

## Phase 2 — User Probing

Run only when Phase 1 found items requiring probing. Process each
category independently. Mechanical resolutions never generate questions.

### Probe A — Naming conflicts

Trigger: Phase 1 found ≥ 2 feature_group names that partially match
the incoming topic (ambiguous which to reuse).

Present:
```
⚠ Multiple feature groups match this topic area:

  a) "<ExistingGroup1>" — N decisions
  b) "<ExistingGroup2>" — N decisions
  c) "<ProposedNewName>" (new)
  d) Enter a different name

Which group should these decisions belong to?
```

Wait for user response. Apply the chosen name to ALL incoming decisions
unless the user specifies per-decision overrides.

### Probe B — Supersession conflicts

Trigger: Phase 1 found a prior decision making a contradictory claim.

Present:
```
⚠ Possible supersession — is this new decision a replacement?

  New:  "<incoming decision text>"
  Prior: "<prior decision text>" (Source: <source_ref>)

  A. Supersede — prior becomes superseded; new enters live flow
  B. Keep both — parallel decisions, no conflict
  C. Drop the new entry — discard, prior stands
  RECOMMENDATION: A if the new text overrides the prior; B if different scenarios.
```

**Cap at 3 probes per sentry call.** When more than 3 supersession
conflicts exist, emit the first 3 individually, then batch the rest:
```
Bicameral found N more potential conflicts (not asking individually).

  A. Proceed — record all as parallel decisions
  B. Review them — list all, pick for each
RECOMMENDATION: A unless any override a shipped commitment.
```

On **A** (supersede): queue `bicameral.resolve_collision(new_id, old_id, action='supersede')`
On **B** (keep both): queue `bicameral.resolve_collision(new_id, old_id, action='keep_both')`
On **C** (drop): note the decision_id to discard after probing is complete

### Probe C — Context-for candidates

Trigger: Phase 1 found a `context_pending` decision that the incoming
material likely answers.

Present:
```
This excerpt may answer the open question for:
  "[context_pending decision description]"

Excerpt: "<span text or incoming material snippet>"

Does this provide the context needed? [Y/n]
```

On **Y**: queue `bicameral.resolve_collision(span_id, decision_id, confirmed=True)`
On **N**: queue `bicameral.resolve_collision(span_id, decision_id, confirmed=False)`

### Probe D — Missing business context

Trigger: incoming decisions describe engineering behavior with no named
business driver, AND the sentry found that this feature area has established
business driver patterns (compliance, contract, SLA) from prior decisions.

Present:
```
Prior decisions in "<feature_group>" cite [SOC2 / enterprise contract / SLA].
Does this decision share the same driver, or is there a separate motivation?

  a) Yes — same driver: <driver from prior>
  b) Different driver — <enter here>
  c) No business driver named (engineering-only — will not be tracked)
```

Only emit Probe D when the mismatch is unambiguous. Do not probe on
every decision — one question for the whole batch is sufficient.

---

## Execution protocol

After all probes are answered, execute the queued resolutions in order:
1. All `resolve_collision` calls (supersession + context-for)
2. Any discards (`reset` scoped to dropped decision_ids)

Then return control to the calling flow with:
- The resolved `feature_group` name
- Any updated decision descriptions (with business driver context appended)
- List of decisions that were dropped (for the calling flow to exclude from ingest)

---

## Rules

1. **Mechanical resolutions are always silent.** No output for clear parallel
   scope, exact name matches, or established driver pattern matches.
2. **Cap at 3 supersession probes per sentry call.** Batch the rest.
3. **Phase 1 empty path is silent.** Zero search results → no output,
   no probing, hand back immediately.
4. **Advisory-mode override** (`BICAMERAL_GUIDED_MODE=0`): demote all
   probes to informational notes; do not block the calling flow.
5. **Session-drop safety**: queued resolutions that don't fire before
   session end leave durable `collision_pending`/`context_pending` state in
   the ledger. Preflight surfaces them at the next session.
