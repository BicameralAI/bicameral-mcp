---
name: qor-shadow-process
description: Append-only process failure log; threshold-triggered GitHub issue creation; cross-repo aggregation.
user-invocable: true
category: governance
requires: []
enhances_with: []
gate_reads: []
gate_writes:
  - docs/PROCESS_SHADOW_GENOME.md
  - docs/PROCESS_SHADOW_GENOME_UPSTREAM.md
phase: governance
tone_aware: false
---
# /qor-shadow-process — Process Shadow Genome Recorder

<skill>
  <trigger>/qor-shadow-process</trigger>
  <phase>governance (cross-cutting; invoked by other skills and auto-triggers)</phase>
  <persona>Judge</persona>
  <output>Appended event in docs/PROCESS_SHADOW_GENOME.md or docs/PROCESS_SHADOW_GENOME_UPSTREAM.md (JSONL)</output>
</skill>

## Purpose

Record **process-level** failures (distinct from `docs/SHADOW_GENOME.md` which records **audit-verdict** failures). Analogous to the Shadow Genome concept but at the meta-process layer — failures of how we work, not failures in the artifact itself.

Events flow into a threshold-gated GitHub issue pipeline (see `qor/scripts/check_shadow_threshold.py` and `create_shadow_issue.py`).

## Event Schema

```json
{
  "ts": "ISO-8601 UTC",
  "skill": "qor-<name>",
  "session_id": "<UTC-ISO-MIN>-<6hex>",
  "event_type": "gate_override | regression | hallucination | degradation | capability_shortfall | aged_high_severity_unremediated",
  "severity": 1-5,
  "details": {},
  "addressed": false,
  "issue_url": null,
  "addressed_ts": null,
  "addressed_reason": null,
  "source_entry_id": null
}
```

**Severity rubric**: gate_override=1, capability_shortfall=2, regression=3, hallucination=4, degradation=5.

**`addressed` state machine**:
- `false → true (issue_created)` — any severity; threshold trips (sum ≥ 10)
- `false → true (resolved_without_issue)` — any severity; operator action via `/qor-remediate`
- `false → true (stale_expired)` — severity 1–2 only; 90 days unaddressed
- Severity ≥ 3 never stale-expires
- Reverse transitions forbidden (re-opening requires a new event)

## Execution Protocol

### Recording an event

Invoke `qor/scripts/shadow_process.py` via its library interface:

```python
from qor.scripts import shadow_process
event = {
    "ts": shadow_process.now_iso(),
    "skill": "qor-audit",
    "session_id": "<UTC-ISO-MIN>-<6hex>",
    "event_type": "gate_override",
    "severity": 1,
    "details": {...},
    "addressed": False,
    "issue_url": None,
    "addressed_ts": None,
    "addressed_reason": None,
    "source_entry_id": None,
}
event_id = shadow_process.append_event(event, attribution="UPSTREAM")
# or attribution="LOCAL" — classify per qor/references/doctrine-shadow-attribution.md
```

Before appending, classify attribution per `qor/references/doctrine-shadow-attribution.md`. UPSTREAM events go to `docs/PROCESS_SHADOW_GENOME_UPSTREAM.md`. LOCAL events go to `docs/PROCESS_SHADOW_GENOME.md`. When in doubt, LOCAL.

Validates against `qor/gates/schema/shadow_event.schema.json`, computes `id` = SHA256 of canonical event fields, appends atomically via `os.replace()`.

### Threshold check (periodic or post-skill-run)

```bash
python -m qor.scripts.check_shadow_threshold
```

- Applies stale expiry: severities 1–2 unaddressed > 90 days → `addressed=true`, `addressed_reason="stale"`
- Applies aged-high-severity self-escalation: severities ≥ 3 unaddressed > 90 days → emit one `aged_high_severity_unremediated` (sev 5) per source (idempotent via `source_entry_id`)
- Sums unaddressed severity. If ≥ 10: writes `.qor/remediate-pending` marker, exits 10.

Exit 10 is the `/qor-remediate` trigger signal. Skills consuming this script should check the exit code; the marker file persists across invocations so later skills see `Path(".qor/remediate-pending").exists()` and can prompt the user.

### Issue creation (manual or via /qor-remediate)

```bash
python -m qor.scripts.create_shadow_issue
```

- Validates `gh auth status`
- Reads marker, aggregates events, builds structured issue body
- `gh issue create --repo MythologIQ-Labs-LLC/Qor-logic --label qor-shadow`
- Flips matched events to `addressed=true`, `addressed_reason="issue_created"`, `issue_url=<url>`
- Removes marker

## Constraints

- **NEVER** modify existing events (append-only; use `addressed=true` to close)
- **NEVER** delete events
- **ALWAYS** use atomic write primitive (`os.replace`)
- **ALWAYS** validate against schema before append
- **ALWAYS** compute deterministic id; identical events produce identical ids

## Tested behaviors

See `tests/test_shadow.py` (18 tests): id determinism, schema validation, threshold sum, severity-gated expiry, self-escalation idempotence, marker lifecycle, issue creation flipping `addressed`.
