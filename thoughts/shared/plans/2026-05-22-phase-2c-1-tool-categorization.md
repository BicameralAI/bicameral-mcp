# Phase 2c-1 — Read/Write Tool Categorization

**Date**: 2026-05-22
**Branch**: `feat/daemon-02c-1-tool-categorization` (off `origin/dev`)
**Parent plan**: `~/github/bicameral/thoughts/shared/plans/2026-05-21-daemon-extraction-and-universal-ingest-egress.md`
**Predecessors**:
- Phase 1 (universal protocol contracts) — merged in cfa1f30
- Phase 2a (daemon scaffolding) — merged in c3f337e
- Phase 2b (MCP adapter shells + tenant-aware bootstrap) — merged in 391aad2

**Successors (this phase blocks)**:
- Phase 2c-2 — physical code moves into `daemon/`
- Phase 3 — repo split

---

## Goal

Tag every externally-callable handler with a `@read_tool` or `@write_tool` decorator and reshape the protocol namespace into five typed subspaces:

| Prefix                | Intent                                                      |
|-----------------------|-------------------------------------------------------------|
| `read.*`              | Ledger reads (no state mutation, no side effects)           |
| `write.*`             | Ledger writes (mutates decisions/sources/regions/bindings)  |
| `grounding.lookup.*`  | Deterministic code-locator primitives (symbol lookups)      |
| `grounding.analyze.*` | L1-L3 drift / region analysis (semantic-grounding work)     |
| `system.*`            | Daemon meta + lifecycle (attach, version, dashboard, reset) |

This is the *foundation* for Phase 2c-2's physical move — once categories are typed, the daemon can route reads through a replica reader and writes through the single-writer queue without ambiguity.

