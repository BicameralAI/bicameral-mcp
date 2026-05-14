# Host trust model — MCP host UX is an external dependency

**Status**: active
**Closes gap**: MCP-01 (OWASP LLM-07) per `docs/research-brief-compliance-audit-2026-05-06.md` § 1.1, § 2.4
**Doctrine**: #205 deterministic-governance hard rule

> For the MCP-transport trust boundary specifically (SOC2-01 gap, single-tenant scope statement, team-mode posture), see [`threat-model-and-trust-boundary.md`](threat-model-and-trust-boundary.md). This document is about *host-side* surface dependencies; that document is about *transport-side* tenancy scope.

## Why this document exists

bicameral-mcp's design assumes specific MCP-host UX behaviors (the operator sees tool calls, can deny them, sees server output, can intervene mid-call). **Those surfaces are external to the server** — they live in the MCP host (Claude Code, Cursor, Codex, etc.), not in bicameral-mcp itself. A host that auto-approves tool calls, fails to surface stdout, or lacks a denial path silently bypasses any "the operator will see this" assumption baked into bicameral-mcp's design.

This document enumerates what the server enforces server-side (the deterministic gates) versus what the design assumes the host will surface (the externalized assumptions). Operators and auditors consult this list when evaluating a deployment claim that depends on host-side confirmation surfaces.

## Server-side guarantees

These are enforced inside bicameral-mcp regardless of host behavior. They hold even on hosts that auto-approve tool calls:

| Gate | Surface | Doctrine |
|---|---|---|
| Payload size cap | `bicameral.ingest` refuses payloads exceeding `ingest_max_bytes` (default 1 MiB) | LLM-02 / #216 |
| Token-bucket rate limit | `bicameral.ingest` refuses bursts exceeding `ingest_rate_limit_burst` per session-id | LLM-08 / #216 |
| Prompt-injection canary scan | `bicameral.ingest` refuses payloads matching a curated catalog of known canary patterns | LLM-01 / #212 |
| Sensitive-data detect-and-refuse | `bicameral.ingest` refuses payloads containing secrets / PHI / PAN per the v1 regex catalog | LLM-04 + HIPAA-01 + PCI-01 / #213 |
| Source-attribution redaction | `bicameral.preflight` redacts names and dates in `source_ref` per the `render_source_attribution` config (default: redacted) | #200 Phase 3 + #209 |
| Hooks-manifest signature verification | `setup_wizard._install_*_hooks` verifies cosign-keyless signature on `hooks-manifest.json` before writing host-config files (when bundled) | LLM-11 / #218 Phase 1 |
| Skills-manifest signature verification | `setup_wizard._install_skills` verifies cosign-keyless signature on `skills-manifest.toml` before copying skill content (when bundled); per-file SHA-256 cross-check catches in-place tampering of the installed package directory | LLM-06 / #214 / #218 |
| Bypass tracking | When the agent records a guidance bypass via `record_bypass`, the event is written to `~/.bicameral/preflight_events.jsonl` server-side, regardless of whether the host displays it | #200 Phase 3 |

## Host-side surfaces this design assumes

These are NOT server-enforceable. The server cannot detect, refuse, or compensate for a host that fails to surface them:

1. **Tool-call visibility** — operator sees every MCP tool-call request before it executes. A host that auto-approves invisible tool calls silently bypasses operator review.
2. **Denial path** — operator can deny a tool-call execution mid-flight. A host without a denial UI removes operator control.
3. **Stdout / TextContent surfacing** — operator sees the server's stdout and `TextContent` responses. A host that drops server output to a debug log invisibilizes refusal messages, error context, and bypass-event narratives.
4. **Mid-call intervention** — operator can cancel a long-running tool call. A host without cancel UI traps the operator.
5. **Destructive-action confirmation surface** — destructive tools (`bicameral.reset`) surface via the host's confirmation UI. A host that treats `confirm=True` parameters as auto-approve permission bypasses out-of-band confirmation entirely.

**None of (1)-(5) is server-enforceable.** A host that auto-approves tool calls or fails to surface server output silently invalidates any deployment claim that depended on host-side confirmation surfaces. **Operators choosing a host must verify these assumptions hold before relying on bicameral-mcp's "operator will see this" guarantees.**

## Per-host operator checklist

Verify these surfaces hold for each MCP host you deploy bicameral-mcp on. Update this checklist as host behavior changes.

### Claude Code (CLI)

- [x] Tool-call visibility: each tool call surfaces in the conversation pane before execution
- [x] Denial path: operator can cancel via Ctrl-C or by typing "no" at the approval prompt (when permission mode is interactive)
- [x] Stdout surfacing: server stdout appears in the conversation as `TextContent` responses
- [x] Mid-call intervention: Ctrl-C cancels the active tool call
- [x] Destructive-action confirmation: depends on the operator's chosen permission mode (`acceptEdits`, `default`, `dontAsk`); operators using auto-approve permission modes must understand they bypass per-tool confirmation

### Cursor

- [x] Tool-call visibility: tool calls surface in Cursor's chat sidebar before execution
- [ ] Denial path: depends on Cursor version — verify with current install
- [x] Stdout surfacing: server stdout appears in Cursor's MCP output panel
- [ ] Mid-call intervention: depends on Cursor version
- [ ] Destructive-action confirmation: depends on Cursor's auto-approve config — verify per install

### Codex

- [ ] Tool-call visibility: depends on Codex version — verify with current install
- [ ] Denial path: depends on Codex version
- [ ] Stdout surfacing: depends on Codex version
- [ ] Mid-call intervention: depends on Codex version
- [ ] Destructive-action confirmation: depends on Codex's auto-approve config

### Generic host / non-listed

If the host is not listed above, the operator MUST audit the host's tool-call display, denial, surfacing, intervention, and confirmation behavior before deploying bicameral-mcp. Without that audit, the "operator will see this" guarantees are silently invalid.

## What the #217 epic adds

The "per-tool authority gradation" epic (#217) ships an out-of-band confirmation primitive that does NOT depend on host UX: destructive tools require an explicit operator action (the host's `AskUserQuestion` flow when available, falling back to an interactive terminal prompt or stdin acknowledgement). This closes the gap that THIS document declares — once #217 ships, destructive tools are server-side gated regardless of host auto-approve behavior.

Until #217 lands, operators on auto-approving hosts MUST treat destructive tool calls (`bicameral.reset`, ingest with overwrite semantics) as if no host-side confirmation existed.

## Cross-references

- Research brief: § 1.1 (MCP host UX bullet), § 2.4 (gap MCP-01)
- Related epic: #217 (per-tool authority gradation)
- Doctrine: #205 (deterministic-governance hard rule)
