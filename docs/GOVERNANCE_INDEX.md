# Governance Index

**Last Reviewed**: 2026-06-03

A single authoritative map of every governance artifact in this project, organized
into six freshness tiers with explicit drift contracts. A stale entry here is
itself a Tier 1 drift bug, so the index is self-policing. See
`qor/references/doctrine-governance-index.md` for the model and contracts.

## Tier 1 — Canonical Source

MUST be current at every cycle close. Drift signal: wrong version / wrong state / missing recent entries.

| Artifact | Path | Freshness marker |
|----------|------|------------------|
| Meta Ledger | `docs/META_LEDGER.md` | latest sealed entry |
| System State | `docs/SYSTEM_STATE.md` | latest phase snapshot |
| Concept | `docs/CONCEPT.md` | stable |
| Architecture Plan | `docs/ARCHITECTURE_PLAN.md` | stable |
| Backlog | `docs/BACKLOG.md` | open items current |
| Changelog | `CHANGELOG.md` | latest release stamped |
| README | `README.md` | badges current |
| Feature Index | `docs/FEATURE_INDEX.md` | every feature → a real test; no stale/renamed rows |

> **Adopted 2026-06-06**: `docs/FEATURE_INDEX.md` (Phase-73 artifact) is now live
> (30 entries, every row grounded to a confirmed test). This closes the
> FEATURE_INDEX governance-onboarding debt (issue #538). A stale row — a renamed
> handler, deleted test, or new tool with no row — is a Tier 1 drift bug; update
> the index in the same commit as any tool/handler change.

## Tier 2 — Doctrine & Policy

Stable; changes are explicit doctrine events. Drift signal: rules contradict each other or operator memory.

| Artifact | Path |
|----------|------|
| Project instructions | `CLAUDE.md` |

## Tier 3 — Active Initiative

Live until close; ages out at substantiate. Drift signal: shipped feature still tracked as pending.

| Artifact | Path | Opened |
|----------|------|--------|
| _example_ | `.qor/session/<id>` | [date] |

## Tier 4 — Per-Plan Artifact

Live for plan duration; archived at substantiate. Drift signal: plan shipped but artifact still presents as open.

| Artifact | Path | Plan |
|----------|------|------|
| _example_ | `docs/plan-*.md` | [slug] |

## Tier 5 — Reference Material

Informational, slow-drift. Drift signal: factual claims diverge from current code.

| Artifact | Path |
|----------|------|
| Research briefs | `docs/research-brief-*.md` |
| Compliance audit (2026-06-03) | `docs/COMPLIANCE_AUDIT_2026-06-03.md` |
| Shadow Genome | `docs/SHADOW_GENOME.md` |
| Process Shadow Genome (upstream) | `docs/PROCESS_SHADOW_GENOME_UPSTREAM.md` |

## Tier 6 — Archived

Frozen historical record. Drift signal: none (frozen).

| Archive | Path |
|---------|------|
| _example_ | `docs/archive/` |

## How to add a governance artifact

1. Create the file in the same commit that registers it here.
2. Add a row to the tier whose freshness contract matches the file's lifecycle.
3. Refresh **Last Reviewed** above.

## How to retire a governance artifact

1. Move the file to the Tier 6 archive path.
2. Move its row from its live tier to Tier 6 (or delete it if superseded).
3. Refresh **Last Reviewed** above.
