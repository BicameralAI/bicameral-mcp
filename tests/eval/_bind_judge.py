"""Headless caller-LLM driver for the bicameral-bind skill (#280 PR-2).

Drives `skills/bicameral-bind/SKILL.md` end-to-end against a synthetic
fixture repo: the LLM gets `read_file`, `validate_symbols`, and
`submit_binding` tools; we run a multi-turn tool-use loop until it
either submits a binding or aborts on weak evidence.

Modeled on tests/eval/_skill_judge.py — same x-api-key auth, same
fixture-cache keyed on SHA(model | skill_sha | repo_sha | input_sha).
Cache hits keep CI cost ~$0 unless the dataset, fixture repo, or skill
change.

Environment:
    ANTHROPIC_API_KEY                       required for live calls
    BICAMERAL_GROUNDING_EVAL_MODEL          default "claude-haiku-4-5-20251001"
    BICAMERAL_GROUNDING_EVAL_RECORD=1       force-bypass cache, re-record
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
SKILL_MD_PATH = REPO_ROOT / "skills" / "bicameral-bind" / "SKILL.md"
CACHE_DIR = Path(__file__).resolve().parent / "fixtures" / "bind_judge"

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_API_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
MAX_OUTPUT_TOKENS = 2048
MAX_TURNS = 8
REQUEST_TIMEOUT_S = 90.0


# ── Tool schemas exposed to the LLM ─────────────────────────────────────────


READ_FILE_TOOL: dict[str, Any] = {
    "name": "read_file",
    "description": (
        "Read the full contents of a file in the fixture repo. Use this to "
        "confirm a candidate symbol's body actually implements the decision's "
        "intent before submitting a binding. Path is repo-relative "
        "(e.g. 'src/checkout/orders.py')."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Repo-relative path to the file.",
            }
        },
        "required": ["file_path"],
    },
}


VALIDATE_SYMBOLS_TOOL: dict[str, Any] = {
    "name": "validate_symbols",
    "description": (
        "Confirm one or more candidate symbol names exist in the fixture "
        "repo's symbol index. Returns the list of (file_path, symbol_name) "
        "pairs that match. Use this before submitting a binding — the "
        "handler verifies symbols against the same index."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "candidates": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Symbol-name hypotheses (e.g. ['process_order', "
                    "'CheckoutRetryGuard.check_cap'])."
                ),
            }
        },
        "required": ["candidates"],
    },
}


SUBMIT_BINDING_TOOL: dict[str, Any] = {
    "name": "submit_binding",
    "description": (
        "Submit your final binding decision. Call this exactly once. "
        "Either provide (file_path, symbol_name) for the bind, or set "
        "abort=true with abort_reason if the evidence is too weak to bind "
        "(per skills/bicameral-bind/SKILL.md 'abort on weak evidence')."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "abort": {
                "type": "boolean",
                "description": "True if aborting on weak evidence; false if submitting a real binding.",
            },
            "file_path": {
                "type": "string",
                "description": "Repo-relative file the symbol lives in. Required when abort=false.",
            },
            "symbol_name": {
                "type": "string",
                "description": "The symbol to bind to. Required when abort=false.",
            },
            "abort_reason": {
                "type": "string",
                "description": "One sentence why the evidence was too weak. Required when abort=true.",
            },
            "reasoning": {
                "type": "string",
                "description": "One sentence explaining the choice (or the abort).",
            },
        },
        "required": ["abort", "reasoning"],
    },
}


TOOLS = [READ_FILE_TOOL, VALIDATE_SYMBOLS_TOOL, SUBMIT_BINDING_TOOL]


# ── Public result shape ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class BindJudgment:
    case_id: str
    aborted: bool
    bound_file: str | None
    bound_symbol: str | None
    abort_reason: str | None
    reasoning: str
    turns: int
    tokens_in: int
    tokens_out: int


# ── Helpers ─────────────────────────────────────────────────────────────────


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load_skill_md() -> str:
    if not SKILL_MD_PATH.exists():
        raise FileNotFoundError(f"SKILL.md not found at {SKILL_MD_PATH}")
    return SKILL_MD_PATH.read_text(encoding="utf-8")


def _scan_repo(repo_root: Path) -> dict[str, str]:
    """Walk the fixture repo and return {repo_relative_path: content}."""
    out: dict[str, str] = {}
    for path in sorted(repo_root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix not in {".py", ".ts", ".js", ".go", ".rs"}:
            continue
        rel = path.relative_to(repo_root).as_posix()
        out[rel] = path.read_text(encoding="utf-8")
    return out


def _index_symbols(files: dict[str, str]) -> list[tuple[str, str]]:
    """Build a flat (file, symbol) index from the fixture repo.

    Lightweight — extracts top-level def/class names and one level of
    methods. Good enough for the synthetic fixture; the production
    bicameral symbol index is richer but for the eval we just need
    deterministic 'does this symbol exist in this file' answers.
    """
    import re

    out: list[tuple[str, str]] = []
    py_def = re.compile(r"^(?:async\s+)?def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", re.MULTILINE)
    py_class = re.compile(r"^class\s+([a-zA-Z_][a-zA-Z0-9_]*)", re.MULTILINE)
    py_method = re.compile(r"^    (?:async\s+)?def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", re.MULTILINE)
    ts_func = re.compile(
        r"^(?:export\s+)?(?:async\s+)?function\s+([a-zA-Z_$][a-zA-Z0-9_$]*)",
        re.MULTILINE,
    )
    ts_class = re.compile(r"^(?:export\s+)?class\s+([a-zA-Z_$][a-zA-Z0-9_$]*)", re.MULTILINE)

    for file_path, content in files.items():
        if file_path.endswith((".py",)):
            for m in py_def.finditer(content):
                out.append((file_path, m.group(1)))
            for cls in py_class.finditer(content):
                cls_name = cls.group(1)
                out.append((file_path, cls_name))
                # Methods inside this class — capture as Class.method
                tail = content[cls.end() :]
                next_cls = py_class.search(tail)
                cls_body = tail[: next_cls.start()] if next_cls else tail
                for meth in py_method.finditer(cls_body):
                    out.append((file_path, f"{cls_name}.{meth.group(1)}"))
        elif file_path.endswith((".ts", ".js")):
            for m in ts_func.finditer(content):
                out.append((file_path, m.group(1)))
            for m in ts_class.finditer(content):
                out.append((file_path, m.group(1)))
    return out


def _execute_tool(
    tool_name: str,
    tool_input: dict,
    *,
    repo_files: dict[str, str],
    symbol_index: list[tuple[str, str]],
) -> str:
    """Deterministic tool dispatch — no real handler call, just fixture lookup.

    The eval measures whether the LLM picks the right (file, symbol) given
    the available evidence; we don't need the production bicameral handler
    in the loop. Cleaner cache + faster CI.
    """
    if tool_name == "read_file":
        path = tool_input.get("file_path", "")
        if path in repo_files:
            return repo_files[path]
        return f"ERROR: file not found: {path!r} (try one of: {sorted(repo_files.keys())[:5]}…)"

    if tool_name == "validate_symbols":
        candidates = tool_input.get("candidates") or []
        matches: list[dict] = []
        for cand in candidates:
            for fp, sym in symbol_index:
                if cand == sym or cand == sym.split(".")[-1]:
                    matches.append({"file_path": fp, "symbol_name": sym, "candidate": cand})
        return json.dumps({"matches": matches}, indent=2)

    return f"ERROR: unknown tool {tool_name!r}"


def _cache_path(model: str, skill_sha: str, repo_sha: str, input_sha: str) -> Path:
    key = f"{model}|{skill_sha}|{repo_sha}|{input_sha}"
    return CACHE_DIR / f"{_sha(key)}.json"


def _build_system_prompt(skill_md: str) -> str:
    return f"""\
