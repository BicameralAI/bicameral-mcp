"""Tests for #337 foundations cycle 3 — content evaluation hook.

The hook is a dotted import path (``"module.path:function_name"``) that
the operator points at a callable in their own code. The
:func:`run_eval_hook` helper resolves the path, calls the callable,
and treats every failure mode (malformed path, ImportError,
AttributeError, non-callable, exception, non-bool return) as a
filter rejection with a stderr warning. Failed paths are cached so
subsequent items in the same process don't re-spam.

We test the helper directly + the combined ``evaluate_filters``
function + the per-adapter wiring.
"""

from __future__ import annotations

import sys
import types

import pytest

from filters import FilterSpec, evaluate_filters, run_eval_hook
from filters.evaluator import _reset_hook_caches_for_tests


@pytest.fixture(autouse=True)
def _reset_caches():
    _reset_hook_caches_for_tests()
    yield
    _reset_hook_caches_for_tests()


def _install_fake_module(name: str, **funcs):
    """Install a dummy module on sys.modules with the given callables."""
    mod = types.ModuleType(name)
    for fname, fn in funcs.items():
        setattr(mod, fname, fn)
    sys.modules[name] = mod
    return mod


@pytest.fixture
def fake_hooks_module():
    """Module fake_eval_hooks with a few sample callables for hook tests."""
    mod = _install_fake_module(
        "fake_eval_hooks",
        always_true=lambda cand: True,
        always_false=lambda cand: False,
        contains_urgent=lambda cand: "urgent" in (cand.get("text") or "").lower(),
        raises=lambda cand: (_ for _ in ()).throw(RuntimeError("hook crashed")),
        returns_none=lambda cand: None,
        returns_string=lambda cand: "truthy but not bool",
        not_callable_constant=42,
    )
    yield mod
    sys.modules.pop("fake_eval_hooks", None)


# ── run_eval_hook ───────────────────────────────────────────────────────────


def test_hook_returns_true_passes(fake_hooks_module):
    assert run_eval_hook("fake_eval_hooks:always_true", {"text": "x"}) is True


def test_hook_returns_false_rejects(fake_hooks_module):
    assert run_eval_hook("fake_eval_hooks:always_false", {"text": "x"}) is False


def test_hook_receives_candidate_dict(fake_hooks_module):
    assert run_eval_hook("fake_eval_hooks:contains_urgent", {"text": "URGENT decision"}) is True
    assert run_eval_hook("fake_eval_hooks:contains_urgent", {"text": "lunch?"}) is False


def test_hook_malformed_path_rejects(capsys):
    # Missing colon → "module.path:function_name" expected shape.
    assert run_eval_hook("no_colon_here", {"text": "x"}) is False
    err = capsys.readouterr().err
    assert "malformed" in err


def test_hook_module_not_found_rejects(capsys):
    assert run_eval_hook("nonexistent_module_xyz:fn", {"text": "x"}) is False
    err = capsys.readouterr().err
    assert "import failed" in err


def test_hook_attribute_not_found_rejects(capsys, fake_hooks_module):
    assert run_eval_hook("fake_eval_hooks:does_not_exist", {"text": "x"}) is False
    err = capsys.readouterr().err
    assert "has no attribute" in err


def test_hook_not_callable_rejects(capsys, fake_hooks_module):
    assert run_eval_hook("fake_eval_hooks:not_callable_constant", {"text": "x"}) is False
    err = capsys.readouterr().err
    assert "not" in err.lower() and "callable" in err.lower()


def test_hook_raises_rejects(capsys, fake_hooks_module):
    assert run_eval_hook("fake_eval_hooks:raises", {"text": "x"}) is False
    err = capsys.readouterr().err
    assert "raised" in err


def test_hook_returns_non_bool_rejects(capsys, fake_hooks_module):
    assert run_eval_hook("fake_eval_hooks:returns_string", {"text": "x"}) is False
    err = capsys.readouterr().err
    assert "non-bool" in err


