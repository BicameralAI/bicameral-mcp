---
name: qor-tone
description: >-
  Session-level communication tier selector. Sets the output register for tone-aware skills to technical, standard, or plain. Inspired by the MIT-licensed caveman project (https://github.com/JuliusBrussee/caveman).
metadata:
  category: memory
  author: MythologIQ
  source:
    repository: https://github.com/MythologIQ-Labs-LLC/Qor-logic
    path: qor/skills/memory/qor-tone
phase: memory
tone_aware: false
autonomy: interactive
gate_reads: ""
gate_writes: ""
---
# /qor-tone - Communication Tier Selector

<skill>
  <trigger>/qor-tone</trigger>
  <phase>ANY</phase>
  <persona>Governor</persona>
  <output>Session tier set (sticky until changed or session ends)</output>
</skill>

## Purpose

Set the communication tier for the current session. Tone-aware skills branch on the selected tier when rendering user-facing output. Tier selection does NOT apply to evidentiary artifacts (ledger, shadow genome, gate JSON, Merkle seals) -- those stay technical-only for integrity.

See `qor/references/doctrine-communication-tiers.md` for the full doctrine and per-tier rendering rules.

## Execution Protocol

### Step 1: Parse argument

Invocation forms:

- `/qor-tone technical` -- full jargon, SG IDs, citations, hash values
- `/qor-tone standard` -- complete sentences, terms introduced inline, tags minimized
- `/qor-tone plain` -- no jargon, no pattern IDs, short declarative sentences

Any other value is a user error; respond with "Valid tiers: technical, standard, plain" and leave the current tier unchanged.

### Step 2: Apply for the session

The selected tier is sticky for the remainder of the current agent-session window. Subsequent invocations of tone-aware skills render in the selected register until `/qor-tone` is called again or the session ends.

### Step 3: Persist (optional)

If the operator wants the preference to outlive the session, they run `qor-logic init --tone <tier>` to persist into `.qorlogic/config.json`. The CLI flag writes the config; it does not set the current session tier.

### Step 4: Report

Confirm the selection in one line: `Tone set: <tier>. Tone-aware skills will render in this register for the remainder of the session.`

## Scope exclusion

The following are NEVER rendered in a non-technical tier, regardless of session setting:

- `docs/META_LEDGER.md` entries
- `docs/SHADOW_GENOME.md` entries
- `.qor/gates/<session>/*.json` content
- Merkle seal text, chain hashes, content hashes

These artifacts have evidentiary weight and stay technical-only for integrity.

## Constraints

- **NEVER** apply the tier to evidentiary or hash-chained content.
- **NEVER** persist the tier to `.qorlogic/config.json` from this skill; that requires explicit `qor-logic init --tone <tier>` invocation.
- **ALWAYS** treat invalid tier arguments as user errors that leave the current tier unchanged.

## Integration with Qor-logic

- Session-level tier selector complementary to the CLI-level `qor-logic init --tone` persistence.
- Tone-aware skills read the active tier via `qor.tone.resolve_tone()` in Python, or by checking session state in their own rendering preambles.
- Tier selection is orthogonal to the `autonomy` classification: an autonomous skill's emergency surface can still render in the selected tier.
