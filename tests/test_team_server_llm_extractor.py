"""Phase 3 — real Anthropic SDK extractor."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(autouse=True)
def env_setup(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-test-key")
    monkeypatch.delenv("BICAMERAL_TEAM_SERVER_EXTRACT_MODEL", raising=False)


class _StubResponse:
    def __init__(self, text):
        self.content = [type("Block", (), {"text": text})()]


class _StubClient:
    """Records messages.create calls; returns a configured payload."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    @property
    def messages(self):
        return self

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)


def _patch_anthropic(monkeypatch, client):
    import sys as _sys

    fake = type(_sys)("anthropic")
    fake.AsyncAnthropic = lambda **_kwargs: client
    fake.APIError = type("APIError", (Exception,), {})
    fake.APIStatusError = type("APIStatusError", (Exception,), {"status_code": 0})
    monkeypatch.setitem(_sys.modules, "anthropic", fake)
    return fake


@pytest.mark.asyncio
async def test_extract_returns_structured_decisions_from_mocked_anthropic_response(monkeypatch):
    from team_server.extraction import llm_extractor

    client = _StubClient([_StubResponse('{"decisions": [{"summary": "use REST"}]}')])
    _patch_anthropic(monkeypatch, client)
    result = await llm_extractor.extract("we decided to use REST", ["decided"])
    assert result["decisions"] == [{"summary": "use REST"}]
    assert "extract" in result["extractor_version"]
    assert result["matched_triggers"] == ["decided"]


@pytest.mark.asyncio
async def test_extract_passes_matched_triggers_into_prompt(monkeypatch):
    from team_server.extraction import llm_extractor

    client = _StubClient([_StubResponse('{"decisions": []}')])
    _patch_anthropic(monkeypatch, client)
    await llm_extractor.extract("hello", ["decided", "agreed"])
    prompt = client.calls[0]["messages"][0]["content"]
    assert "decided" in prompt
    assert "agreed" in prompt


@pytest.mark.asyncio
async def test_extract_retries_on_429_then_succeeds(monkeypatch):
    from team_server.extraction import llm_extractor

    fake = _patch_anthropic(monkeypatch, None)

    class APIStatusError429(Exception):
        status_code = 429

    fake.APIStatusError = APIStatusError429
    # Re-import won't help; we'll override behavior via _one_attempt patching
    # at a higher level instead. Simpler: replace AsyncAnthropic with a client
    # whose .messages.create raises APIStatusError429 once then returns.

    state = {"calls": 0}

    class _Flaky:
        @property
        def messages(self):
            return self

        async def create(self, **kw):
            state["calls"] += 1
            if state["calls"] == 1:
                raise APIStatusError429("rate-limited")
            return _StubResponse('{"decisions": [{"summary": "ok"}]}')

    fake.AsyncAnthropic = lambda **_kw: _Flaky()
    monkeypatch.setattr("asyncio.sleep", lambda *a, **kw: _noop_async())
    result = await llm_extractor.extract("text", [])
    assert result["decisions"] == [{"summary": "ok"}]
    assert state["calls"] == 2


async def _noop_async():
    return None


@pytest.mark.asyncio
async def test_extract_fails_soft_on_500_returns_error_field(monkeypatch):
    from team_server.extraction import llm_extractor

    fake = _patch_anthropic(monkeypatch, None)

    class APIStatusError500(Exception):
        status_code = 500

    fake.APIStatusError = APIStatusError500

    class _Always500:
        @property
        def messages(self):
            return self

        async def create(self, **kw):
            raise APIStatusError500("internal error")

    fake.AsyncAnthropic = lambda **_kw: _Always500()
    result = await llm_extractor.extract("text", [])
    assert result["decisions"] == []
    assert "500" in result["error"]


@pytest.mark.asyncio
async def test_extract_returns_empty_decisions_when_model_emits_unparseable_content(monkeypatch):
    from team_server.extraction import llm_extractor

    client = _StubClient([_StubResponse("not-json-at-all")])
    _patch_anthropic(monkeypatch, client)
    result = await llm_extractor.extract("text", [])
    assert result["decisions"] == []
    assert "parse-failure" in result["error"]


@pytest.mark.asyncio
async def test_extract_uses_env_overridden_model_when_set(monkeypatch):
    from team_server.extraction import llm_extractor

    monkeypatch.setenv("BICAMERAL_TEAM_SERVER_EXTRACT_MODEL", "claude-sonnet-4-6")
    client = _StubClient([_StubResponse('{"decisions": []}')])
    _patch_anthropic(monkeypatch, client)
    await llm_extractor.extract("text", [])
    assert client.calls[0]["model"] == "claude-sonnet-4-6"


@pytest.mark.asyncio
async def test_extract_raises_loud_when_anthropic_api_key_unset(monkeypatch):
    from team_server.extraction import llm_extractor

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(llm_extractor.MissingAnthropicKeyError) as exc_info:
        await llm_extractor.extract("text", [])
    assert "ANTHROPIC_API_KEY" in str(exc_info.value)
