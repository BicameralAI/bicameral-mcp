"""Tests for #337 Phase 3b — GitHub polling adapter."""

from __future__ import annotations

import io
import json
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _disable_keyring(monkeypatch):
    monkeypatch.setenv("BICAMERAL_KEYRING_DISABLE", "1")
    from secrets_store.store import _reset_for_tests

    _reset_for_tests()
    yield
    _reset_for_tests()


def _mock_response(body, headers=None):
    raw = json.dumps(body).encode("utf-8")
    resp = io.BytesIO(raw)
    resp.status = 200  # type: ignore[attr-defined]

    class _Headers:
        def __init__(self, h):
            self._h = h

        def items(self):
            return list(self._h.items())

        def get(self, key, default=None):
            return self._h.get(key, default)

    resp.headers = _Headers(headers or {})  # type: ignore[attr-defined]

    class _Ctx:
        def __enter__(self):
            return resp

        def __exit__(self, *args):
            return False

    return _Ctx()


# ── poller.list_merged_pulls_since ──────────────────────────────────────────


def test_list_merged_filters_to_actually_merged():
    """state=closed returns both merged + abandoned; we keep only merged."""
    from sources.github.poller import list_merged_pulls_since

    pulls = [
        {
            "number": 1,
            "html_url": "u1",
            "updated_at": "2026-05-01T00:00:00Z",
            "merged_at": "2026-05-01T01:00:00Z",
        },
        {"number": 2, "html_url": "u2", "updated_at": "2026-05-02T00:00:00Z", "merged_at": None},
        {
            "number": 3,
            "html_url": "u3",
            "updated_at": "2026-05-03T00:00:00Z",
            "merged_at": "2026-05-03T01:00:00Z",
        },
    ]
    with patch("urllib.request.urlopen", return_value=_mock_response(pulls)):
        result = list_merged_pulls_since(api_key="ghp_x", owner="foo", repo="bar")
    assert [p["number"] for p in result] == [1, 3]


def test_list_merged_paginates_via_link_header():
    from sources.github.poller import list_merged_pulls_since

    page1_pulls = [
        {
            "number": 1,
            "html_url": "u1",
            "updated_at": "2026-05-01T00:00:00Z",
            "merged_at": "2026-05-01T01:00:00Z",
        },
    ]
    page2_pulls = [
        {
            "number": 2,
            "html_url": "u2",
            "updated_at": "2026-05-02T00:00:00Z",
            "merged_at": "2026-05-02T01:00:00Z",
        },
    ]
    page1 = _mock_response(
        page1_pulls,
        headers={"Link": ('<https://api.github.com/repos/foo/bar/pulls?page=2>; rel="next"')},
    )
    page2 = _mock_response(page2_pulls)
    with patch("urllib.request.urlopen", side_effect=[page1, page2]):
        result = list_merged_pulls_since(api_key="ghp_x", owner="foo", repo="bar")
    assert len(result) == 2


def test_list_merged_raises_on_api_error():
    import urllib.error

    from sources.github.poller import list_merged_pulls_since

    err = urllib.error.HTTPError(
        url="https://api.github.com/repos/foo/bar/pulls",
        code=403,
        msg="forbidden",
        hdrs=None,
        fp=io.BytesIO(b""),
    )
    with patch("urllib.request.urlopen", side_effect=err):
        with pytest.raises(RuntimeError, match="pulls listing failed"):
            list_merged_pulls_since(api_key="ghp_x", owner="foo", repo="bar")


# ── GitHubPollingAdapter.pull ───────────────────────────────────────────────


def test_pull_returns_payloads_for_new_prs(tmp_path):
    from events.sources.github import GitHubPollingAdapter
    from secrets_store import put_secret

    put_secret(source_id="github", key="api_key", value="ghp_x")

    fake_pulls = [
        {
            "number": 1,
            "html_url": "https://github.com/foo/bar/pull/1",
            "updated_at": "2026-05-01T00:00:00Z",
            "merged_at": "2026-05-01T01:00:00Z",
        },
        {
            "number": 2,
            "html_url": "https://github.com/foo/bar/pull/2",
            "updated_at": "2026-05-02T00:00:00Z",
            "merged_at": "2026-05-02T01:00:00Z",
        },
    ]
    fake_payload = {"source": "github", "decisions": [], "title": "x"}

    with (
        patch(
            "sources.github.poller.list_merged_pulls_since",
            return_value=fake_pulls,
        ),
        patch(
            "sources.github.adapter.GitHubAdapter.fetch_active",
            return_value=fake_payload,
        ),
    ):
        adapter = GitHubPollingAdapter()
        result = adapter.pull(watermark_dir=tmp_path, config={"repos": ["foo/bar"]})

    assert len(result) == 2
    # Per-repo watermark advanced.
    assert adapter._pending_watermarks["foo/bar"] == "2026-05-02T00:00:00Z"