**Out of scope**:
- Moving handler bodies into `daemon/` (Phase 2c-2)
- Wiring the read/write methods into the JSON-RPC server (the methods will be *registered* but the bodies still proxy to today's handlers; switchover happens in 2c-2)
- Renaming the user-facing MCP tool names (`bicameral.ingest` etc. stay as-is — see "Compatibility")

---

## Categorization

Mapping today's `bicameral.*` MCP tools onto the new protocol namespace. The MCP tool name is what callers see; the protocol method is what travels over the UDS socket.

### `read.*` (no mutation, no side effects)

| MCP tool                  | Protocol method     | Handler                          |
|---------------------------|---------------------|----------------------------------|
| `bicameral.history`       | `read.history`      | `handlers.history.handle_history` |
| `bicameral.usage_summary` | `read.usage_summary`| `handlers.usage_summary.handle_usage_summary` |

`bicameral.preflight` moves to `grounding.analyze.preflight` (see below) — the drift analysis it triggers is core to its job and the dependency on grounding should be visible at the wire level.

### `write.*` (mutates ledger state)

| MCP tool                       | Protocol method                | Handler |
|--------------------------------|--------------------------------|---------|
| `bicameral.ingest`             | `write.ingest`                 | `handlers.ingest.handle_ingest` |
| `bicameral.link_commit`        | `write.link_commit`            | `handlers.link_commit.handle_link_commit` |
| `bicameral.ratify`             | `write.ratify`                 | `handlers.ratify.handle_ratify` |
| `bicameral.judge_gaps`         | `write.judge_gaps`             | `handlers.gap_judge.handle_judge_gaps` |
| `bicameral.resolve_compliance` | `write.resolve_compliance`     | `handlers.resolve_compliance.handle_resolve_compliance` |
| `bicameral.resolve_collision`  | `write.resolve_collision`      | `handlers.resolve_collision.handle_resolve_collision` |
| `bicameral.remove_decision`    | `write.remove_decision`        | `handlers.remove_decision.handle_remove_decision` |
| `bicameral.remove_source`      | `write.remove_source`          | `handlers.remove_source.handle_remove_source` |
| `bicameral.feedback`           | `write.feedback`               | `handlers.???` (today inline in server.py) |
| `bicameral.skill_begin`        | `write.skill_begin`            | `handlers.???` (today inline in server.py) |
| `bicameral.skill_end`          | `write.skill_end`              | `handlers.???` (today inline in server.py) |

### `grounding.lookup.*` (deterministic symbol lookup; writes are scoped to grounding metadata only)

| MCP tool / RPC          | Protocol method                  | Handler |
|-------------------------|----------------------------------|---------|
| `validate_symbols`      | `grounding.lookup.validate_symbols` | `code_locator.tools.validate_symbols` |
| `get_neighbors`         | `grounding.lookup.get_neighbors`    | `code_locator.tools.get_neighbors` |
| `bicameral.bind`        | `grounding.lookup.bind`             | `handlers.bind.handle_bind` |

`bicameral.bind` writes a binding row, but it's a *grounding-shaped* write — the daemon will eventually route it through the grounding port rather than the generic write queue. Keeping it under `grounding.lookup.*` keeps that affinity visible. If the user prefers `write.bind`, we move it — flagged as an open question below.

### `grounding.analyze.*` (drift detection + region analysis)

These RPCs already exist in `protocol/contracts.py` as `AnalyzeRegionRequest` / `BatchAnalyzeRequest` / `DriftResult` but have no method-name slot yet.

| MCP tool / RPC          | Protocol method                  | Handler |
|-------------------------|----------------------------------|---------|
| `bicameral.preflight`   | `grounding.analyze.preflight`    | `handlers.preflight.handle_preflight` |
| (daemon-internal)       | `grounding.analyze.region`       | preflight, detect_drift |
| (daemon-internal)       | `grounding.analyze.batch`        | scan_branch, link_commit |
| (daemon-internal)       | `grounding.analyze.extract_symbols` | bind, link_commit |

Pinning preflight here surfaces the L1-L3 dependency at the wire level: any adapter that wants preflight semantics has to go through the grounding port, not the cheap read path.

### `system.*` (daemon lifecycle + meta — not an adapter surface)

| MCP tool / RPC          | Protocol method        | Handler |
|-------------------------|------------------------|---------|
| (existing)              | `system.version`       | (in protocol/server.py) |
| (existing)              | `system.attach`        | (in protocol/server.py) |
| `bicameral.update`      | `system.update`        | `handlers.update.handle_update` |
| `bicameral.reset`       | `system.reset`         | `handlers.reset.handle_reset` |
| `bicameral.diagnose`    | `system.diagnose`      | `handlers.diagnose.handle_diagnose` |
| `bicameral.dashboard`   | `system.dashboard`     | (today inline in server.py) |

Rationale for `system`: these affect daemon process state, not ledger rows. They aren't called by adapters — they're invoked by the CLI or session bootstrap.

### Internal helpers (NO decorator)

Files in `handlers/` that aren't user-facing tools — they don't get tagged:
- `analysis.py`, `action_hints.py`, `canary_patterns.py`, `sensitive_patterns.py` — pure helpers
- `decision_status.py`, `search_decisions.py`, `detect_drift.py` — internal queries called by other handlers
- `sync_middleware.py` — middleware

The decorator is the public-surface contract. Decorating helpers would muddy the categorization invariant.

---

## Implementation

### File-level changes

1. **`protocol/categorization.py`** (new, ~80 LOC)
   ```python
   READ_PREFIX = "read."
   WRITE_PREFIX = "write."
   GROUNDING_LOOKUP_PREFIX = "grounding.lookup."
   GROUNDING_ANALYZE_PREFIX = "grounding.analyze."
   SYSTEM_PREFIX = "system."

   def read_tool(method: str) -> Callable: ...
   def write_tool(method: str) -> Callable: ...
   def grounding_lookup(method: str) -> Callable: ...
   def grounding_analyze(method: str) -> Callable: ...
   def system_tool(method: str) -> Callable: ...
   ```
   Each decorator attaches `__bicameral_protocol_method__` + `__bicameral_protocol_category__`
   on the wrapped function. No runtime behavior change — pure metadata.

2. **`handlers/*.py`** — apply one decorator per externally-callable `handle_*` function. ~15 handlers touched, single-line edit each.

3. **`protocol/contracts.py`** — add request/result models for the methods that don't have them yet:
   - `read.preflight`, `read.history`, `read.usage_summary`
   - `write.ratify`, `write.judge_gaps`, `write.resolve_compliance`, `write.resolve_collision`,
     `write.remove_decision`, `write.remove_source`, `write.feedback`,
     `write.skill_begin`, `write.skill_end`
   - `grounding.lookup.bind`
   - `system.update`, `system.reset`, `system.diagnose`, `system.dashboard`

   Mirror the existing handler signatures; reuse types where the handler already returns Pydantic.

4. **`protocol/server.py`** — extend the existing dispatcher's `_PRE_ATTACH_ALLOWED` to include `system.version` + `system.attach` (already there) — no other changes in 2c-1. Concrete handler registration is deferred to 2c-2 when the daemon owns the bodies.

5. **`tests/test_protocol_categorization.py`** (new, ~150 LOC):
   - Every `handle_*` in `handlers/` either has exactly one categorization decorator OR is on the explicit "internal helpers" allowlist (the allowlist lives next to the test).
   - Decorator's declared method name has the right prefix (`read.foo` → must be a `@read_tool`, etc.).
   - Every protocol method registered in `contracts.py` is referenced by exactly one decorated handler (no orphans, no duplicates).
   - Smoke test: import every handler module — no import errors.

### Branch scope

LOC budget: ~300 production + ~150 test. The estimate from the parent plan checks out.

### Skill updates

CLAUDE.md mandates `skills/*/SKILL.md` updates for tool behavior changes. Phase 2c-1 doesn't change tool behavior — the MCP tool names + responses are identical. **No skill changes required for 2c-1.** Skill updates land with 2c-2 when the daemon actually routes the calls.

---

## Compatibility

- **MCP tool names unchanged** — `bicameral.ingest` etc. remain the wire name to caller LLMs. Only the *protocol method* name (UDS) gets the new prefix. Skills and CLI continue working unchanged.
- **Reversible** — the decorators are additive metadata. Reverting is `git revert` on a single PR.

---

## Resolved design decisions (2026-05-22 with Jin)

1. **`bicameral.bind` → `grounding.lookup.bind`** — grounding-shaped write, daemon will route through grounding port.
2. **`bicameral.preflight` → `grounding.analyze.preflight`** — adapter-author wire surface should reflect the L1-L3 dependency, not just the read-shaped output.
3. **Inline handlers extracted** — `feedback`, `skill_begin`, `skill_end`, `dashboard` get moved out of `server.py` into `handlers/` files so the invariant "every decorated handler lives in `handlers/`" holds.

These names are *adapter-author-facing*, not end-user-facing. End-user MCP tool names (`bicameral.preflight` etc.) are unchanged.

---

## Acceptance checklist

- [ ] `protocol/categorization.py` exports five decorators + prefix constants
- [ ] Every externally-callable handler is decorated exactly once
- [ ] `tests/test_protocol_categorization.py` covers: exactly-one-decorator, prefix-matches-decorator, no-orphan-methods, no-duplicate-methods, smoke-import
- [ ] `pytest tests/test_protocol_categorization.py` is green
- [ ] Full `pytest -q` still passes (no regressions)
- [ ] `ruff check` clean
- [ ] PR opened against `dev`; CI green ~30s after push