You are a caller-LLM running the bicameral-bind skill against a synthetic
fixture repo. Your job: read the decision text, identify the right
(file, symbol) it should bind to, then call `submit_binding`.

Apply the skill's contract verbatim — especially the mandatory pre-bind
verification (Read at least one candidate file end-to-end, confirm via
`validate_symbols`, abort on weak evidence). The handler-side rejection
contract from #280 means your binding is verified against the same
symbol index `validate_symbols` queries; submitting a hallucinated
symbol will be rejected.

You have at most {MAX_TURNS} turns to gather evidence and submit. Do
not respond in plain text — every turn must be a tool_use block.

──── Skill contract (verbatim from skills/bicameral-bind/SKILL.md) ────

{skill_md}
"""


def _build_user_prompt(decision_description: str, repo_files: dict[str, str]) -> str:
    file_list = "\n".join(f"  - {p}" for p in sorted(repo_files.keys()))
    return f"""\
Decision to bind:

  {decision_description}

Fixture repo files available (use `read_file` to view contents):

{file_list}

Identify the right (file, symbol) for this decision and submit via
`submit_binding`. If after gathering evidence you can't point at a
specific function or class body that implements the decision, submit
with abort=true.
"""


def _call_messages_api(
    *,
    model: str,
    system_prompt: str,
    messages: list[dict],
    api_key: str,
) -> dict:
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
    with httpx.Client(timeout=REQUEST_TIMEOUT_S) as client:
        resp = client.post(ANTHROPIC_API_URL, headers=headers, json=payload)
        if resp.status_code >= 400:
            raise RuntimeError(f"Anthropic API error {resp.status_code}: {resp.text[:500]}")
        return resp.json()


# ── Public entrypoint ───────────────────────────────────────────────────────


def run_bind_judgment(
    *,
    case_id: str,
    decision_description: str,
    repo_root: Path,
    model: str | None = None,
    api_key: str | None = None,
    use_cache: bool = True,
) -> BindJudgment:
    """Drive the bicameral-bind skill against the fixture repo for one case.

    Returns a `BindJudgment` capturing the LLM's outcome (binding or abort)
    along with token + turn telemetry.

    Caching: response is cached to ``tests/eval/fixtures/bind_judge/`` keyed
    on (model, SKILL.md SHA, repo SHA, decision SHA). Set
    ``BICAMERAL_GROUNDING_EVAL_RECORD=1`` to bypass cache and re-record.
    """
    chosen_model: str = model or os.getenv("BICAMERAL_GROUNDING_EVAL_MODEL") or DEFAULT_MODEL
    skill_md = _load_skill_md()
    skill_sha = _sha(skill_md)

    repo_files = _scan_repo(repo_root)
    symbol_index = _index_symbols(repo_files)

    canonical_repo = json.dumps(repo_files, sort_keys=True, ensure_ascii=False)
    repo_sha = _sha(canonical_repo)
    input_sha = _sha(decision_description)

    cache_file = _cache_path(chosen_model, skill_sha, repo_sha, input_sha)
    force_record = os.getenv("BICAMERAL_GROUNDING_EVAL_RECORD", "").strip() in {"1", "true", "yes"}
    if use_cache and not force_record and cache_file.exists():
        cached: dict[str, Any] = json.loads(cache_file.read_text(encoding="utf-8"))
        return BindJudgment(
            case_id=case_id,
            aborted=bool(cached["aborted"]),
            bound_file=cached.get("bound_file"),
            bound_symbol=cached.get("bound_symbol"),
            abort_reason=cached.get("abort_reason"),
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
            f"repo={repo_sha[:8]}, input={input_sha[:8]})."
        )

    system_prompt = _build_system_prompt(skill_md)
    messages: list[dict] = [
        {"role": "user", "content": _build_user_prompt(decision_description, repo_files)}
    ]

    tokens_in = 0
    tokens_out = 0
    bound_file: str | None = None
    bound_symbol: str | None = None
    aborted = False
    abort_reason: str | None = None
    reasoning = ""
    turn = 0

    for turn in range(1, MAX_TURNS + 1):  # noqa: B007 — `turn` is read after the loop for telemetry
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
        # Append assistant turn to history regardless of tool/no-tool
        messages.append({"role": "assistant", "content": content})

        tool_uses = [b for b in content if b.get("type") == "tool_use"]
        if not tool_uses:
            # No tool call — agent gave up without submitting. Treat as abort.
            aborted = True
            abort_reason = "agent did not call any tool"
            reasoning = next((b.get("text", "") for b in content if b.get("type") == "text"), "")
            break

        # Submit the tool results, but check first if any tool_use is submit_binding.
        submit_call = next((tu for tu in tool_uses if tu.get("name") == "submit_binding"), None)
        if submit_call is not None:
            inp = submit_call.get("input") or {}
            reasoning = str(inp.get("reasoning") or "")
            if inp.get("abort"):
                aborted = True
                abort_reason = str(inp.get("abort_reason") or "")
            else:
                bound_file = str(inp.get("file_path") or "") or None
                bound_symbol = str(inp.get("symbol_name") or "") or None
            break

        # Otherwise execute read_file / validate_symbols and continue the loop.
        tool_results: list[dict] = []
        for tu in tool_uses:
            result = _execute_tool(
                tu.get("name", ""),
                tu.get("input") or {},
                repo_files=repo_files,
                symbol_index=symbol_index,
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
        # Loop exhausted without submit_binding.
        aborted = True
        abort_reason = f"hit MAX_TURNS={MAX_TURNS} without submitting a binding"

    judgment_payload: dict[str, Any] = {
        "aborted": aborted,
        "bound_file": bound_file,
        "bound_symbol": bound_symbol,
        "abort_reason": abort_reason,
        "reasoning": reasoning,
        "turns": turn,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
    }

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(
        json.dumps(judgment_payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return BindJudgment(
        case_id=case_id,
        aborted=aborted,
        bound_file=bound_file,
        bound_symbol=bound_symbol,
        abort_reason=abort_reason,
        reasoning=reasoning,
        turns=turn,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
    )


def fixture_exists(
    *, case_id: str, decision_description: str, repo_root: Path, model: str | None = None
) -> bool:
    """True if a cached fixture exists for these inputs (used to skip when
    no API key + no cache)."""
    chosen_model: str = model or os.getenv("BICAMERAL_GROUNDING_EVAL_MODEL") or DEFAULT_MODEL
    skill_md = _load_skill_md()
    skill_sha = _sha(skill_md)
    repo_files = _scan_repo(repo_root)
    canonical_repo = json.dumps(repo_files, sort_keys=True, ensure_ascii=False)
    repo_sha = _sha(canonical_repo)
    input_sha = _sha(decision_description)
    return _cache_path(chosen_model, skill_sha, repo_sha, input_sha).exists()