def test_pull_returns_empty_when_repos_missing(tmp_path, capsys):
    from events.sources.github import GitHubPollingAdapter

    adapter = GitHubPollingAdapter()
    result = adapter.pull(watermark_dir=tmp_path, config={})
    assert result == []
    assert "owner/repo" in capsys.readouterr().err


def test_pull_skips_malformed_repo_entries(tmp_path, capsys):
    from events.sources.github import GitHubPollingAdapter
    from secrets_store import put_secret

    put_secret(source_id="github", key="api_key", value="ghp_x")

    with patch(
        "sources.github.poller.list_merged_pulls_since",
        return_value=[],
    ):
        adapter = GitHubPollingAdapter()
        # "no-slash" entry filtered before reaching the API.
        result = adapter.pull(
            watermark_dir=tmp_path,
            config={"repos": ["no-slash-here", "foo/bar"]},
        )

    assert result == []  # no merged PRs in foo/bar


def test_pull_per_repo_watermark_isolation(tmp_path):
    """Slow repo should not re-pull everything when fast repo advances."""
    from events.sources.github import GitHubPollingAdapter
    from secrets_store import put_secret

    put_secret(source_id="github", key="api_key", value="ghp_x")

    # Seed disk watermark with one repo already advanced.
    wm = tmp_path / "github.json"
    wm.write_text(json.dumps({"foo/fast": "2026-05-15T00:00:00Z"}))

    captured: list = []

    def _capture(*, api_key, owner, repo, updated_after):
        captured.append((f"{owner}/{repo}", updated_after))
        return []

    with patch(
        "sources.github.poller.list_merged_pulls_since",
        side_effect=_capture,
    ):
        adapter = GitHubPollingAdapter()
        adapter.pull(
            watermark_dir=tmp_path,
            config={"repos": ["foo/fast", "foo/slow"]},
        )

    # fast repo passes its existing watermark; slow repo passes None.
    by_repo = dict(captured)
    assert by_repo["foo/fast"] == "2026-05-15T00:00:00Z"
    assert by_repo["foo/slow"] is None


def test_pull_skips_individual_pr_fetch_failures(tmp_path, capsys):
    from events.sources.github import GitHubPollingAdapter
    from secrets_store import put_secret

    put_secret(source_id="github", key="api_key", value="ghp_x")

    fake_pulls = [
        {
            "number": 1,
            "html_url": "https://github.com/foo/bar/pull/1",
            "updated_at": "2026-05-01T00:00:00Z",
            "merged_at": "2026-05-01T01:00:00Z",
        },
        {
            "number": 2,
            "html_url": "https://github.com/foo/bar/pull/2",
            "updated_at": "2026-05-02T00:00:00Z",
            "merged_at": "2026-05-02T01:00:00Z",
        },
    ]

    def _flaky(self, url):
        if "/pull/1" in url:
            raise RuntimeError("transient")
        return {"source": "github", "decisions": [], "title": "x"}

    with (
        patch(
            "sources.github.poller.list_merged_pulls_since",
            return_value=fake_pulls,
        ),
        patch(
            "sources.github.adapter.GitHubAdapter.fetch_active",
            new=_flaky,
        ),
    ):
        adapter = GitHubPollingAdapter()
        result = adapter.pull(watermark_dir=tmp_path, config={"repos": ["foo/bar"]})

    assert len(result) == 1
    err = capsys.readouterr().err
    assert "foo/bar#1" in err
    # Watermark advances past PR 2 even though PR 1 was skipped.
    assert adapter._pending_watermarks["foo/bar"] == "2026-05-02T00:00:00Z"


def test_confirm_watermark_persists_per_repo_dict(tmp_path):
    from events.sources.github import GitHubPollingAdapter

    adapter = GitHubPollingAdapter()
    adapter._watermark_path = tmp_path / "github.json"
    adapter._pending_watermarks = {
        "foo/bar": "2026-05-19T00:00:00Z",
        "foo/baz": "2026-05-18T00:00:00Z",
    }
    adapter.confirm_watermark()
    data = json.loads((tmp_path / "github.json").read_text())
    assert data == {
        "foo/bar": "2026-05-19T00:00:00Z",
        "foo/baz": "2026-05-18T00:00:00Z",
    }


def test_registered_in_ADAPTERS():
    from events.sources import ADAPTERS
    from events.sources.github import GitHubPollingAdapter

    assert ADAPTERS["github"] is GitHubPollingAdapter
