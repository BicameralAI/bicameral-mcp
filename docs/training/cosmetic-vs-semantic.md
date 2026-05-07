# Cosmetic vs Semantic Drift — Training

## Why this exists

When you change a function's body, Bicameral has to decide whether the
decision bound to that region still holds. The naive answer is "any
content-hash change = drift" — but that flags whitespace edits, comment
rewrites, and import re-orders as drift, eroding trust until the user
ignores every drift signal. The right question is two-axis: *did the code
still do what the decision asked?* (compliance) AND *did the change cross
the cosmetic-or-semantic line?* (semantic_status). This doc teaches the
two-axis judgment because the LLM judge in the `bicameral-sync` skill
needs to internalise it to be useful.

## Prerequisites

- Read `skills/bicameral-sync/SKILL.md` Step 2 ("Resolve every pending
  compliance check") and §2.bis ("Uncertain-band sub-protocol").
- Skim `contracts.py` for the shape of `PreClassificationHint`,
  `ComplianceVerdict`, and `PendingComplianceCheck`.
- Familiarity with `link_commit`'s output shape — specifically that
  `pending_compliance_checks` is the work queue and `auto_resolved_count`
  is what the deterministic Phase 4 classifier already handled.

## The concept

### Phase 4 partitions every drifted region into three buckets

The deterministic classifier in `codegenome/drift_classifier.py` scores a
4-signal weighted sum (signature 0.30, neighbors 0.25, diff_lines 0.30,
no_new_calls 0.15) and writes one of three outcomes:

```
score ≥ 0.80  →  cosmetic   → auto-resolve at link_commit time
                              (compliance_check written; you never see it)
score ≤ 0.30  →  semantic   → emit PendingComplianceCheck with NO hint
                              (clear-cut: ask the LLM about compliance)
0.30 < s < 0.80 → uncertain → emit PendingComplianceCheck WITH hint
                              (this is where you, the LLM, judge)
```

The first bucket is handled deterministically. The second is the standard
compliance flow you already know. **The third bucket is what this doc is
about.**

### Why uncertain needs two-axis reasoning

When you receive a pending check with `pre_classification.verdict ==
"uncertain"`, you have to answer two independent questions:

| Axis | Question | Output field | Possible values |
|---|---|---|---|
| 1. Compliance | Does the new code satisfy the decision? | `verdict` | `compliant` / `drifted` / `not_relevant` |
| 2. Cosmetic-vs-semantic | Was the change a cosmetic edit or a real behaviour change? | `semantic_status` | `semantically_preserved` / `semantic_change` / `None` |

The two axes are mostly independent — but **Axis 1 is decided FIRST**
because `not_relevant` short-circuits Axis 2. If retrieval grabbed a
region that has nothing to do with the decision, the cosmetic-vs-semantic
question is meaningless; pruning the binding is what matters. So:

```
Step 1.  Decide Axis 1.
         - not_relevant?  →  emit verdict=not_relevant, semantic_status=None.
                              The server prunes binds_to. Stop.
         - else            →  go to step 2.
Step 2.  Decide Axis 2 using the hint signals + reading the diff.
         - cosmetic       →  semantic_status=semantically_preserved
                              + verdict=compliant.
         - semantic       →  semantic_status=semantic_change
                              + verdict from Axis 1 (compliant or drifted
                                depending on whether the new logic still
                                meets the decision).
```

### The signals are advisory, not authoritative

`pre_classification.signals` is a `dict[str, float]` carrying each
classifier signal's raw contribution (not its weighted contribution).
High values lean cosmetic; low values lean semantic. **Use them as
evidence, not as the verdict.** The classifier landed in the uncertain
band precisely because the signals couldn't decide on their own; you have
the diff text and the decision description, the classifier doesn't.

## Worked example

The M3 benchmark in `tests/fixtures/m3_benchmark/cases.py` seeds 10
uncertain cases with `expected_judge` ground-truth labels. Walk through
`py_12_constant_value_tuned`:

```python
old = "DISCOUNT = 0.10\ndef apply(p): return p * (1 - DISCOUNT)\n"
new = "DISCOUNT = 0.15\ndef apply(p): return p * (1 - DISCOUNT)\n"
```

Hypothetical bound decision: *"checkout flow applies a 10% discount."*

**What you receive in `pending_compliance_checks`**:

```yaml
- decision_id: dec_checkout_discount
  region_id: rgn_apply_42
  content_hash: <sha>
  decision_description: "checkout flow applies a 10% discount"
  code_body: |
    DISCOUNT = 0.15
    def apply(p): return p * (1 - DISCOUNT)
  pre_classification:
    verdict: uncertain
    confidence: 0.55
    signals:
      signature: 1.00       # function shape unchanged
      neighbors: 1.00       # surrounding code unchanged
      diff_lines: 0.00      # the diff line is NOT comment/whitespace
      no_new_calls: 1.00    # no new callees
    evidence_refs:
      - "score:0.550"
      - "signature:1.00"
      - "neighbors:1.00"
      - "diff_lines:0.00"
      - "no_new_calls:1.00"
```

**Axis 1 reasoning**: the decision is about the discount rate; the code
defines and applies a discount rate. **Relevant.** Continue to Axis 2.

**Axis 2 reasoning**: signals lean cosmetic on three of four axes — but
`diff_lines: 0.00` flags that the changed line is *not* whitespace /
comment / docstring. Reading the diff confirms: the literal `0.10` became
`0.15`. The function body's logic shape is identical, but the constant
value drives observable behaviour. **`semantic_change`.**

**Verdict**: the new code applies a 15% discount, not 10%. The decision
no longer holds. **`drifted`.**

**Final emit**:

```python
bicameral.resolve_compliance(
    phase="drift",
    flow_id="...",
    verdicts=[{
        "decision_id":     "dec_checkout_discount",
        "region_id":       "rgn_apply_42",
        "content_hash":    "<sha>",  # echo exactly
        "verdict":         "drifted",
        "confidence":      "high",
        "explanation":     "DISCOUNT constant changed from 0.10 to 0.15; decision specifies 10%.",
        "semantic_status": "semantic_change",
        "evidence_refs":   ["score:0.550", "signature:1.00", "diff_lines:0.00", ...],
    }],
)
```

The `expected_judge` for `py_12_constant_value_tuned` in
`cases.py` declares this exact pair: `{"verdict": "drifted",
"semantic_status": "semantic_change"}`. If your LLM emits the same pair,
the operator QC pass for this case is green.

## Common pitfalls

1. **Skipping Axis 1 and emitting `semantic_status` for a `not_relevant`
   region.** This pollutes the audit trail with cosmetic-vs-semantic
   claims about regions that aren't even about the decision. The server
   accepts the verdict (Pydantic doesn't enforce the cross-field rule)
   but the data is meaningless.

2. **Trusting the signals over the diff.** The signals are advisory by
   design — three of four signals leaned cosmetic in the worked example
   above, yet the change was semantic. Always read the diff.

3. **Forgetting to echo `evidence_refs`.** The hint's `evidence_refs` are
   the audit trail of the deterministic→LLM hand-off. Drop them, and you
   lose the ability to debug which signal misled the classifier later.
   Echo them; the server merges your additional refs with theirs.

4. **Conflating `compliant + semantic_change` with `drifted`.** A
   semantic change can still satisfy the decision (e.g. method now async
   but the contract is preserved). `semantic_status` is about the change;
   `verdict` is about the decision. They can disagree.

5. **Re-classifying cosmetic-band cases.** If you see
   `pre_classification.verdict == "cosmetic"`, the deterministic
   classifier already auto-resolved it — you should never see one in
   `pending_compliance_checks`. If you do, the auto-resolution path is
   broken; report a bug rather than emit a verdict.

## See also

- `skills/bicameral-sync/SKILL.md` §2.bis — the rubric in normative form
- `codegenome/drift_classifier.py` — the deterministic classifier whose
  uncertain-band output you consume
- `contracts.py` — `PreClassificationHint`, `ComplianceVerdict`,
  `PendingComplianceCheck` typed contracts
- `tests/fixtures/m3_benchmark/cases.py` — 10 uncertain cases with
  `expected_judge` ground-truth labels
- BicameralAI/bicameral-mcp#44 — issue
- BicameralAI/bicameral-mcp#61 — Phase 4 of CodeGenome (upstream
  classifier)
