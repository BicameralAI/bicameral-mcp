"""Step-0 headless caller-LLM driver for the bicameral-preflight skill (#306 Part B).

Tests the **upstream decision** Part A's Step-1 eval cannot reach: given a
topic and a handler-empty preflight result, does the caller LLM elect to
call ``bicameral.history()`` to surface decisions that might exist in the
ledger but aren't pinned to the touched files? Or does it proceed
silently, dropping any vocab-mismatch / ungrounded decisions on the floor?

This is the "implicit tool invocation" failure pattern OpenAI documents
(developers.openai.com/blog/eval-skills): a tool whose name + description
+ system-prompt guidance is not strong enough for the agent to elect to
call it without being prompted. Step-1 (Part A) measures recall once
``bicameral.history()`` has already been called; Step-0 measures whether
the agent calls it at all.

Modeled on tests/eval/_bind_judge.py — same x-api-key auth, same
multi-turn tool-use loop, same retry envelope, same fixture-cache keyed
on SHA(model | skill_sha | input_sha). The cache discipline keeps CI
cost ~$0 unless the dataset or skill SHA change.

Environment:
    ANTHROPIC_API_KEY                                required for live calls
    BICAMERAL_PREFLIGHT_INVOCATION_EVAL_MODEL        default DEFAULT_MODEL below
    BICAMERAL_PREFLIGHT_INVOCATION_EVAL_RECORD=1     force-bypass cache, re-record
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_MD_PATH = REPO_ROOT / "skills" / "bicameral-preflight" / "SKILL.md"
CACHE_DIR = Path(__file__).resolve().parent / "fixtures" / "skill_invocation_judge"

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_API_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
MAX_OUTPUT_TOKENS = 2048
MAX_TURNS = 4  # Step-0 is short: decide → optionally fetch → submit.
REQUEST_TIMEOUT_S = 90.0

# Retry envelope mirrors _bind_judge.py — single httpx ReadTimeout on a
# transient API blip should not crash the entire eval run.
_RETRY_ATTEMPTS = 3
_RETRY_BACKOFF_BASE_S = 2.0


# ── Tool schemas exposed to the LLM ─────────────────────────────────────────


BICAMERAL_HISTORY_TOOL: dict[str, Any] = {
    "name": "bicameral_history",
    "description": (
        "Read the full decision ledger grouped by feature. Use this when "
        "bicameral.preflight returned fired=false but the topic could "
        "plausibly relate to a previously-recorded decision — for example, "
        "topics involving policy areas (PII, retention, auth, performance "
        "SLAs) or cross-cutting concerns (logging, error handling) where a "
        "decision may exist but not be bound to the specific files you "
        "named. Returns the seeded ledger as JSON; you can then reason "
        "about which feature groups relate to your topic."
    ),
    "input_schema": {
        "type": "object",
        "properties": {},
    },
}


SUBMIT_DECISION_TO_PROCEED_TOOL: dict[str, Any] = {
    "name": "submit_decision_to_proceed",
    "description": (
        "Signal that you have finished your discovery phase and are ready "
        "to proceed with implementation. Call this exactly once, at the "
        "end. If you called bicameral_history first, include a one-sentence "
        "summary of what you found; if you proceeded without fetching "
        "history, state why the topic does not warrant a ledger read."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "reasoning": {
                "type": "string",
                "description": (
                    "One sentence explaining the decision to fetch (or "
                    "skip) bicameral_history. The eval reads this only as "
                    "an audit string — the load-bearing signal is whether "
                    "bicameral_history was called before this submit."
                ),
            },
        },
        "required": ["reasoning"],
    },
}


TOOLS = [BICAMERAL_HISTORY_TOOL, SUBMIT_DECISION_TO_PROCEED_TOOL]


# ── Public result shape ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class InvocationJudgment:
    """Outcome of one Step-0 row evaluation.

    ``invoked_history`` is the load-bearing signal — combined with the
    row's ``should_invoke_history`` ground truth, it produces the
    four-cell outcome classification:

    | should_invoke | invoked | outcome                            |
    |---------------|---------|------------------------------------|
    | True          | True    | ``invoked_history_correctly``      |
    | True          | False   | ``skipped_history_should_have``    |
    | False         | True    | ``invoked_history_unnecessarily``  |
    | False         | False   | ``proceeded_without_fetch``        |
    """

    case_id: str
    invoked_history: bool
    submitted: bool  # False iff loop exhausted without submit_decision_to_proceed
    reasoning: str
    turns: int
    tokens_in: int
    tokens_out: int


# Outcome category labels — exposed so the runner + summary renderer can
# classify without duplicating the truth table.
OUTCOME_INVOKED_CORRECTLY = "invoked_history_correctly"
OUTCOME_SKIPPED_SHOULD_HAVE = "skipped_history_should_have"
OUTCOME_INVOKED_UNNECESSARILY = "invoked_history_unnecessarily"
OUTCOME_PROCEEDED_WITHOUT_FETCH = "proceeded_without_fetch"

ALL_OUTCOMES = (
    OUTCOME_INVOKED_CORRECTLY,
    OUTCOME_SKIPPED_SHOULD_HAVE,
    OUTCOME_INVOKED_UNNECESSARILY,
    OUTCOME_PROCEEDED_WITHOUT_FETCH,
)


def classify_outcome(*, should_invoke: bool, invoked: bool) -> str:
    """Pure function — table-driven outcome classifier. Used by the runner
    and the summary renderer; tested standalone in
    tests/test_skill_invocation_judge.py so a refactor of the truth table
    fails fast without an API call."""
    if should_invoke and invoked:
        return OUTCOME_INVOKED_CORRECTLY
    if should_invoke and not invoked:
        return OUTCOME_SKIPPED_SHOULD_HAVE
    if not should_invoke and invoked:
        return OUTCOME_INVOKED_UNNECESSARILY
    return OUTCOME_PROCEEDED_WITHOUT_FETCH


# ── Helpers ─────────────────────────────────────────────────────────────────


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load_skill_md() -> str:
    if not SKILL_MD_PATH.exists():
        raise FileNotFoundError(f"SKILL.md not found at {SKILL_MD_PATH}")
    return SKILL_MD_PATH.read_text(encoding="utf-8")


def _cache_path(model: str, skill_sha: str, input_sha: str) -> Path:
    key = f"{model}|{skill_sha}|{input_sha}"
    return CACHE_DIR / f"{_sha(key)}.json"


def _execute_tool(
    tool_name: str,
    tool_input: dict,
    *,
    seeded_decisions: list[dict],
) -> str:
    """Deterministic tool dispatch. The only data-bearing tool is
    ``bicameral_history`` — returns the seeded ledger as JSON.
    ``submit_decision_to_proceed`` is terminal and handled in the loop
    (this function is unreachable for it). Unknown tools return an
    error string so the LLM's tool_use loop can recover."""
    if tool_name == "bicameral_history":
        # Mirror the actual bicameral.history() response shape — feature-grouped.
        return json.dumps({"features": seeded_decisions}, indent=2, ensure_ascii=False)
    return f"ERROR: unknown tool {tool_name!r}"


