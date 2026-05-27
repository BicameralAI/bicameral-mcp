---
name: bicameral-bind
description: Pin a decision to a specific code region via the `bicameral_bind` tool. Use after ingest when a decision wasn't pinned at extraction time, when a decision's intent now applies to existing code, or after a refactor moved a previously-bound symbol. Mandatory pre-bind verification — Read the candidate file, confirm the symbol via `validate_symbols`, abort on weak evidence. The handler rejects bindings whose `symbol_name` does not resolve at the supplied span (#280) — silent acceptance was the M2 grounding precision regression that motivated this contract.
---

# Bicameral Bind

Pin a decision to a specific code region via the `bicameral_bind` tool.

## When to use

- After ingest when a decision wasn't pinned at extraction time (e.g. ingested in
  natural format with no `code_regions`)
- When a decision's intent now applies to existing code that didn't exist at
  ingest time (deferred grounding)
- After a refactor that moved a previously-bound symbol — re-bind to the new
  location rather than letting drift detection re-fire on every preflight

## When NOT to use

- During `bicameral.ingest` itself — pass `code_regions` on the ingest payload
  in **internal format** (see `skills/bicameral-ingest/SKILL.md` §3). Binding
  during ingest is one round-trip; binding after is two.
- For decisions that are genuinely abstract ("ship by Q3", "SOC2-compliant
  session storage") and don't yet point at code — leave them ungrounded.
  Honest empty state beats a false binding.

## Mandatory pre-bind verification

The handler rejects unverified bindings (#280). To avoid the rejection path,
**before EVERY `bicameral_bind` call:**

1. **Generate symbol hypotheses from the decision text.** If the decision says
   *"all email dispatch functions filter via a single source-of-truth check,"*
   your hypotheses are `dispatchReminders`, `dispatchInterventions`,
   `dispatchNudge`, `resolveMemberStatus`, `isActiveSubscriber` — not just
   the literal word "dispatch."
2. **Read at least one candidate file end-to-end.** Use the Read tool — not a
   grep snippet, not a fuzzy match. Confirm the candidate symbol's body
   actually implements the decision's intent.
3. **Confirm the symbol via `validate_symbols`.** A grep match is not proof
   of existence in the symbol index; the index is the source of truth and is
   what the handler queries to verify your binding. Each result now includes
   `indexed_at_sha` — the git commit the symbol index was built against.
   **Compare it against the `authoritative_sha` from your most recent
   `link_commit` response.** If they differ, the index is ahead of (or behind)
   the ref the bind handler will validate at, and bind may reject a symbol
   that `validate_symbols` confirmed — see "Snapshot drift" below.
4. **If the candidate is ambiguous (multiple symbols match the intent),
   `get_neighbors` to resolve scope** before binding. Surfaces callers and
   callees so you can tell whether the decision is local to one function or
   spans a call tree.
5. **Abort on weak evidence.** If you can't point to a specific function body,
   class definition, or module-level statement that implements the decision,
   do NOT bind. Leave the decision ungrounded — a future ingest or
   `bicameral_bind` call can pin it later.

## Snapshot drift (#334)

`validate_symbols` reads from the local SQLite symbol index (built and stamped
by `code_locator_runtime.record_index_state`). `bicameral_bind` resolves
symbols via `git show {authoritative_sha}:{file_path}` + tree-sitter. These
are two different data sources at two different refs — when they disagree, a
caller can satisfy `validate_symbols` (score 100) and still hit a hard
rejection from `bind`.

Each `validate_symbols` result now carries `indexed_at_sha`. Use it:

- **If `indexed_at_sha == authoritative_sha`:** safe to proceed — the index
  was built against the same commit bind will validate at.
- **If `indexed_at_sha` differs from `authoritative_sha`:** the index is
  drift-prone for this binding. Proceed only when you have independent
  evidence the symbol exists at `authoritative_sha` (Read the file at that
  ref; check `git log` for the introducing commit). Prefer re-indexing
  (`python -m code_locator index <repo_path>`) over guessing.
- **If `indexed_at_sha == ""`:** the index pre-dates ref tracking (legacy
  build, or a `record_index_state` call was skipped). Treat as snapshot-
  unknown — same caution as the drift case.

Skipping this check is what made the field bug in #334 (Jacob, 2026-05):
`validate_symbols` returned `score=100` for a symbol introduced on a feature
branch after the most recent `link_commit`. The caller bound and got
`"not found at <authoritative_sha>"`.

## Anti-patterns — REJECT these

| Anti-pattern | Why it fails |
|---|---|
| Binding to a file because the file name keyword-matches the decision | File-level pins lose the symbol-level signal that drift detection needs |
| Binding to a symbol because its name fuzzy-matches the decision text | A `dispatch` reducer is not the same as email dispatch — vocab overlap is not implementation overlap |
| Submitting a binding with `symbol_name` you only saw in a grep result | The handler verifies symbols via the symbol index, not grep — the call will be rejected |
| Submitting a binding with `start_line`/`end_line` ranges that don't overlap the symbol's resolved body | Pre-#280 this was silently accepted; now the handler rejects with `span mismatch` |
| Binding ahead of code ("we'll bind it once the function lands") | Bind to existing code only. The handler rejects bindings to files that don't exist at the SHA |

## Format

`bicameral_bind` accepts a list of `bindings`. Each binding:

```
{
  "decision_id": "...",          # required — must exist in the ledger
  "file_path": "src/lib/...",    # required — must exist at HEAD (or ref)
  "symbol_name": "ProcessOrder", # required — must resolve via validate_symbols
  "start_line": 42,              # optional — if omitted, tree-sitter resolves
  "end_line": 89,                #            from symbol_name
  "purpose": "implements ..."    # optional — short rationale, indexed
}
```

**Rules:**

- All three of `decision_id`, `file_path`, `symbol_name` are required.
- If you omit `start_line`/`end_line`, the handler resolves them via
  tree-sitter from `symbol_name`. This is the safest call — let the handler
  pin the exact span.
- If you supply `start_line`/`end_line`, the handler still resolves the
  symbol via tree-sitter and rejects unless your supplied span overlaps the
  resolved span (#280 — closes the silent-acceptance branch where caller
  hallucinated a wrong/non-existent symbol on a real file).
- Submit only bindings to **existing** code at the authoritative SHA.
  Hypothetical files / future code is rejected.

## Handler-side enforcement (#280)

The handler rejects with a structured `error` (and `region_id=""`) when:

| Condition | Error message contains |
|---|---|
| `decision_id` is empty | `"decision_id, file_path, and symbol_name are required"` |
| `decision_id` doesn't exist in the ledger | `"unknown_decision_id"` |
| `file_path` doesn't exist at the SHA | `"does not exist at <sha> — only bind to existing code"` |
| `symbol_name` doesn't resolve via tree-sitter | `"symbol '<name>' not found in <file> at <sha>"` |
| Caller-supplied span doesn't overlap resolved span | `"span mismatch (#280)"` |

Errors are returned per-binding (the handler keeps processing the rest of the
list); a partial response is normal when one binding fails.

## After binding

Each successful binding returns a `pending_compliance_check` payload — read
the bound code at `file_path` lines `start_line`-`end_line` and verify it
actually implements the decision, then call `bicameral.resolve_compliance`
with your verdict. See `skills/bicameral-sync/SKILL.md` for the verification
contract.

Unverified bindings stay in `pending` state — they don't yet count as
`reflected`, so preflight retrieval and drift detection treat them as
provisional until the verdict lands.

## Example

User: "Bind decision dec-checkout-retry-cap to the rate limiter — it caps
checkout retries at 3 per Stripe's contract."

Procedure:
1. Hypotheses from text: `RateLimiter`, `checkout_rate_limit`, `retry_cap`,
   `process_checkout`, `_check_retry_count`.
2. Grep for hypotheses → `src/checkout/rate_limiter.py:CheckoutRateLimiter`
   and `src/checkout/retry.py:CheckoutRetryGuard` are candidates.
3. Read `src/checkout/rate_limiter.py` end-to-end → it limits requests
   per minute, not retries per session. Wrong candidate.
4. Read `src/checkout/retry.py` end-to-end → `CheckoutRetryGuard.check_cap()`
   raises `MaxRetriesExceeded` after 3 attempts. ✓ matches intent.
5. Call `validate_symbols(["CheckoutRetryGuard", "check_cap"])` → confirms
   both exist, `check_cap` is at lines 24–47.
6. Bind:
   ```
   bicameral_bind(bindings=[{
     "decision_id": "dec-checkout-retry-cap",
     "file_path": "src/checkout/retry.py",
     "symbol_name": "CheckoutRetryGuard.check_cap",
     "purpose": "implements 3-retry cap per Stripe contract clause"
   }])
   ```
7. Read pending check, call `bicameral.resolve_compliance` with
   `compliant=True, confidence=high` and a one-sentence explanation.
