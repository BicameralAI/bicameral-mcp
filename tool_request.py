"""ToolRequest construction helpers."""

from __future__ import annotations

import logging
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

log = logging.getLogger(__name__)

MCP_TOOL_COMMANDS: dict[str, str] = {
    "bicameral.ingest": "ingest.submit_local",
    "bicameral.capture_context": "ingest.submit_local",
    "bicameral.preflight": "preflight.run",
    "bicameral.context": "lookup.query",
    "bicameral.correction_findings": "lookup.query",
    "bicameral.lookup": "lookup.query",
    "bicameral.request_correction": "correction.request",
    "bicameral.bind": "binding.create",
    "bicameral.workspace.bind": "workspace.bind",
    "bicameral.binding.inspect": "binding.inspect",
    "bicameral.evidence.refresh": "evidence.refresh",
    "bicameral.review.candidates": "search.query",
    "bicameral.review.corpus_proposals": "lookup.query",
    "bicameral.review.accept_candidate": "review.accept_candidate",
    "bicameral.review.reject_candidate": "review.reject_candidate",
    "bicameral.review.promote_candidate": "recall.promote_decision_candidate",
    "bicameral.review.request_corpus_change": "recall.request_correction",
    "bicameral.review.approve_signoff": "review.approve_signoff",
    "bicameral.review.reject_signoff": "review.reject_signoff",
    "bicameral.review.resolve_compliance": "review.resolve_compliance",
    "bicameral.recall.inspect_evidence": "recall.inspect_evidence",
    "bicameral.recall.expand_scope": "recall.expand_scope",
    "bicameral.brief": "brief.render",
    "bicameral.history": "history.list",
    "bicameral.search": "search.query",
    "bicameral.privacy.erase_subject": "privacy.erase_subject",
    "bicameral.review.contradictions": "governance.inbox.list",
    "bicameral.review.triage_contradiction": "governance.resolve_contradiction",
    "bicameral.governance.inbox": "governance.inbox.list",
    "bicameral.governance.inspect": "governance.inspect",
    "bicameral.governance.resolve": "governance.resolve_contradiction",
}

# Tools that are locally gated and never dispatched to the daemon.
LOCAL_ONLY_TOOLS: frozenset[str] = frozenset(
    {
        "bicameral.request_correction.approve",
        "bicameral.privacy.erase_subject.approve",
    }
)

# Canonical daemon command for MCP-assisted local workspace binding
# (bicameral-bot#732/#747). The daemon remains the sole binding authority
# (ADR-0005): MCP proposes, the daemon validates and materializes.
WORKSPACE_BIND_COMMAND = "workspace.bind"

# Durable workspace-binding lifecycle states the daemon owns (never authored by
# MCP). Used only to validate the optional optimistic-concurrency hint.
WORKSPACE_BINDING_STATES: frozenset[str] = frozenset(
    {
        "local_workspace_unbound",
        "local_workspace_bound",
        "local_workspace_repair_required",
    }
)

SUPPORTED_COMMANDS = tuple(MCP_TOOL_COMMANDS.values())