def test_hook_returns_none_rejects(capsys, fake_hooks_module):
    # None is non-bool — same failure path.
    assert run_eval_hook("fake_eval_hooks:returns_none", {"text": "x"}) is False
    err = capsys.readouterr().err
    assert "non-bool" in err


def test_failed_hook_path_cached_no_respam(capsys):
    """Second call with the same failed path doesn't re-print the warning."""
    run_eval_hook("nonexistent_module_xyz:fn", {"text": "x"})
    run_eval_hook("nonexistent_module_xyz:fn", {"text": "y"})
    err = capsys.readouterr().err
    # Only one "import failed" line, not two.
    assert err.count("import failed") == 1


def test_successful_hook_path_cached(fake_hooks_module):
    """Multiple successful calls use the cached callable (one resolution)."""
    # Confirm both calls return correct results — caching is a
    # performance hint, the contract is: behavior is identical across
    # calls.
    assert run_eval_hook("fake_eval_hooks:always_true", {"text": "a"}) is True
    assert run_eval_hook("fake_eval_hooks:always_true", {"text": "b"}) is True


# ── evaluate_filters (universal + hook composition) ─────────────────────────


def test_evaluate_filters_no_hook_falls_through_to_universal():
    spec = FilterSpec(keyword_include=["decided"])
    assert evaluate_filters({"text": "decided"}, spec)
    assert not evaluate_filters({"text": "discussed"}, spec)


def test_evaluate_filters_universal_rejects_before_hook(fake_hooks_module):
    """When the universal layer rejects, the hook never runs.

    Pin this by setting the hook to one that would PASS, but the
    universal filter to one that won't — the hook running would pass
    the item, but it should be rejected by the universal layer first.
    """
    spec = FilterSpec(
        keyword_include=["never-matches"],
        eval_hook="fake_eval_hooks:always_true",
    )
    assert not evaluate_filters({"text": "anything else"}, spec)


def test_evaluate_filters_hook_runs_only_when_universal_passes(fake_hooks_module):
    spec = FilterSpec(
        keyword_include=["decided"],
        eval_hook="fake_eval_hooks:always_false",
    )
    # Universal passes (text contains "decided"), but hook rejects.
    assert not evaluate_filters({"text": "decided to ship"}, spec)


def test_evaluate_filters_both_pass(fake_hooks_module):
    spec = FilterSpec(
        keyword_include=["decided"],
        eval_hook="fake_eval_hooks:contains_urgent",
    )
    assert evaluate_filters({"text": "decided urgent matter"}, spec)


# ── Per-adapter wiring ──────────────────────────────────────────────────────


def test_linear_adapter_uses_eval_hook(tmp_path, monkeypatch, fake_hooks_module):
    from unittest.mock import patch

    from events.sources.linear import LinearPollingAdapter
    from secrets_store import put_secret
    from secrets_store.store import _reset_for_tests

    monkeypatch.setenv("BICAMERAL_KEYRING_DISABLE", "1")
    _reset_for_tests()

    put_secret(source_id="linear", key="api_key", value="lin_t")

    fake_issues = [
        {
            "identifier": "BIC-1",
            "url": "https://linear.app/x/issue/BIC-1",
            "completedAt": "2026-05-01T00:00:00Z",
        },
        {
            "identifier": "BIC-2",
            "url": "https://linear.app/x/issue/BIC-2",
            "completedAt": "2026-05-02T00:00:00Z",
        },
    ]

    def _fetch(self, url):
        if "BIC-1" in url:
            return {"query": "urgent fix", "decisions": [], "participants": []}
        return {"query": "regular work", "decisions": [], "participants": []}

    with (
        patch("sources.linear.poller.list_completed_issues", return_value=fake_issues),
        patch("sources.linear.adapter.LinearAdapter.fetch_active", new=_fetch),
    ):
        adapter = LinearPollingAdapter()
        result = adapter.pull(
            watermark_dir=tmp_path,
            config={"filters": {"eval_hook": "fake_eval_hooks:contains_urgent"}},
        )

    assert len(result) == 1
    assert adapter._pending_watermark == "2026-05-02T00:00:00Z"