def _build_system_prompt(skill_md: str) -> str:
    return f"""\
You are a caller-LLM running the bicameral-preflight skill. The user has
just issued an implementation prompt; bicameral.preflight has already
been called with the relevant file paths and returned fired=false (no
region-anchored decisions match the touched files).

Your Step-0 decision: should you also call ``bicameral_history`` to
surface decisions that might exist in the ledger but aren't pinned to
the files you named? The skill's guidance below tells you when this
upstream fetch is appropriate.

You have at most {MAX_TURNS} turns. Always end with a tool call — do
not respond in plain text. Either call ``bicameral_history`` first
and then ``submit_decision_to_proceed``, or call
``submit_decision_to_proceed`` directly if the topic does not warrant
a full-ledger read.

──── Skill contract (verbatim from skills/bicameral-preflight/SKILL.md) ────

{skill_md}
"""


def _build_user_prompt(*, topic: str) -> str:
    return f"""\
Implementation prompt from the user: "{topic}"

bicameral.preflight returned fired=false. No region-anchored decisions
match the files you named. Decide whether the topic warrants a
``bicameral_history`` read before proceeding, then submit via
``submit_decision_to_proceed``.
"""


def _call_messages_api(
    *,
    model: str,
    system_prompt: str,
    messages: list[dict],
    api_key: str,
) -> dict:
    """POST to the Anthropic Messages API with bounded retry on transient
    failures. Mirrors _bind_judge.py's _call_messages_api — identical
    error envelope so the runner's per-case catch can record an
    ``eval_error`` outcome on terminal failure."""
    import time

    headers = {
        "anthropic-version": ANTHROPIC_API_VERSION,
        "content-type": "application/json",
        "x-api-key": api_key,
    }
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "temperature": 0,
        "system": [
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": messages,
        "tools": TOOLS,
    }

    last_exc: Exception | None = None
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT_S) as client:
                resp = client.post(ANTHROPIC_API_URL, headers=headers, json=payload)

            if resp.status_code >= 500 or resp.status_code == 429:
                last_exc = RuntimeError(
                    f"Anthropic API {resp.status_code} (attempt {attempt}/{_RETRY_ATTEMPTS}): "
                    f"{resp.text[:200]}"
                )
                if attempt < _RETRY_ATTEMPTS:
                    time.sleep(_RETRY_BACKOFF_BASE_S * (4 ** (attempt - 1)))
                    continue
                raise last_exc
            if resp.status_code >= 400:
                raise RuntimeError(f"Anthropic API error {resp.status_code}: {resp.text[:500]}")
            return resp.json()
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
            last_exc = exc
            if attempt < _RETRY_ATTEMPTS:
                time.sleep(_RETRY_BACKOFF_BASE_S * (4 ** (attempt - 1)))
                continue
            raise RuntimeError(
                f"Anthropic API transport failure after {_RETRY_ATTEMPTS} attempts: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

    raise RuntimeError(f"unreachable: retry loop exited without return (last_exc={last_exc!r})")


# ── Public entrypoint ───────────────────────────────────────────────────────


def run_invocation_judgment(
    *,
    case_id: str,
    topic: str,
    seeded_decisions: list[dict],
    model: str | None = None,
    api_key: str | None = None,
    use_cache: bool = True,
) -> InvocationJudgment:
    """Drive the bicameral-preflight skill's Step-0 invocation decision for
    one case.

    Returns an ``InvocationJudgment`` capturing whether the LLM elected to
    call ``bicameral_history`` before submitting, plus token + turn
    telemetry. The runner combines this with the row's
    ``should_invoke_history`` ground truth to classify the outcome.

    Caching: response is cached to
    ``tests/eval/fixtures/skill_invocation_judge/`` keyed on (model,
    SKILL.md SHA, dataset row SHA). Set
    ``BICAMERAL_PREFLIGHT_INVOCATION_EVAL_RECORD=1`` to bypass cache and
    re-record."""
    chosen_model: str = (
        model or os.getenv("BICAMERAL_PREFLIGHT_INVOCATION_EVAL_MODEL") or DEFAULT_MODEL
    )
    skill_md = _load_skill_md()
    skill_sha = _sha(skill_md)

    canonical_input = json.dumps(
        {"topic": topic, "seeded_decisions": seeded_decisions},
        sort_keys=True,
        ensure_ascii=False,
    )
    input_sha = _sha(canonical_input)

    cache_file = _cache_path(chosen_model, skill_sha, input_sha)
    force_record = os.getenv("BICAMERAL_PREFLIGHT_INVOCATION_EVAL_RECORD", "").strip() in {
        "1",
        "true",
        "yes",
    }
    if use_cache and not force_record and cache_file.exists():
        cached: dict[str, Any] = json.loads(cache_file.read_text(encoding="utf-8"))
        return InvocationJudgment(
            case_id=case_id,
            invoked_history=bool(cached["invoked_history"]),
            submitted=bool(cached.get("submitted", True)),
            reasoning=str(cached.get("reasoning") or ""),
            turns=int(cached.get("turns") or 0),
            tokens_in=int(cached.get("tokens_in") or 0),
            tokens_out=int(cached.get("tokens_out") or 0),
        )

    chosen_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not chosen_key.strip():
        raise RuntimeError(
            "ANTHROPIC_API_KEY missing and no cached fixture exists for "
            f"(model={chosen_model}, case={case_id}, skill={skill_sha[:8]}, "
            f"input={input_sha[:8]})."
        )

    system_prompt = _build_system_prompt(skill_md)
    messages: list[dict] = [{"role": "user", "content": _build_user_prompt(topic=topic)}]

    tokens_in = 0
    tokens_out = 0
    invoked_history = False
    submitted = False
    reasoning = ""
    turn = 0

    for turn in range(1, MAX_TURNS + 1):  # noqa: B007
        data = _call_messages_api(
            model=chosen_model,
            system_prompt=system_prompt,
            messages=messages,
            api_key=chosen_key,
        )
        usage = data.get("usage") or {}
        tokens_in += int(usage.get("input_tokens", 0))
        tokens_out += int(usage.get("output_tokens", 0))

        content = data.get("content") or []
        messages.append({"role": "assistant", "content": content})

        tool_uses = [b for b in content if b.get("type") == "tool_use"]
        if not tool_uses:
            # No tool call — agent gave up without submitting. Treat as a
            # silent proceed: invoked_history reflects whatever was called
            # earlier in the turn loop (default False); submitted=False
            # flags the anomaly for the runner.
            reasoning = next((b.get("text", "") for b in content if b.get("type") == "text"), "")
            break

        # Terminal-tool check FIRST — submit_decision_to_proceed wins even
        # if it appears alongside a (redundant) bicameral_history in the
        # same turn. The agent might "submit and also fetch in one shot";
        # the load-bearing signal is whether history was *ever* called.
        submit_call = next(
            (tu for tu in tool_uses if tu.get("name") == "submit_decision_to_proceed"), None
        )
        history_calls = [tu for tu in tool_uses if tu.get("name") == "bicameral_history"]
        if history_calls:
            invoked_history = True

        if submit_call is not None:
            inp = submit_call.get("input") or {}
            reasoning = str(inp.get("reasoning") or "")
            submitted = True
            break

        # Non-terminal turn — execute bicameral_history + continue.
        tool_results: list[dict] = []
        for tu in tool_uses:
            result = _execute_tool(
                tu.get("name", ""),
                tu.get("input") or {},
                seeded_decisions=seeded_decisions,
            )
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tu.get("id"),
                    "content": result,
                }
            )
        messages.append({"role": "user", "content": tool_results})
    else:
        # Loop exhausted without submit_decision_to_proceed.
        reasoning = f"hit MAX_TURNS={MAX_TURNS} without submitting"
        submitted = False

    judgment_payload: dict[str, Any] = {
        "invoked_history": invoked_history,
        "submitted": submitted,
        "reasoning": reasoning,
        "turns": turn,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
    }

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(
        json.dumps(judgment_payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return InvocationJudgment(
        case_id=case_id,
        invoked_history=invoked_history,
        submitted=submitted,
        reasoning=reasoning,
        turns=turn,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
    )


def fixture_exists(*, topic: str, seeded_decisions: list[dict], model: str | None = None) -> bool:
    """True if a cached fixture exists for these inputs. Used by the
    pytest runner to skip cleanly when no API key + no cache."""
    chosen_model: str = (
        model or os.getenv("BICAMERAL_PREFLIGHT_INVOCATION_EVAL_MODEL") or DEFAULT_MODEL
    )
    skill_md = _load_skill_md()
    skill_sha = _sha(skill_md)
    canonical_input = json.dumps(
        {"topic": topic, "seeded_decisions": seeded_decisions},
        sort_keys=True,
        ensure_ascii=False,
    )
    input_sha = _sha(canonical_input)
    return _cache_path(chosen_model, skill_sha, input_sha).exists()
