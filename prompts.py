"""MCP prompts for generic Bicameral tool workflows."""

from mcp import types

PROMPTS: dict[str, str] = {
    "preflight": (
        "Run Bicameral preflight before implementation. Call bicameral.preflight "
        "with the relevant files, symbols, diff context, and branch if known. "
        "Include checkpoint_hint (pre_work, mid_session, or pre_write) when "
        "session context is available; it is inert metadata forwarded to the "
        "daemon and does not change MCP authority or behavior. "
        "Use the daemon's graph evidence states as authoritative; do not infer "
        "global consistency from local no-conflict results."
    ),
    "bind": (
        "Bind a decision or candidate to code through Bicameral. Call "
        "bicameral.bind with the target id and exact binding hints. Do not use "
        "local symbol search as verified evidence; the bot daemon owns snapshot "
        "validation and BindingEvidence materialization."
    ),
    "ingest": (
        "Submit local source or session evidence through Bicameral. Call "
        "bicameral.ingest with source_uri, source_type, title, description, and "
        "evidence excerpts. Caller rationale is a hint, not verified evidence."
    ),
    "history_search": (
        "Inspect Bicameral state through daemon-owned read models. Use "
        "bicameral.history for replayed ledger state and bicameral.search for "
        "querying decisions, candidates, and bindings."
    ),
    "evidence_refresh": (
        "Request manual evidence currentness refresh through Bicameral. Call "
        "bicameral.evidence.refresh with a decision_id. Display the daemon-authored "
        "currentness or typed rejection as-is; do not run local link_commit, mutate "
        "ledger state, rebuild graphs, or infer compliance/signoff."
    ),
    "brief": (
        "Generate a decision context brief through Bicameral for onboarding or "
        "decision explanation. Call bicameral.brief with the feature area or "
        "cross-cutting topic. Optionally pass decision_ids to narrow to specific "
        "decisions, since to limit the timeline start, and include_graph to add "
        "decision graph edges. The result is a daemon-authored Markdown narrative "
        "with timeline, open items, and decision graph — display it as-is. Do not "
        "infer compliance, completeness, or safety beyond what the daemon states."
    ),
}


def list_prompt_definitions() -> list[types.Prompt]:
    return [types.Prompt(name=name, description=text) for name, text in sorted(PROMPTS.items())]


def get_prompt_result(name: str, arguments: dict[str, str]) -> types.GetPromptResult:
    if name not in PROMPTS:
        raise ValueError(f"Unknown Bicameral prompt: {name}")
    suffix = ""
    if arguments:
        rendered = ", ".join(f"{key}={value}" for key, value in sorted(arguments.items()))
        suffix = f"\n\nCaller-provided context: {rendered}"
    return types.GetPromptResult(
        description=PROMPTS[name],
        messages=[
            types.PromptMessage(
                role="user",
                content=types.TextContent(type="text", text=PROMPTS[name] + suffix),
            )
        ],
    )
