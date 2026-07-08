"""MCP-facing tool schemas for canonical ToolRequest commands."""

from __future__ import annotations

from mcp.types import Tool


def _schema(properties: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "object",
        "properties": {
            **properties,
            "actor_id": {"type": "string", "description": "Optional actor override."},
            "session_id": {"type": "string", "description": "Optional MCP session id."},
            "workspace": {"type": "string", "description": "Workspace root for policy scope."},
            "policy_scope": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional policy scopes.",
            },
        },
        "required": required or [],
    }


def _review_schema() -> dict:
    return _schema(
        {
            "target_id": {"type": "string"},
            "reason": {"type": "string"},
        },
        ["target_id"],
    )


def _approval_proof_schema() -> dict:
    return {
        "type": "object",
        "description": "Daemon-validated approval proof for governed recall review actions.",
    }


SUPPORTED_TOOLS: tuple[Tool, ...] = (
    Tool(
        name="bicameral.ingest",
        description="Submit local source/session evidence or decision candidates to the bot daemon.",
        inputSchema=_schema(
            {
                "source_uri": {"type": "string"},
                "source_type": {"type": "string"},
                "label": {"type": "string"},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "level": {"type": "string"},
                "decision_level": {
                    "type": "string",
                    "enum": ["L1", "L2", "L3"],
                    "description": (
                        "Explicit decision-level classification. "
                        "L1 = behavioral commitment (evidence-evaluated, not code-bound), "
                        "L2 = code-grounded identity (enters codegenome graph), "
                        "L3 = lightweight/tolerant (never tracked in identity graph). "
                        "When omitted the daemon receives a pending_classification "
                        "signal and applies heuristic or marks the decision pending."
                    ),
                },
                "snapshot_content": {"type": "string"},
                "evidence": {"type": "array", "items": {"type": "object"}},
                "candidate_drafts": {"type": "array", "items": {"type": "object"}},
                "binding_hints": {"type": "array", "items": {"type": "object"}},
                "rationale": {"type": "string"},
                "metadata": {"type": "object"},
            },
            ["source_uri", "source_type", "title", "description"],
        ),
    ),
    Tool(
        name="bicameral.capture_context",
        description=(
            "Submit MCP session, tool, command-output, and code-hint context as "
            "bot-owned Source/SourceSnapshot/EvidenceReference-compatible local "
            "ingest input. Code hints remain advisory binding_hints; MCP does not "
            "claim graph verification, compliance, signoff, or event-store authority."
        ),
        inputSchema=_schema(
            {
                "source_uri": {
                    "type": "string",
                    "description": "Optional source link for this capture; defaults to mcp://session/...",
                },
                "source_type": {
                    "type": "string",
                    "description": "Bot SourceKind-compatible source type.",
                },
                "source_kind": {
                    "type": "string",
                    "description": "Optional SourceKind vocabulary mirror for metadata.",
                },
                "source_link": {
                    "type": "string",
                    "description": "Optional human-facing source link for metadata.",
                },
                "title": {"type": "string"},
                "description": {"type": "string"},
                "label": {"type": "string"},
                "snapshot_content": {
                    "type": "string",
                    "description": "Optional pre-rendered SourceSnapshot content.",
                },
                "session_turns": {"type": "array", "items": {"type": "object"}},
                "tool_calls": {"type": "array", "items": {"type": "object"}},
                "tool_outputs": {"type": "array", "items": {"type": "object"}},
                "command_outputs": {"type": "array", "items": {"type": "object"}},
                "code_hints": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Advisory file/range/symbol/diff hints forwarded as binding_hints.",
                },
                "code_region_hints": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Optional LLM-produced code-region hints, metadata only.",
                },
                "evidence": {"type": "array", "items": {"type": "object"}},
                "evidence_references": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Optional caller-known EvidenceReference-compatible refs.",
                },
                "correlation_id": {"type": "string"},
                "rationale": {"type": "string"},
                "metadata": {"type": "object"},
            }
        ),
    ),
    Tool(
        name="bicameral.preflight",
        description=(
            "Run daemon-owned constraint lookup/readiness context before or during "
            "implementation. This is not an MCP compliance decision, signoff, or "
            "governed work gate."
        ),
        inputSchema=_schema(
            {
                "files": {"type": "array", "items": {"type": "string"}},
                "symbols": {"type": "array", "items": {"type": "string"}},
                "diff_context": {"type": "string"},
                "branch": {"type": "string"},
                "checkpoint_hint": {
                    "type": "string",
                    "description": (
                        "Optional session checkpoint hint forwarded to the daemon. "
                        "Values such as pre_work, mid_session, or pre_write are "
                        "metadata only and do not change MCP authority or behavior."
                    ),
                },
            }
        ),
    ),
    Tool(
        name="bicameral.lookup",
        description="Query relevant decisions and constraints from the daemon before implementation. Returns daemon-authored RecallPacket with searched sources, matches, and allowed next actions.",
        inputSchema=_schema(
            {
                "files": {"type": "array", "items": {"type": "string"}},
                "symbols": {"type": "array", "items": {"type": "string"}},
                "scope": {
                    "type": "string",
                    "enum": ["pre_work", "mid_session", "pre_write"],
                    "description": "Lookup checkpoint scope.",
                },
                "include_context": {
                    "type": "boolean",
                    "description": "Include extended context in recall results.",
                },
            }
        ),
    ),
    Tool(
        name="bicameral.context",
        description=(
            "Request a compact relevance-time context packet from the daemon for an "
            "agent or developer workflow. Core owns narrowing, ranking, authority, "
            "freshness, and canonical truth; MCP only forwards request hints and renders "
            "the daemon-authored packet."
        ),
        inputSchema=_schema(
            {
                "query": {
                    "type": "string",
                    "description": "Optional natural-language task or lookup query.",
                },
                "ticket": {
                    "type": "string",
                    "description": "Ticket, issue, or work item identifier or URL.",
                },
                "branch": {"type": "string"},
                "pr": {
                    "type": "string",
                    "description": "Pull request identifier or URL.",
                },
                "repo": {"type": "string"},
                "files": {"type": "array", "items": {"type": "string"}},
                "symbols": {"type": "array", "items": {"type": "string"}},
                "code_region": {
                    "type": "object",
                    "description": "Optional daemon-interpreted code region hint.",
                },
                "feature_area": {"type": "string"},
                "agent_session_context": {
                    "type": "object",
                    "description": "Bounded agent-session hints; not a raw transcript dump.",
                },
                "planned_action": {"type": "string"},
                "checkpoint_hint": {
                    "type": "string",
                    "enum": ["pre_work", "mid_session", "pre_write", "manual_lookup"],
                    "description": (
                        "Optional inert checkpoint metadata. It does not grant capture, "
                        "blocking, enforcement, ranking, or persistence authority to MCP."
                    ),
                },
                "scope": {
                    "type": "string",
                    "description": "Optional daemon-defined corpus or lookup scope hint.",
                },
                "include_context": {
                    "type": "boolean",
                    "description": "Ask daemon for compact context fields when supported.",
                },
            }
        ),
    ),
    Tool(
        name="bicameral.correction_findings",
        description=(
            "Request daemon-authored correction-capture findings for PR, ticket, "
            "branch, repo, file, code-region, feature-area, or agent-session context. "
            "MCP renders findings and review handoff actions only; the daemon owns "
            "correction detection, authority, and canonical state."
        ),
        inputSchema=_schema(
            {
                "query": {
                    "type": "string",
                    "description": "Optional natural-language task or lookup query.",
                },
                "ticket": {
                    "type": "string",
                    "description": "Ticket, issue, or work item identifier or URL.",
                },
                "branch": {"type": "string"},
                "pr": {
                    "type": "string",
                    "description": "Pull request identifier or URL.",
                },
                "repo": {"type": "string"},
                "files": {"type": "array", "items": {"type": "string"}},
                "symbols": {"type": "array", "items": {"type": "string"}},
                "code_region": {
                    "type": "object",
                    "description": "Optional daemon-interpreted code region hint.",
                },
                "feature_area": {"type": "string"},
                "agent_session_context": {
                    "type": "object",
                    "description": "Bounded agent-session hints; not a raw transcript dump.",
                },
                "planned_action": {"type": "string"},
                "checkpoint_hint": {
                    "type": "string",
                    "enum": ["pre_work", "mid_session", "pre_write", "manual_lookup"],
                    "description": "Optional inert checkpoint metadata.",
                },
                "scope": {
                    "type": "string",
                    "description": "Optional daemon-defined corpus or lookup scope hint.",
                },
                "finding_status": {
                    "type": "string",
                    "description": "Optional daemon-defined correction finding status filter.",
                },
                "severity": {
                    "type": "string",
                    "description": "Optional daemon-defined finding severity filter.",
                },
                "include_correction_findings": {
                    "type": "boolean",
                    "description": "Ask daemon for correction-capture finding fields when supported.",
                },
                "include_context": {
                    "type": "boolean",
                    "description": "Ask daemon for compact context fields when supported.",
                },
            }
        ),
    ),
    Tool(
        name="bicameral.bind",
        description="Propose binding evidence for a decision or candidate.",
        inputSchema=_schema(
            {
                "decision_or_candidate_id": {"type": "string"},
                "bindings": {"type": "array", "items": {"type": "object"}},
                "commit_sha": {"type": "string"},
                "ref_name": {"type": "string"},
            },
            ["decision_or_candidate_id", "bindings"],
        ),
    ),
    Tool(
        name="bicameral.workspace.bind",
        description=(
            "Propose binding the current local folder to an already-registered "
            "project for local code grounding. MCP never binds silently: without "
            "confirmed=true it returns a confirmation prompt; on confirmed=true it "
            "dispatches the daemon-owned workspace.bind request. The current folder "
            "is candidate path evidence only, never project identity, and the daemon "
            "owns validation and materialization."
        ),
        inputSchema=_schema(
            {
                "project_id": {
                    "type": "string",
                    "description": (
                        "Opaque id of the already-registered project to bind. "
                        "Never a filesystem path."
                    ),
                },
                "confirmed": {
                    "type": "boolean",
                    "description": (
                        "Explicit operator confirmation. When absent/false, MCP "
                        "returns a confirmation prompt and does not bind."
                    ),
                },
                "display_name": {
                    "type": "string",
                    "description": "Human-readable project name for the prompt.",
                },
                "project_slug": {
                    "type": "string",
                    "description": "Optional short project slug/label.",
                },
                "candidate_path": {
                    "type": "string",
                    "description": (
                        "Optional explicit candidate folder. Defaults to the "
                        "detected workspace/repo root."
                    ),
                },
                "candidate_label": {
                    "type": "string",
                    "description": "Optional non-path folder label (basename).",
                },
                "reason": {
                    "type": "string",
                    "description": "Why this folder is proposed as the candidate.",
                },
                "confidence": {
                    "type": "number",
                    "description": "Surface confidence in [0.0, 1.0].",
                },
                "required_daemon_capability": {
                    "type": "integer",
                    "description": (
                        "Minimum daemon workspace-binding capability version required; "
                        "an older daemon fails closed with a capability mismatch."
                    ),
                },
                "expected_current_state": {
                    "type": "string",
                    "enum": [
                        "local_workspace_unbound",
                        "local_workspace_bound",
                        "local_workspace_repair_required",
                    ],
                    "description": "Optional advisory optimistic-concurrency hint.",
                },
            },
            ["project_id"],
        ),
    ),
    Tool(
        name="bicameral.binding.inspect",
        description="Inspect existing binding evidence through the bot daemon.",
        inputSchema=_schema(
            {
                "decision_or_candidate_id": {"type": "string"},
                "commit_sha": {"type": "string"},
            },
            ["decision_or_candidate_id"],
        ),
    ),
    Tool(
        name="bicameral.evidence.refresh",
        description="Request daemon-owned evidence currentness refresh for a tracked Decision.",
        inputSchema=_schema(
            {
                "decision_id": {
                    "type": "string",
                    "description": "Decision id whose evidence currentness should be refreshed.",
                },
            },
            ["decision_id"],
        ),
    ),
    Tool(
        name="bicameral.review.candidates",
        description=(
            "List daemon-owned decision candidates for review. MCP forwards a "
            "search.query request scoped to candidates and renders evidence, source, "
            "provenance, affected-surface, rationale, and allowed review actions "
            "when returned by the daemon."
        ),
        inputSchema=_schema(
            {
                "query": {
                    "type": "string",
                    "description": "Optional candidate search query; defaults to empty list query.",
                },
                "filters": {"type": "object"},
                "limit": {"type": "integer"},
            }
        ),
    ),
    Tool(
        name="bicameral.review.corpus_proposals",
        description=(
            "List daemon-authored corpus-change proposals/correction findings for "
            "review without mutating trusted corpus state. MCP requests lookup.query "
            "with correction-finding fields and renders review handoff payloads."
        ),
        inputSchema=_schema(
            {
                "query": {"type": "string"},
                "ticket": {"type": "string"},
                "branch": {"type": "string"},
                "pr": {"type": "string"},
                "repo": {"type": "string"},
                "files": {"type": "array", "items": {"type": "string"}},
                "symbols": {"type": "array", "items": {"type": "string"}},
                "code_region": {"type": "object"},
                "feature_area": {"type": "string"},
                "agent_session_context": {"type": "object"},
                "planned_action": {"type": "string"},
                "checkpoint_hint": {
                    "type": "string",
                    "enum": ["pre_work", "mid_session", "pre_write", "manual_lookup"],
                },
                "scope": {"type": "string"},
                "finding_status": {"type": "string"},
                "severity": {"type": "string"},
                "include_context": {"type": "boolean"},
            }
        ),
    ),
    Tool(
        name="bicameral.review.accept_candidate",
        description="Accept a decision candidate through bot governance.",
        inputSchema=_review_schema(),
    ),
    Tool(
        name="bicameral.review.reject_candidate",
        description="Reject a decision candidate through bot governance.",
        inputSchema=_review_schema(),
    ),
    Tool(
        name="bicameral.review.promote_candidate",
        description=(
            "Request daemon-governed promotion, supersession, or routing of a "
            "DecisionCandidate from a RecallPacket. The daemon validates candidate "
            "identity, approval proof, authority, and canonical transitions."
        ),
        inputSchema=_schema(
            {
                "packet_id": {"type": "string"},
                "candidate_id": {"type": "string"},
                "promotion_outcome": {
                    "type": "string",
                    "description": "Daemon-defined promotion outcome, e.g. new_constraint.",
                },
                "supersedes_decision_id": {"type": "string"},
                "scoping_relationship": {"type": "string"},
                "approval_proof": _approval_proof_schema(),
            },
            ["packet_id", "candidate_id", "promotion_outcome", "approval_proof"],
        ),
    ),
    Tool(
        name="bicameral.review.request_corpus_change",
        description=(
            "Request daemon-governed correction/corpus-change review from a "
            "RecallPacket selection. This is a review handoff only; the daemon "
            "owns authorization and any later canonical state transition."
        ),
        inputSchema=_schema(
            {
                "packet_id": {"type": "string"},
                "selected_item_ids": {"type": "array", "items": {"type": "string"}},
                "correction_kind": {
                    "type": "string",
                    "description": "Daemon-defined correction kind, e.g. source_contradiction.",
                },
                "rationale": {"type": "string"},
                "approval_proof": _approval_proof_schema(),
            },
            ["packet_id", "selected_item_ids", "correction_kind", "rationale", "approval_proof"],
        ),
    ),
    Tool(
        name="bicameral.review.approve_signoff",
        description="Approve signoff on a promoted decision through bot governance.",
        inputSchema=_review_schema(),
    ),
    Tool(
        name="bicameral.review.reject_signoff",
        description="Reject signoff on a promoted decision through bot governance.",
        inputSchema=_review_schema(),
    ),
    Tool(
        name="bicameral.review.resolve_compliance",
        description=(
            "Deferred in alpha. Resolve compliance state for a decision through "
            "bot governance when the daemon advertises this command as supported. "
            "Hidden from the tool list when deferred; returns a typed daemon "
            "capability error if called directly."
        ),
        inputSchema=_schema(
            {
                "target_id": {"type": "string"},
                "compliance_verdict": {"type": "string"},
                "reason": {"type": "string"},
            },
            ["target_id", "compliance_verdict"],
        ),
    ),
    Tool(
        name="bicameral.brief",
        description=(
            "Render a chronological narrative brief for a feature area or "
            "cross-cutting query from the decision ledger. Returns a "
            "daemon-authored Markdown summary with timeline, decision graph, "
            "and open items suitable for onboarding and decision explanation."
        ),
        inputSchema=_schema(
            {
                "topic": {
                    "type": "string",
                    "description": "Feature area or cross-cutting query to brief.",
                },
                "decision_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional explicit decision IDs to include.",
                },
                "since": {
                    "type": "string",
                    "description": "ISO-8601 date to limit timeline start.",
                },
                "include_graph": {
                    "type": "boolean",
                    "description": "Include decision graph edges in the brief.",
                },
            },
            ["topic"],
        ),
    ),
    Tool(
        name="bicameral.history",
        description="Read replayed/materialized decision state from the bot daemon.",
        inputSchema=_schema(
            {
                "decision_id": {"type": "string"},
                "include_events": {"type": "boolean"},
                "include_bindings": {"type": "boolean"},
                "since": {"type": "string"},
            }
        ),
    ),
    Tool(
        name="bicameral.search",
        description="Search daemon-owned decision, candidate, and binding state.",
        inputSchema=_schema(
            {
                "query": {"type": "string"},
                "scope": {
                    "type": "string",
                    "enum": ["decisions", "candidates", "bindings", "all"],
                },
                "filters": {"type": "object"},
                "limit": {"type": "integer"},
            },
            ["query"],
        ),
    ),
    Tool(
        name="bicameral.recall.inspect_evidence",
        description=(
            "Inspect cited evidence from a daemon-authored RecallPacket. Returns "
            "daemon-owned evidence detail for the specified match within the "
            "packet's searched scope. Unknown scope is surfaced explicitly; "
            "absence of evidence in the searched scope does not imply absence "
            "in unknown or un-searched sources. This is a read-only lookup; "
            "it does not create, mutate, or verify Decisions, candidates, "
            "BindingEvidence, signoff, compliance, or trusted-corpus state."
        ),
        inputSchema=_schema(
            {
                "packet_id": {
                    "type": "string",
                    "description": "RecallPacket ID returned by a prior lookup or preflight.",
                },
                "match_id": {
                    "type": "string",
                    "description": "Match ID within the RecallPacket to inspect.",
                },
                "evidence_id": {
                    "type": "string",
                    "description": "Optional specific evidence reference ID to inspect.",
                },
            },
            ["packet_id", "match_id"],
        ),
    ),
    Tool(
        name="bicameral.recall.expand_scope",
        description=(
            "Request the daemon to expand the search scope of a prior "
            "RecallPacket lookup. The daemon decides which additional sources "
            "to search and returns an updated RecallPacket with expanded "
            "searched-scope and any newly discovered matches. Unknown scope "
            "after expansion is surfaced explicitly. This is a read-only "
            "scope-widening request; it does not create, mutate, or verify "
            "Decisions, candidates, BindingEvidence, signoff, compliance, "
            "or trusted-corpus state."
        ),
        inputSchema=_schema(
            {
                "packet_id": {
                    "type": "string",
                    "description": "RecallPacket ID whose scope should be expanded.",
                },
                "expand_to": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional list of additional source identifiers to include "
                        "in the expanded search. When omitted the daemon uses its "
                        "default expansion strategy."
                    ),
                },
                "reason": {
                    "type": "string",
                    "description": "Optional reason for requesting scope expansion.",
                },
            },
            ["packet_id"],
        ),
    ),
    Tool(
        name="bicameral.review.contradictions",
        description=(
            "List active contradiction findings for review. Alias for the daemon "
            "governance inbox, preserving evidence, source distinction, provenance, "
            "affected surface, rationale, and allowed triage actions."
        ),
        inputSchema=_schema(
            {
                "status_filter": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["open", "acknowledged"]},
                },
                "limit": {"type": "integer"},
            }
        ),
    ),
    Tool(
        name="bicameral.review.triage_contradiction",
        description=(
            "Submit a contradiction triage state update through daemon governance. "
            "MCP forwards the request; the daemon enforces authority and canonical "
            "state transitions."
        ),
        inputSchema=_schema(
            {
                "report_id": {"type": "string"},
                "action": {
                    "type": "string",
                    "enum": ["resolve", "acknowledge", "dismiss", "route"],
                },
                "reason": {"type": "string"},
                "route_to": {"type": "string"},
            },
            ["report_id", "action"],
        ),
    ),
    Tool(
        name="bicameral.governance.inbox",
        description=(
            "List active governance inbox items (contradiction findings) for the "
            "current actor. Returns open and acknowledged findings deterministically "
            "without duplicating the same ContradictionReport."
        ),
        inputSchema=_schema(
            {
                "status_filter": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["open", "acknowledged"],
                    },
                    "description": (
                        "Filter by finding status. Defaults to active findings "
                        "(open + acknowledged)."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of inbox items to return.",
                },
            }
        ),
    ),
    Tool(
        name="bicameral.governance.inspect",
        description="Inspect a specific governance finding by its contradiction report ID.",
        inputSchema=_schema(
            {
                "report_id": {
                    "type": "string",
                    "description": "ContradictionReport ID to inspect.",
                },
            },
            ["report_id"],
        ),
    ),
    Tool(
        name="bicameral.governance.resolve",
        description=(
            "Resolve an active contradiction finding through the bot governance "
            "protocol. Authorized Product Owner or delegate can resolve, acknowledge, "
            "dismiss, or route the finding. Unauthorized actors receive an explicit "
            "unauthorized result and no canonical event is submitted."
        ),
        inputSchema=_schema(
            {
                "report_id": {
                    "type": "string",
                    "description": "ContradictionReport ID to resolve.",
                },
                "action": {
                    "type": "string",
                    "enum": ["resolve", "acknowledge", "dismiss", "route"],
                    "description": "Resolution action to take on the finding.",
                },
                "reason": {
                    "type": "string",
                    "description": "Reason for the resolution action.",
                },
                "route_to": {
                    "type": "string",
                    "description": "Target actor or team when action is 'route'.",
                },
            },
            ["report_id", "action"],
        ),
    ),
    Tool(
        name="bicameral.privacy.erase_subject.approve",
        description=(
            "Grant single-use approval for a scoped PII erasure request. "
            "The user must confirm the subject identifier and optional "
            "predicate before erasure is allowed. Approval is consumed "
            "on the next successful erase_subject call. Required by "
            "GDPR Art.17 right-to-erasure (fail-closed approval gate)."
        ),
        inputSchema=_schema(
            {
                "subject_id": {
                    "type": "string",
                    "description": "Identifier of the data subject to erase.",
                },
                "predicate": {
                    "type": "string",
                    "description": "Optional filter predicate for selective erasure.",
                },
                "reason": {
                    "type": "string",
                    "description": "Reason for the erasure request (audit trail).",
                },
            },
            ["subject_id"],
        ),
    ),
    Tool(
        name="bicameral.privacy.erase_subject",
        description=(
            "Erase PII for a data subject from the daemon-owned PII archive. "
            "Requires prior single-use approval via "
            "bicameral.privacy.erase_subject.approve scoped to the same "
            "subject_id. Routes to the daemon's privacy.erase_subject "
            "command. Fail-closed: erasure is rejected locally without "
            "approval, and archive failures do not fall back to inline "
            "storage. Implements GDPR Art.17 right-to-erasure."
        ),
        inputSchema=_schema(
            {
                "subject_id": {
                    "type": "string",
                    "description": "Identifier of the data subject to erase.",
                },
                "predicate": {
                    "type": "string",
                    "description": "Optional filter predicate for selective erasure.",
                },
                "reason": {
                    "type": "string",
                    "description": "Reason for the erasure request (audit trail).",
                },
            },
            ["subject_id"],
        ),
    ),
    Tool(
        name="bicameral.request_correction.approve",
        description=(
            "Grant single-use approval for a scoped correction request. "
            "The user must confirm the specific packet item, excerpt, diff, "
            "or correction request text before submission is allowed. "
            "Approval is consumed on the next successful request_correction call."
        ),
        inputSchema=_schema(
            {
                "packet_id": {
                    "type": "string",
                    "description": "Packet item ID the correction targets.",
                },
                "excerpt": {
                    "type": "string",
                    "description": "Excerpt text the correction targets.",
                },
                "diff": {
                    "type": "string",
                    "description": "Diff content the correction targets.",
                },
                "correction_request": {
                    "type": "string",
                    "description": "The correction request text to approve.",
                },
            },
        ),
    ),
    Tool(
        name="bicameral.request_correction",
        description=(
            "Submit a correction request to the bot daemon. Requires prior "
            "single-use approval via bicameral.request_correction.approve "
            "scoped to the same packet item, excerpt, diff, or correction "
            "request. Submission is rejected locally without approval."
        ),
        inputSchema=_schema(
            {
                "packet_id": {
                    "type": "string",
                    "description": "Packet item ID the correction targets.",
                },
                "excerpt": {
                    "type": "string",
                    "description": "Excerpt text the correction targets.",
                },
                "diff": {
                    "type": "string",
                    "description": "Diff content the correction targets.",
                },
                "correction_request": {
                    "type": "string",
                    "description": "The correction request text to submit.",
                },
                "reason": {
                    "type": "string",
                    "description": "Reason or context for the correction.",
                },
            },
        ),
    ),
)


ERASURE_TOOLS: frozenset[str] = frozenset(
    {
        "bicameral.privacy.erase_subject",
        "bicameral.privacy.erase_subject.approve",
    }
)


def tool_for_name(name: str) -> Tool | None:
    return next((tool for tool in SUPPORTED_TOOLS if tool.name == name), None)