def build_tool_request(
    *,
    command_name: str,
    params: dict[str, Any],
    authority: dict[str, Any],
) -> dict[str, Any]:
    return {
        "request_id": str(uuid4()),
        "command": {"name": command_name, "params": _command_params(command_name, params)},
        "authority": authority,
        "issued_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }


def _command_params(command_name: str, params: dict[str, Any]) -> dict[str, Any]:
    control_keys = {
        "actor_id",
        "session_id",
        "workspace",
        "policy_scope",
    }
    cleaned = {key: value for key, value in params.items() if key not in control_keys}

    if command_name == "ingest.submit_local":
        return _ingest_params(cleaned)
    if command_name == "preflight.run":
        return _only(cleaned, "files", "symbols", "diff_context", "branch", "checkpoint_hint")
    if command_name == "binding.create":
        workspace = (
            params.get("workspace")
            or os.environ.get("BICAMERAL_WORKSPACE")
            or os.environ.get("REPO_PATH")
            or os.getcwd()
        )
        head_sha, branch = _resolve_workspace_ref(workspace)
        if head_sha:
            cleaned.setdefault("commit_sha", head_sha)
        if branch:
            cleaned.setdefault("ref_name", branch)
        return _only(cleaned, "decision_or_candidate_id", "bindings", "commit_sha", "ref_name")
    if command_name == WORKSPACE_BIND_COMMAND:
        return _workspace_bind_params(cleaned)
    if command_name == "binding.inspect":
        return _only(cleaned, "decision_or_candidate_id", "commit_sha")
    if command_name == "evidence.refresh":
        return _only(cleaned, "decision_id")
    if command_name in {
        "review.accept_candidate",
        "review.reject_candidate",
        "review.approve_signoff",
        "review.reject_signoff",
    }:
        return _only(cleaned, "target_id", "reason")
    if command_name == "review.resolve_compliance":
        return _only(cleaned, "target_id", "compliance_verdict", "reason")
    if command_name == "brief.render":
        return _only(cleaned, "topic", "decision_ids", "since", "include_graph")
    if command_name == "history.list":
        return _only(cleaned, "decision_id", "include_events", "include_bindings", "since")
    if command_name == "search.query":
        return _only(cleaned, "query", "scope", "filters", "limit")
    if command_name == "lookup.query":
        return _only(
            cleaned,
            "query",
            "ticket",
            "branch",
            "pr",
            "repo",
            "files",
            "symbols",
            "code_region",
            "feature_area",
            "agent_session_context",
            "planned_action",
            "checkpoint_hint",
            "scope",
            "finding_status",
            "severity",
            "include_correction_findings",
            "include_context",
        )
    if command_name == "recall.promote_decision_candidate":
        return _only(
            cleaned,
            "packet_id",
            "candidate_id",
            "promotion_outcome",
            "supersedes_decision_id",
            "scoping_relationship",
            "approval_proof",
        )
    if command_name == "recall.inspect_evidence":
        return _only(cleaned, "packet_id", "match_id", "evidence_id")
    if command_name == "recall.expand_scope":
        return _only(cleaned, "packet_id", "expand_to", "reason")
    if command_name == "recall.request_correction":
        return _only(
            cleaned,
            "packet_id",
            "selected_item_ids",
            "correction_kind",
            "rationale",
            "approval_proof",
        )
    if command_name == "correction.request":
        return _only(cleaned, "packet_id", "excerpt", "diff", "correction_request", "reason")
    if command_name == "privacy.erase_subject":
        return _only(cleaned, "subject_id", "predicate", "reason")
    if command_name == "governance.inbox.list":
        return _only(cleaned, "status_filter", "limit")
    if command_name == "governance.inspect":
        return _only(cleaned, "report_id")
    if command_name == "governance.resolve_contradiction":
        return _only(cleaned, "report_id", "action", "reason", "route_to")
    return cleaned


VALID_DECISION_LEVELS: frozenset[str] = frozenset({"L1", "L2", "L3"})


def _ingest_params(cleaned: dict[str, Any]) -> dict[str, Any]:
    """Shape ingest.submit_local params with decision_level classification signal.

    When the caller provides ``decision_level``, it is forwarded as-is.
    When omitted, ``pending_classification`` is injected so the daemon
    knows to apply heuristic classification rather than silently storing
    the decision as unclassified (which codegenome/bind treats as
    tolerant L3).
    """
    result = _only(
        cleaned,
        "source_uri",
        "source_type",
        "label",
        "title",
        "description",
        "level",
        "suggested_level",
        "decision_level",
        "snapshot_content",
        "evidence",
        "candidate_drafts",
        "binding_hints",
        "rationale",
        "metadata",
    )
    if "decision_level" not in result:
        result["pending_classification"] = True
    return result


def _workspace_bind_params(cleaned: dict[str, Any]) -> dict[str, Any]:
    """Shape flat MCP inputs into a daemon `workspace.bind` WorkspaceBindRequest.

    MCP only *proposes*: it assembles the transient ``WorkspaceBindingProposal``
    plus explicit operator confirmation and dispatches it to the local daemon,
    which is the sole binding authority (ADR-0005, bicameral-bot#732).

    The ``candidate_path`` is LOCAL-ONLY: it is placed inside the local
    ``ToolRequest`` proposal for the daemon and is never sent to Cloud or echoed
    into any hosted-safe projection. The safe ``display`` block (display name,
    slug, non-path folder label) is the only project metadata that may cross the
    hosted bridge, so ``candidate_label`` is never an absolute path.
    """
    project_id = cleaned.get("project_id")
    candidate_path = cleaned.get("candidate_path")

    display: dict[str, Any] = {"display_name": cleaned.get("display_name") or project_id or ""}
    if cleaned.get("project_slug"):
        display["project_slug"] = cleaned["project_slug"]
    candidate_label = cleaned.get("candidate_label")
    if not candidate_label and candidate_path:
        candidate_label = os.path.basename(str(candidate_path).rstrip("/\\")) or None
    if candidate_label:
        display["candidate_label"] = candidate_label

    # Git-remote evidence (mcp#702 owner decision): the candidate folder's git
    # remote is *evidence only* and never project identity. It may raise or lower
    # the proposal confidence and reason, but ``project_id`` remains the authority
    # key and the daemon still owns validation and materialization.
    evidence = evaluate_remote_evidence(
        candidate_path=candidate_path,
        project_source_refs=_coerce_source_refs(cleaned),
    )

    caller_confidence = cleaned.get("confidence")
    confidence = (
        _coerce_confidence(caller_confidence)
        if caller_confidence is not None
        else evidence.confidence
    )
    reason = cleaned.get("reason") or evidence.reason

    proposal: dict[str, Any] = {
        "project_id": project_id,
        "display": display,
        "candidate_path": candidate_path,
        "source_surface": "mcp",
        "reason": reason,
        "confidence": confidence,
    }

    params: dict[str, Any] = {
        "proposal": proposal,
        "confirmed": bool(cleaned.get("confirmed", False)),
    }
    required = cleaned.get("required_daemon_capability")
    if isinstance(required, int) and not isinstance(required, bool):
        params["required_daemon_capability"] = required
    expected = cleaned.get("expected_current_state")
    if expected in WORKSPACE_BINDING_STATES:
        params["expected_current_state"] = expected
    return params


def _coerce_confidence(value: Any) -> float:
    """Clamp a caller-supplied confidence into ``[0.0, 1.0]``.

    Defaults to full confidence: an explicit operator-driven bind proposal is a
    deliberate selection, not a heuristic guess.
    """
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 1.0
    return max(0.0, min(1.0, confidence))


# ── Git-remote evidence (mcp#702) ──────────────────────────────────────────
#
# Owner decision: the candidate local workspace's git remote may be used as
# *evidence* for a workspace-bind proposal — never as project identity.
# ``project_id`` remains the authority key; the daemon still owns validation
# and materialization. MCP only shapes proposal confidence/reason and fails
# closed (no dispatch) when the remote clearly contradicts the selected project.

#: Confidence when the candidate remote matches a registered project source ref.
REMOTE_MATCH_CONFIDENCE = 0.95
#: Confidence when a remote is detected but no source ref is available to verify.
REMOTE_UNVERIFIED_CONFIDENCE = 0.6
#: Confidence when no unambiguous remote could be detected.
REMOTE_AMBIGUOUS_CONFIDENCE = 0.4


@dataclass(frozen=True)
class RemoteEvidence:
    """Outcome of comparing the candidate git remote to the selected project.

    ``verdict`` is one of ``match``, ``contradiction``, ``unverified``, or
    ``ambiguous``. ``candidate_repo_ref`` is the normalized ``org/repo`` ref
    (or ``None`` when no unambiguous remote was found). It is evidence only and
    is never used as project identity.
    """

    verdict: str
    confidence: float
    reason: str
    candidate_repo_ref: str | None = None
    project_source_refs: tuple[str, ...] = ()


def normalize_repo_ref(raw: Any) -> str | None:
    """Normalize a git remote URL (or repo ref) to a stable ``org/repo`` ref.

    Handles SSH (``git@github.com:Org/Repo.git``), scp-like, and HTTPS
    (``https://github.com/Org/Repo(.git)``) forms, stripping scheme, userinfo,
    host, a trailing ``.git`` and trailing slashes. Returns ``None`` when the
    input does not resolve to at least ``org/repo``.
    """
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None
    # Strip URL scheme (https://, ssh://, git://, git+ssh://, ...).
    s = re.sub(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", "", s)
    # Strip leading userinfo (user@ or user:pass@) up to the first '/'.
    s = re.sub(r"^[^/@]+@", "", s)
    # scp-like "host:org/repo" -> "host/org/repo" (only when the colon precedes
    # a path and the segment before it is a bare host, not already a path).
    if ":" in s:
        before, _, after = s.partition(":")
        if "/" not in before:
            s = f"{before}/{after}"
    s = s.rstrip("/")
    if s.endswith(".git"):
        s = s[:-4]
    parts = [p for p in s.split("/") if p]
    if len(parts) < 2:
        return None
    return f"{parts[-2]}/{parts[-1]}"


def _coerce_source_refs(cleaned: dict[str, Any]) -> list[str]:
    """Collect caller-supplied registered-project source refs (list or scalar)."""
    refs: list[str] = []
    raw_list = cleaned.get("project_source_refs")
    if isinstance(raw_list, str):
        refs.append(raw_list)
    elif isinstance(raw_list, (list, tuple)):
        refs.extend(str(item) for item in raw_list if isinstance(item, str))
    single = cleaned.get("project_source_ref")
    if isinstance(single, str):
        refs.append(single)
    return refs


def detect_candidate_repo_ref(candidate_path: Any) -> str | None:
    """Return the normalized ``origin`` repo ref for *candidate_path*, or ``None``.

    Uses ``remote.origin.url`` as the unambiguous signal; a folder with no
    ``origin`` remote (or no git repo) yields ``None`` (treated as ambiguous).
    """
    workspace = (
        str(candidate_path)
        if candidate_path
        else (os.environ.get("BICAMERAL_WORKSPACE") or os.environ.get("REPO_PATH") or os.getcwd())
    )
    try:
        url = (
            subprocess.check_output(
                ["git", "config", "--get", "remote.origin.url"],
                cwd=workspace,
                stderr=subprocess.DEVNULL,
            )
            .decode()
            .strip()
        )
    except Exception:
        log.debug("candidate git remote resolution skipped for %s", workspace)
        return None
    return normalize_repo_ref(url)


def evaluate_remote_evidence(
    *,
    candidate_path: Any,
    project_source_refs: list[str],
) -> RemoteEvidence:
    """Compare the candidate folder's git remote to the selected project.

    Returns a :class:`RemoteEvidence`. This never mutates state and never treats
    the remote as identity — it only classifies the strength of the proposal.
    """
    candidate_ref = detect_candidate_repo_ref(candidate_path)
    normalized_sources = tuple(
        dict.fromkeys(ref for ref in (normalize_repo_ref(r) for r in project_source_refs) if ref)
    )

    if candidate_ref is None:
        return RemoteEvidence(
            verdict="ambiguous",
            confidence=REMOTE_AMBIGUOUS_CONFIDENCE,
            reason=(
                "No unambiguous git remote was detected for the candidate folder; "
                "proposing at reduced confidence — explicit operator confirmation required."
            ),
            candidate_repo_ref=None,
            project_source_refs=normalized_sources,
        )

    if normalized_sources:
        lowered = {ref.lower() for ref in normalized_sources}
        if candidate_ref.lower() in lowered:
            return RemoteEvidence(
                verdict="match",
                confidence=REMOTE_MATCH_CONFIDENCE,
                reason=(
                    f"Candidate git remote '{candidate_ref}' matches the registered "
                    "project source ref; high-confidence bind proposal (remote is "
                    "evidence only, not project identity)."
                ),
                candidate_repo_ref=candidate_ref,
                project_source_refs=normalized_sources,
            )
        return RemoteEvidence(
            verdict="contradiction",
            confidence=0.0,
            reason=(
                f"Candidate git remote '{candidate_ref}' contradicts the registered "
                f"project source ref(s) {list(normalized_sources)}; failing closed "
                "without binding (remote is evidence only, not project identity)."
            ),
            candidate_repo_ref=candidate_ref,
            project_source_refs=normalized_sources,
        )

    return RemoteEvidence(
        verdict="unverified",
        confidence=REMOTE_UNVERIFIED_CONFIDENCE,
        reason=(
            f"Candidate git remote '{candidate_ref}' detected, but no registered "
            "project source ref was supplied to verify it; explicit operator "
            "confirmation required."
        ),
        candidate_repo_ref=candidate_ref,
        project_source_refs=normalized_sources,
    )


def _resolve_workspace_ref(workspace: str) -> tuple[str, str]:
    """Return ``(head_sha, branch)`` for *workspace*, or ``("", "")`` on failure."""
    try:
        head_sha = (
            subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=workspace,
                stderr=subprocess.DEVNULL,
            )
            .decode()
            .strip()
        )
        branch = (
            subprocess.check_output(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=workspace,
                stderr=subprocess.DEVNULL,
            )
            .decode()
            .strip()
        )
        return head_sha, branch
    except Exception:
        log.debug("workspace ref resolution skipped for %s", workspace)
        return "", ""


def _only(values: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {key: values[key] for key in keys if key in values}
