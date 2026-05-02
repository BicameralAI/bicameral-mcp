# qor-audit — Adversarial Mode (Codex Plugin)

**Status**: Contract-only specification. Full Codex-plugin invocation wiring is reserved for the harness profile (`qor/platform/profiles/claude-code-with-codex.md`).

## Trigger

`qor_audit_runtime.should_run_adversarial_mode()` returns `True` only when:
1. `qor_platform.current()["detected"]["host"] == "claude-code"`, AND
2. `qor_platform.is_available("codex-plugin")` returns `True`.

When False on a `claude-code` host, audit logs a `capability_shortfall` shadow event (severity 2) and falls back to solo mode.

## Input contract (audit → Codex)

The Judge passes the audit subject + verified context to Codex:

```json
{
  "plan_path": "docs/plan-<feature>.md",
  "plan_content_hash": "<sha256>",
  "codebase_snapshot_refs": [
    {"path": "src/<file>", "lines": "1-200"},
    ...
  ],
  "session_id": "<UTC-ISO-MIN>-<6hex>",
  "audit_passes_completed": ["security", "razor", "macro", "orphan", "dependency"],
  "judge_findings_so_far": [
    {"id": "V-1", "category": "razor", "severity": "L2", "claim": "..."},
    ...
  ]
}
```

The plan's content hash must be computed via `qor/scripts/ledger_hash.py hash <plan_path>` so Codex can verify it matches the on-disk plan.

## Output contract (Codex → audit)

Codex returns a structured critique:

```json
{
  "critiques": [
    {
      "severity": "L1|L2|L3",
      "claim_challenged": "Razor estimate undercounts function lines",
      "counter_evidence": "src/foo.ts:45 — function spans 67 lines, exceeds 40 limit",
      "recommended_gap": "V-NEW: file-level Razor breach, mandate /qor-refactor"
    },
    ...
  ],
  "confidence": 0.0,
  "model": "<codex-model-id>",
  "ts": "<ISO-8601>"
}
```

## Synthesis (Judge integrates Codex critique)

After Codex returns:

1. Each critique with `severity >= L2` becomes a new `V-<n>` row in the audit report
2. Critiques that contradict the Judge's existing findings are merged: the Judge re-examines and either accepts (re-classifies severity) or refutes (cites why)
3. The synthesized audit verdict is recorded with mode marker: `**Mode**: adversarial (Codex critique synthesized)` in the report frontmatter
4. The Codex critique payload is preserved at `.qor/gates/<session_id>/audit-codex-critique.json` for traceability (not committed; runtime-only)

## Failure modes

| Mode | Behavior |
|---|---|
| Codex unavailable mid-call | Emit `capability_shortfall` (sev 2); continue solo; flag in verdict that adversarial was attempted-but-aborted |
| Codex returns malformed JSON | Treat as unavailable; emit shortfall; continue solo |
| Codex critique contradicts Judge with no evidence | Judge dismisses critique; logs reason in verdict |
| Codex critique severity > Judge's | Adopt the higher severity; re-classify findings accordingly |

## Wiring

The actual `subprocess` / tool-call invocation that delivers the input contract to Codex and parses the output contract is **not implemented in this repository**. It is documented as a profile binding in `qor/platform/profiles/claude-code-with-codex.md`. When the Codex-plugin invocation surface is finalized in Claude Code, that profile gains a one-line bridge to call this contract.

Until then, `should_run_adversarial_mode()` returns `False` in all observable test environments, and audit runs in solo mode with the shortfall logged.

## See also

- `qor/scripts/qor_audit_runtime.py` — `should_run_adversarial_mode`, `emit_capability_shortfall`
- `qor/gates/schema/audit.schema.json` — audit gate artifact schema (input data lineage)
- `qor/platform/profiles/claude-code-with-codex.md` — profile that wires the actual invocation (when authored)
- `qor/references/doctrine-token-efficiency.md` — Codex critique should be summarized at synthesis time, not pasted in full
