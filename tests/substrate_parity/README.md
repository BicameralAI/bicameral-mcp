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

## Measured results (2026-06-19, first run)

Probe wired via `--settings` (isolated; live config untouched), tool-using
workload (Read a file, reply) on `claude` 2.1.x.

| event | interactive | headless `claude -p` |
|-------|:-----------:|:--------------------:|
| SessionStart | fired | fired |
| UserPromptSubmit | fired | fired |
| PreToolUse | fired | fired |
| PostToolUse ★ | fired | fired |
| Stop ★ | fired | fired |
| SessionEnd ★ | fired | fired |
| PreCompact ★ | not exercised | not exercised |

`diff_parity.py interactive vs headless` → exit 0, no divergence. **`SessionEnd`
fires under headless `claude -p`** — the load-bearing assumption for #610/#148,
now verified and at parity with interactive.

Honest scope of this run:
- The interactive leg was `claude` (non-`-p`) driven via piped stdin + EOF — it
  exercises the interactive (non-print) hook path but is **one turn**, not a
  human-at-keyboard TTY. A confirmatory true-TTY pass is still worth doing.
- **`PreCompact` is unverified.** It fires only on actual context compaction;
  a trivial workload has nothing to compact, and a piped-stdin `/compact` is not
  processed as a turn. To capture it, run a real interactive session that either
  reaches the auto-compact threshold or issues `/compact` with substantive
  context loaded, with the fixture settings active, then re-run `diff_parity.py`.
- **Cron / hosted-runner leg not run** (deferred to GA per #610). Until the cron
  leg and `PreCompact` are confirmed, #148 full portability stays open — the
  interactive+headless parity above materially de-risks it but does not close it.
