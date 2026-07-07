# MCP Thin-Client Conformance Checklist (#554)

**Date:** 2026-07-06  
**Status:** current MCP-side checklist for post-GraphStore/search/preflight conformance  
**Scope:** verify `bicameral-mcp` remains a thin ToolRequest client after bot graph, search, binding, and advisory preflight changes.

## Bot Contract Baseline

| Bot reference | Current state | MCP implication |
|---|---|---|
| `bicameral-bot` PR `#157` | Merged to `main` on 2026-06-06. | Original post-GraphStore integration target is no longer speculative. MCP conformance can validate against the ToolRequest read/write split rather than older local graph assumptions. |
| `bicameral-bot` issue `#261` | Closed after PR `#315` implemented `preflight.run` as advisory constraint lookup. | `bicameral.preflight` must map to `preflight.run`, render source/evidence/readiness state, and avoid compliance, signoff, no-conflict, merge-safety, or work-gate claims. |
| `bicameral-bot` issue `#290` | Closed after deferred-command typed-error audit. | MCP must pass through typed unsupported/deferred responses for commands outside the alpha cut line; it must not convert them into success or local fallback behavior. |
| `bicameral-bot` issue `#134` | Still open. Binding search remains intentionally hidden/blocked until bot-owned `BindingEvidence` materialization and evidence-state reporting are real. | MCP may map `bicameral.search` to `search.query`, but must not expose local binding search or silently treat empty binding results as verified absence. |

## Required MCP Checks

| Area | Expected MCP behavior | Evidence |
|---|---|---|
| ToolRequest mapping | Every MCP tool maps to exactly one canonical bot command, with control-plane fields routed into `AuthorityContext` and unknown params stripped. `bicameral.preflight` is the only exception that may dispatch the coverage-guard `lookup.query` before primary `preflight.run`. | `tests/test_toolrequest_conformance.py::test_tool_emits_well_formed_toolrequest` |
| Capability and handshake boundary | Protocol mismatch or daemon unavailability emits typed recovery and sends no ToolRequest. Unsupported/deferred commands are daemon capability errors, not local execution. | `tests/test_toolrequest_conformance.py::test_handshake_failure_dispatches_no_request`; `tests/test_capability_handshake_validation.py` |
| Advisory preflight | `bicameral.preflight` maps to `preflight.run`, renders staged readiness/lookup output, forwards daemon session directives, and does not escalate `enforcement.not_configured` into warn/pause/block behavior. | `tests/test_toolrequest_thin_client.py`; `tests/test_staged_preflight_conformance.py`; `tests/fixtures/toolresponses/preflight.run.json` |
| Search contract | `bicameral.search` maps to `search.query`, renders daemon-provided source links and EvidenceReferences, and preserves binding-scope unsupported/source-only state. | `tests/test_toolrequest_conformance.py::test_search_and_history_fixtures_type_binding_scope_unsupported`; `tests/fixtures/toolresponses/search.query.json` |
| Binding inspection | `bicameral.binding.inspect` maps to `binding.inspect` and renders daemon-authored evidence/readiness states without inferring compliance or source approval. | `tests/test_source_links_581.py`; `tests/fixtures/toolresponses/binding.inspect.json` |
| Graph and CodeGenome authority retirement | MCP does not own CodeGenome, graph indexing, graph readiness, binding materialization, or local graph fallback. | `tests/test_codegenome_authority_retired.py`; `docs/specs/bicameral-mcp-idealized-spec.md` |
| Read-model non-mutation | Read-model tools dispatch only read commands and never route through MCP-owned mutation paths. | `tests/test_toolrequest_conformance.py::test_read_model_tools_map_only_to_read_commands`; `tests/test_toolrequest_conformance.py::test_read_model_tool_dispatches_exactly_one_read_command` |
| Golden path smoke | MCP can perform capture/context, preflight, search, history, and binding inspect through daemon-shaped ToolResponses while preserving source/evidence provenance and avoiding compliance/signoff/merge-safety claims. | `tests/test_advisory_cograph_smoke_580.py` |
| Contract fixture replay | Recorded bot-shaped ToolResponses render through MCP response formatters without a live daemon or LLM. | `tests/test_toolrequest_conformance.py::test_contract_fixture_renders_through_renderer`; `tests/fixtures/toolresponses/` |

## Explicit Non-Goals

- Do not implement graph status, graph refresh, CodeLocator, or binding search locally in MCP.
- Do not treat `search.query` binding-scope absence as verified no binding evidence.
- Do not infer compliance, signoff, global no-conflict, implementation correctness, source approval, or merge safety from lookup, preflight, search, history, or binding inspection output.
- Do not add MCP-local fallback for daemon-deferred or daemon-unsupported commands.
- Do not require a live LLM, external provider, or local bot daemon in required CI for this conformance check.

## Current Finding

No new MCP implementation divergence was found in this pass. The existing deterministic conformance, staged preflight, source-link rendering, CodeGenome retirement, and advisory cograph smoke tests cover the #554 acceptance criteria for the current bot contract surface.

The only material product gap remains bot-owned: `bicameral-bot` issue `#134` keeps binding search blocked until the bot can return real `BindingEvidence` search results with evidence state and readiness warnings. MCP should wait for that daemon-advertised capability instead of introducing a local binding-search path.

## Validation Command Set

Run these before closing #554 or after any future bot contract change:

```bash
python -m pytest tests/test_toolrequest_conformance.py tests/test_toolrequest_thin_client.py tests/test_staged_preflight_conformance.py tests/test_source_links_581.py tests/test_codegenome_authority_retired.py tests/test_advisory_cograph_smoke_580.py -q
python -m ruff check .
python -m ruff format --check .
python -m mypy .
```