def test_slack_adapter_uses_eval_hook(tmp_path, monkeypatch, fake_hooks_module):
    from unittest.mock import patch

    from events.sources.slack import SlackPollingAdapter
    from secrets_store import put_secret
    from secrets_store.store import _reset_for_tests

    monkeypatch.setenv("BICAMERAL_KEYRING_DISABLE", "1")
    _reset_for_tests()

    put_secret(source_id="slack", key="api_key", value="xoxb-t")
    fake_msgs = [
        {"ts": "1700000001.000000", "user": "U1", "text": "urgent: ship it"},
        {"ts": "1700000002.000000", "user": "U2", "text": "lunch?"},
    ]
    with (
        patch("sources.slack.poller.list_new_messages", return_value=fake_msgs),
        patch("sources.slack.client.get_user_info", return_value={}),
    ):
        adapter = SlackPollingAdapter()
        result = adapter.pull(
            watermark_dir=tmp_path,
            config={
                "channels": ["C01A"],
                "filters": {"eval_hook": "fake_eval_hooks:contains_urgent"},
            },
        )

    assert len(result) == 1
    assert "urgent" in result[0].get("decisions", [{}])[0].get("description", "").lower()


def test_adapter_continues_when_hook_raises(tmp_path, monkeypatch, fake_hooks_module, capsys):
    """Hook crashing must not crash the poll loop — that item is
    rejected, others (or none) still pass."""
    from unittest.mock import patch

    from events.sources.linear import LinearPollingAdapter
    from secrets_store import put_secret
    from secrets_store.store import _reset_for_tests

    monkeypatch.setenv("BICAMERAL_KEYRING_DISABLE", "1")
    _reset_for_tests()

    put_secret(source_id="linear", key="api_key", value="lin_t")

    fake_issues = [
        {
            "identifier": "BIC-1",
            "url": "https://linear.app/x/issue/BIC-1",
            "completedAt": "2026-05-01T00:00:00Z",
        },
    ]
    with (
        patch("sources.linear.poller.list_completed_issues", return_value=fake_issues),
        patch(
            "sources.linear.adapter.LinearAdapter.fetch_active",
            return_value={"query": "x", "decisions": [], "participants": []},
        ),
    ):
        adapter = LinearPollingAdapter()
        result = adapter.pull(
            watermark_dir=tmp_path,
            config={"filters": {"eval_hook": "fake_eval_hooks:raises"}},
        )

    assert result == []
    # Watermark still advances past the rejected item.
    assert adapter._pending_watermark == "2026-05-01T00:00:00Z"
    # Operator gets a stderr warning.
    assert "raised" in capsys.readouterr().err


def test_adapter_continues_when_hook_path_invalid(tmp_path, monkeypatch, capsys):
    """Bad import path is loud but non-fatal — poller continues."""
    from unittest.mock import patch

    from events.sources.linear import LinearPollingAdapter
    from secrets_store import put_secret
    from secrets_store.store import _reset_for_tests

    monkeypatch.setenv("BICAMERAL_KEYRING_DISABLE", "1")
    _reset_for_tests()

    put_secret(source_id="linear", key="api_key", value="lin_t")

    fake_issues = [
        {
            "identifier": "BIC-1",
            "url": "https://linear.app/x/issue/BIC-1",
            "completedAt": "2026-05-01T00:00:00Z",
        },
    ]
    with (
        patch("sources.linear.poller.list_completed_issues", return_value=fake_issues),
        patch(
            "sources.linear.adapter.LinearAdapter.fetch_active",
            return_value={"query": "x", "decisions": [], "participants": []},
        ),
    ):
        adapter = LinearPollingAdapter()
        result = adapter.pull(
            watermark_dir=tmp_path,
            config={"filters": {"eval_hook": "totally_made_up_module_xyz:fn"}},
        )

    # Item rejected (hook resolution failed), but poller didn't crash.
    assert result == []
    assert "import failed" in capsys.readouterr().err
