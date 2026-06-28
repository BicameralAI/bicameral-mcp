# Bicameral

Bicameral captures implementation-constraining decisions from product, code, and collaboration evidence, then routes them through review into a durable event authority.

## Language

**Decision**:
A binding constraint on implementation. Not a suggestion, opinion, note, or general product knowledge.
_Avoid_: note, feedback, request, product knowledge

**DecisionCandidate**:
An extracted claim that has not yet been accepted into the Decision Ledger. It is non-canonical until governance policy accepts a review command and the selected event store substrate materializes the event.
_Avoid_: approved decision, canonical record, source note

**SourceEvidence**:
The excerpt, pointer, payload, or provenance record that supports a candidate, binding, dependency signal, or governance result.
_Avoid_: vague context, model memory

**BindingEvidence**:
Reviewable evidence that a decision relates to a code path, symbol, diff, dependency, workflow, or deploy surface.
_Avoid_: compliance verdict, signoff, status

**Decision Ledger**:
The canonical materialized decision record derived by replaying the selected event store substrate. Durable write authority remains the event store substrate.
_Avoid_: UI page, hosted cache, dashboard database

**Ledger View**:
The human-facing surface for inspecting Decision Ledger state and emitting review commands. It is not durable authority.
_Avoid_: Decision Ledger, source of truth

**Governance policy**:
Configurable rules that decide how candidates, review commands, and evidence route to review, advisory state, materialization, or enforcement according to workspace capability.
_Avoid_: connector logic, model prompt, fixed org-chart role

**GovernanceResult**:
A substrate-neutral outcome of governance or conflict analysis. It can express blocking, warning, or informational intent; each substrate maps it to honest enforcement channels.
_Avoid_: CI result only, dashboard warning only

**Signoff**:
The ownership lifecycle on a Decision. Approval is separate from candidate acceptance and separate from code compliance.
_Avoid_: status, compliance, drift, ratification

**Status / compliance state**:
The code-compliance state for a decision. It is computed or reviewed from grounding and drift evidence, not hand-authored as signoff.
_Avoid_: signoff, approval

**Read/write path**:
Review surfaces, MCP tools, integrations, and mods emit substrate-neutral commands/evidence. Governance policy and event store adapters decide materialization.
_Avoid_: UI writes YAML, connector writes canonical decisions directly

**MCP tool surface**:
The local agent-facing command surface that lets coding agents interact with Bicameral by emitting protocol-shaped evidence, queries, and review commands.
_Avoid_: integration adapter, canonical writer, hosted daemon

**CapabilityReport**:
Structured result from the daemon capability handshake. It reports daemon-advertised protocol version, supported commands, endpoint, and readiness/capability metadata so MCP can decide whether it can attempt a ToolRequest. It is a report, not a grant.
_Authority verbs_: return, inspect
_Avoid_: command grant, governance authority, fallback authority, local policy bypass
_Related_: #606

**RecallPacket**:
Daemon-authored lookup result that describes searched scope, unknown scope, matches, evidence references, freshness/readiness labels, and allowed next actions. MCP may render the packet but must not strengthen it into a completeness, safety, compliance, or no-conflict claim.
_Authority verbs_: return, render
_Avoid_: compliance result, global search result, no-conflict proof, merge-safety signal
_Related_: #638, #639

**ContextPacket**:
Daemon-authored relevance-time context artifact assembled after core-owned narrowing and ranking. It may contain trusted corpus entries, reviewed Decisions, constraints, candidate findings, evidence links, risk, confidence, freshness, rationale, and required actions while preserving authority labels. MCP may request and render it; MCP must not assemble, rank, enrich, or canonize it locally.
_Authority verbs_: return, render
_Avoid_: raw source dump, MCP-ranked context, local canonical truth, merge-safety signal
_Related_: #613

**CorrectionFinding**:
Daemon-authored finding that a changed code region may require a trusted-corpus, source-doc, Decision, or constraint correction. It is a review handoff artifact, not a canonical update. MCP may request and render findings and route users to approved review/correction tools; MCP must not decide drift, mutate source docs, or materialize corpus truth locally.
_Authority verbs_: return, render, route
_Avoid_: accepted correction, canonical update, drift verdict, source-doc write, merge-safety signal
_Related_: #618

**review handoff**:
Daemon-authored review surface for decision candidates, corpus-change proposals, and contradiction findings. MCP may list these items, preserve evidence/source/provenance/affected-surface/rationale fields, and submit authorized review or triage commands to the daemon. MCP must not infer approval, mutate trusted corpus, assign reviewer authority, or materialize canonical state locally.
_Authority verbs_: list, render, submit
_Avoid_: local approval, canonical mutation, reviewer assignment, trusted-corpus write, merge-safety signal
_Related_: #614

**MCP context capture**:
Local MCP session, tool, command-output, and code-hint context submitted to the daemon as `ingest.submit_local` input using bot vocabulary such as Source, SourceSnapshot, EvidenceReference, SourceKind, and source link. Code hints are advisory binding_hints only. MCP may package and submit the context; MCP must not claim graph verification, binding authority, compliance, signoff, or event-store authority.
_Authority verbs_: package, submit
_Avoid_: graph verified, accepted binding, compliance result, signoff, local SourceSnapshot file, canonical event
_Related_: #582

**correction_id**:
Daemon-assigned identifier for an accepted correction request outcome. It identifies the daemon-mediated request result; it is not local MCP approval, proof of ledger materialization, or evidence that a correction has become canonical.
_Authority verbs_: return, reference
_Avoid_: approval id, ledger event id, canonical decision id, local write token
_Related_: #639, #640
