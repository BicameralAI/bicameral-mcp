# Substrate parity fixture (GH #611)

**Why this exists.** #610/#148 capture leans on Claude Code hooks (`SessionEnd`,
`PreCompact`, `PostToolUse`, …). #148's portability bar — "capture must work
across interactive Claude Code, headless `claude -p`, and the hosted/cron runner"
— hinges on a fact the docs do **not** pin down: *which hook events actually fire
under each substrate.* Until that is measured, any portability claim is unproven.

This fixture is the cheap, decisive instrument #611 proposed: a no-op hook on
every event that records `{event, substrate, ts}`, run under each substrate, then
diffed. It converts the assumption into a checked fact and **gates** #148.

## Components

| File | Role |
|------|------|
| `parity_hook.py` | No-op probe. Appends one JSON line per hook firing. Never blocks, always exits 0. |
| `settings.fixture.json` | `.claude` hooks block registering the probe on every event. |
| `diff_parity.py` | Diffs per-substrate logs; **exits non-zero if a capture-critical event is missing** in any substrate. |
| `test_parity_fixture.py` | Deterministic tests of the probe + gate logic (runs in CI; no live substrates needed). |

`CAPTURE_CRITICAL = SessionEnd, PreCompact, Stop, PostToolUse` — the events #610
Phase-1 capture cannot lose. A gap in any of these in any substrate is
portability-blocking.

## Running the three legs

For each substrate, merge the `hooks` block from `settings.fixture.json` into that
substrate's `.claude/settings.json`, then export the two env vars and run the
**same** representative workload (e.g. one implementing prompt that edits a file
and stops).

**1. Interactive (reference):**
```bash
export BICAMERAL_PARITY_SUBSTRATE=interactive
export BICAMERAL_PARITY_LOG="$PWD/parity-interactive.jsonl"
# launch Claude Code interactively, run the workload, end the session
```

**2. Headless `claude -p`:**
```bash
export BICAMERAL_PARITY_SUBSTRATE=headless
export BICAMERAL_PARITY_LOG="$PWD/parity-headless.jsonl"
claude -p "edit README, then stop"   # same workload as the reference
```

**3. Cron / cloud agent:**
```bash
# In the scheduled-agent runner config, set:
#   BICAMERAL_PARITY_SUBSTRATE=cron
#   BICAMERAL_PARITY_LOG=/abs/path/parity-cron.jsonl
# and run the same workload on the schedule, then collect the log.
```

> Note for unattended substrates (cron/`/loop`/cloud): even where capture-critical
> events fire, there is **no operator to ratify at capture time** — captures must
> stay tentative/low-authority (reinforces #58 / the strength-authority axis).

## Diffing + the gate

```bash
python tests/substrate_parity/diff_parity.py \
  --log interactive=parity-interactive.jsonl \
  --log headless=parity-headless.jsonl \
  --log cron=parity-cron.jsonl
# exit 0 = all capture-critical events fired everywhere (portability OK so far)
# exit 2 = a capture-critical event is missing in some substrate (BLOCKS #148)
# add --strict to also fail on non-critical divergences
# add --json to emit a machine-readable report (CI artifact)
```

## What this does and does not prove

- **Proves:** which hook events fire in each substrate that was actually run.
- **Does not prove:** anything about a substrate you didn't run. Coverage of the
  hosted runner is deferred to GA (#610). Until the headless and cron legs are
  run and pass, #148's portability acceptance stays open — do not claim it.
- The git `post-commit` signal (#610 Signal B) is a git hook, not a Claude Code
  hook; verify it separately under each substrate's commit path.
