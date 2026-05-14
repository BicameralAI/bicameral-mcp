# GDPR Art. 17 right-to-erasure — implementation roadmap

**Status: Phase 1 of 3 shipped (2026-05-14). GDPR-01 audit gap remains OPEN until Phase 3 completes and backfill runs.**

This document is the operator-facing roadmap for closing the
**GDPR-01** audit gap identified in
[`docs/research-brief-compliance-audit-2026-05-06.md`](../research-brief-compliance-audit-2026-05-06.md) § 2.1.
It is **not** a closure claim — closure is recorded only when Phase 3
ships and the migration backfill completes.

## Operator directive

> "Keep PII OUT of the ledger by ingress filtering + storage segregation,
> NOT by tombstone/rehash mechanics on the append-only chain."
> — operator memory `issue_221_design_directive`, 2026-05-13

Of the three remediation options in [#221](https://github.com/BicameralAI/bicameral-mcp/issues/221):

- **(i) Tombstone-and-rebuild** — rejected. Mutates the append-only chain.
- **(ii) Crypto-shredding** — partial adoption (structural mechanism, no per-row key surface).
- **(iii) Scope-out via ingress detect-and-refuse** — already partially in place via [#213](https://github.com/BicameralAI/bicameral-mcp/issues/213) (PHI/PAN detect-and-refuse). Used as defense-in-depth, not the load-bearing mechanism.

The roadmap below implements a hybrid of (ii) and (iii): **storage
segregation** carries the structural guarantee; **ingress filtering**
is the first line of defense.

## Phase 1 — foundation (this cycle, 2026-05-14)

**Shipped:**

- `pii_archive/` Python module — operator-erasable SQLite store at
  `~/.bicameral/pii-archive.db` (env-override
  `BICAMERAL_PII_ARCHIVE_PATH`).
- `input_span.archive_key` additive schema field (default `''`).
  Schema version bumped from 19 to 20.
- Roadmap doc (this file) — declares Phase 1/2/3 scope and gap status.
- 13 sociable tests against real SQLite + memory:// ledger.

**Explicitly NOT shipped in Phase 1:**

- No ingest wiring. The current `handlers/ingest.py` path is unchanged;
  new rows still get `archive_key=''` and `input_span.text` populated
  as before.
- No read-path migration. Consumers of `input_span.text` continue to
  read it directly.
- No `bicameral-mcp erase-subject` CLI.
- No migration backfill of legacy rows.

**Gap-closure status after Phase 1**: GDPR-01 remains OPEN.

## Phase 2 — ingest cutover (next cycle)

**Will ship:**

- Schema change: `input_span.text` becomes optional; new ASSERT
  `archive_key != '' OR text != ''`; UNIQUE index swaps from
  `(source_type, source_ref, text)` to a conditional UNIQUE on
  `archive_key` where `archive_key != ''`.
- `handlers/ingest.py` writes PII to the archive (sets `archive_key`)
  and stores `''` in `input_span.text` for new rows.
- Speakers and `source_ref` pseudonymization for new `decision` rows
  (`decision.speakers` becomes a list of archive-keyed handles, not
  raw names/emails).
- Read-path migration: `ledger/queries.py` introduces
  `_resolve_span_text(row, archive)` and all consumers route through
  it. Empty-text-but-archive-key-set rows return archive content; legacy
  rows fall back to `input_span.text`.
- Cross-author replay sanitizer: `events/materializer.py` pushes peer
  event text into the **local** archive before write, never inline-
  persists peer PII into the structural ledger.
- Governance gate (`governance-gates.yaml`) declared: the schema ASSERT
  is the deterministic enforcement point.

**Gap-closure status after Phase 2**: GDPR-01 still OPEN. New ingests
are erasable in principle but the CLI surface hasn't shipped yet.

## Phase 3 — operator-facing erasure (cycle after)

**Will ship:**

- `bicameral-mcp erase-subject` CLI subcommand:
  - Predicates: `--speaker SUBSTRING | --source-ref SUBSTRING | --archive-key KEY`.
  - Required `--reason "..."` flag for legitimate-interest documentation.
  - Optional `--retain-with-reason "..."` flag for Art. 17(3)
    legitimate-interest retention claims (audited but does not erase).
  - Interactive `--yes` / `--confirm` to prevent accidental erasure.
- Migration backfill: one-shot script that walks all `input_span` rows
  with `archive_key=''`, copies `text` into the archive, and sets
  `archive_key`. After backfill, every row in the ledger is reachable
  by the CLI.
- Audit-log emission: every erasure emits a
  `GDPR_ERASURE` event with predicate-hash (not predicate),
  count, reason, and operator-identity. The audit log is itself a
  no-PII surface.
- Operator-facing doc `docs/policies/gdpr-art-17-erasure.md` covering
  the runbook for a Data Subject Access Request → erasure flow.

**Gap-closure status after Phase 3**: GDPR-01 closed once backfill
completes on the operator's specific ledger. Audit reviewers should
verify the backfill ran by inspecting `audit_log` for the migration
event.

## What's deliberately out of scope of all three phases

- **JSONL event-log erasure.** The per-author `.bicameral/events/<email>.jsonl`
  files are a separate Art. 17 surface; operators handle them via filesystem
  tooling (`rm`, redaction scripts) or via a future
  `bicameral-mcp ledger-export --redact` pipeline. Tracked separately if
  evidence shows demand.
- **Per-row encryption (full crypto-shredding).** Option (ii) in #221's
  full form requires per-row key management; the issue explicitly defers
  this as out-of-scope-by-default. Future cycles may revisit if a customer
  contract demands it.
- **Ingress filter strengthening** for free-form PII (names/emails without
  PHI/PAN labels). The existing `_check_sensitive` filter is best-effort;
  storage segregation is the load-bearing guarantee. Strengthening the
  filter is a separate cycle gated on evidence.
- **`decision.description` and `decision.rationale` erasure.** These
  fields hold *structural intent* (what was decided), not raw transcribed
  source. The discipline is that operator-authored intent doesn't carry
  PII; if it does, that's an operator-side hygiene issue, not a substrate
  gap.

## Refs

- Audit gap: [`docs/research-brief-compliance-audit-2026-05-06.md`](../research-brief-compliance-audit-2026-05-06.md) § 2.1 (GDPR-01)
- Issue: [#221](https://github.com/BicameralAI/bicameral-mcp/issues/221)
- Doctrine: [#205](https://github.com/BicameralAI/bicameral-mcp/issues/205) (deterministic governance)
- Related ingress filter: [#213](https://github.com/BicameralAI/bicameral-mcp/issues/213) (PHI/PAN detect-and-refuse)
- Plan artifact (Phase A): `plan-221-gdpr-right-to-erasure.md` at repo root
