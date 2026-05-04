"""Offline tests for the headless extraction driver.

Does not call the Anthropic API. Exercises:
- Step 1 excerpt parsing from a real SKILL.md
- JSON response parsing with/without markdown fences
- Cache hit path (pre-seeded cache file, no API key set)

Network-dependent end-to-end tests live in CI only, gated on
ANTHROPIC_API_KEY being present.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _extract_headless import (  # noqa: E402
    CACHE_DIR,
    SKILL_MD_PATH,
    _cache_path,
    _extract_step1_excerpt,
    _load_skill_md,
    _parse_response_json,
    _sha,
    extract_from_current_skill,
)


def test_step1_excerpt_isolates_step1_from_real_skill_md():
    """Parser must extract Step 1 (Extract candidate decisions) from the
    actual bicameral-ingest SKILL.md without bleeding into Step 2."""
    md = _load_skill_md(SKILL_MD_PATH)
    excerpt = _extract_step1_excerpt(md)

    assert "Extract candidate decisions" in excerpt
    # Case-insensitive check: original SKILL.md used "Include"/"Exclude" as
    # subsection headers; the v0.4.3 few-shot rewrite uses "INCLUDE"/"EXCLUDE".
    # Both forms should be acceptable.
    lower = excerpt.lower()
    assert "include" in lower
    assert "exclude" in lower
    # Step 2 header or its body should not be present
    assert "Validate relevance" not in excerpt
    # Hard size ceiling so a future SKILL.md rewrite can't silently include
    # the entire file if header parsing breaks. Bumped from 4000 → 8000 → 10000 → 15000
    # to accommodate the v0.4.3+ few-shot variants which add worked examples to Step 1.
    assert len(excerpt) < 15000, f"excerpt suspiciously long: {len(excerpt)} chars"


@pytest.mark.parametrize(
    "body,expected_decisions",
    [
        ('{"decisions": [{"description": "a"}], "action_items": []}', [{"description": "a"}]),
        ('```json\n{"decisions": [{"description": "b"}]}\n```', [{"description": "b"}]),
        ('```\n{"decisions": [{"description": "c"}]}\n```', [{"description": "c"}]),
        ('  \n\n{"decisions": [{"description": "d"}]}\n\n  ', [{"description": "d"}]),
    ],
)
def test_parse_response_json_tolerates_common_formats(body, expected_decisions):
    r = _parse_response_json(body)
    assert r["decisions"] == expected_decisions
    assert "action_items" in r  # default-filled


def test_parse_response_json_rejects_non_object():
    with pytest.raises(ValueError, match="non-object"):
        _parse_response_json('["not", "an", "object"]')


def test_parse_response_json_rejects_invalid_json():
    with pytest.raises(ValueError, match="did not return valid JSON"):
        _parse_response_json("not json at all")


def test_cache_hit_returns_without_auth(monkeypatch):
    """When a cache file exists for (model, skill_sha, transcript_sha) we
    must return its contents without ever touching the network. Proven by
    unsetting ANTHROPIC_API_KEY: if the cache were missed the call would
    raise a RuntimeError on the missing env var."""
    tmp = Path(tempfile.mkdtemp())
    skill_md = tmp / "SKILL.md"
    skill_md.write_text(
        "## Steps\n### 1. Extract candidate decisions\nfake rules\n### 2. Next\nlater\n"
    )
    transcript = "Decision: use BM25 for retrieval."
    model = os.getenv("M1_EVAL_MODEL", "claude-haiku-4-5-20251001")

    skill_sha = _sha(skill_md.read_text())
    transcript_sha = _sha(transcript)
    cache_file = _cache_path(skill_sha, transcript_sha, model)
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(
        json.dumps({"decisions": [{"description": "use BM25 for retrieval"}], "action_items": []})
    )

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    try:
        result = extract_from_current_skill(transcript, source_ref="test", skill_md_path=skill_md)
    finally:
        cache_file.unlink(missing_ok=True)

    assert result["decisions"][0]["description"] == "use BM25 for retrieval"
    assert result["action_items"] == []


def test_missing_api_key_raises_when_cache_miss(monkeypatch, tmp_path):
    """Cache-cold call with ANTHROPIC_API_KEY unset must raise a clear
    RuntimeError rather than attempt an unauthenticated request."""
    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text("## Steps\n### 1. Extract candidate decisions\nrules\n")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        extract_from_current_skill(
            "fresh transcript text",
            source_ref="test-miss",
            skill_md_path=skill_md,
            use_cache=False,
        )
