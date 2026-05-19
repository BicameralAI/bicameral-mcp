"""Unit tests for m_shadow_divergence_log (#399 Stage C).

Pure-function tests against the real module — no MagicMock. Uses
``BICAMERAL_M_SHADOW_LOG_PATH`` env override to redirect the mirror
file into pytest's tmp_path so each test runs in isolation, matching
the pattern in tests/test_m2_grounding_log.py.
"""

from __future__ import annotations

import importlib
import json


def _reload_module_with_path(monkeypatch, tmp_path):
    """Reload m_shadow_divergence_log so it picks up the env override."""
    log_path = tmp_path / "m_shadow_divergence.jsonl"
    monkeypatch.setenv("BICAMERAL_M_SHADOW_LOG_PATH", str(log_path))
    import m_shadow_divergence_log as mod  # noqa: WPS433 — test-only import

    importlib.reload(mod)
    return mod, log_path


def _read_rows(log_path):
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]


# ── classify_divergence — pure function ─────────────────────────────


def test_classify_equal_sets(monkeypatch, tmp_path):
    mod, _ = _reload_module_with_path(monkeypatch, tmp_path)
    s = {("foo", "function"), ("Bar", "class")}
    assert mod.classify_divergence(s, s) == "equal"


def test_classify_substrate_superset(monkeypatch, tmp_path):
    """Substrate has extras; walker is a strict subset. Allowed direction
    — substrate captures module constants / macros that walkers skip."""
    mod, _ = _reload_module_with_path(monkeypatch, tmp_path)
    walker = {("foo", "function")}
    substrate = {("foo", "function"), ("CONST", "function")}
    assert mod.classify_divergence(walker, substrate) == "substrate-superset"


def test_classify_substrate_subset(monkeypatch, tmp_path):
    """Walker has extras; substrate is a strict subset. Forbidden —
    signals a substrate gap that breaks the parity contract."""
    mod, _ = _reload_module_with_path(monkeypatch, tmp_path)
    walker = {("foo", "function"), ("bar", "function")}
    substrate = {("foo", "function")}
    assert mod.classify_divergence(walker, substrate) == "substrate-subset"


def test_classify_symmetric(monkeypatch, tmp_path):
    """Both sides have unique entries — usually means a (name, type)
    pair has a different type label between walker and substrate."""
    mod, _ = _reload_module_with_path(monkeypatch, tmp_path)
    walker = {("foo", "function")}
    substrate = {("foo", "method")}
    assert mod.classify_divergence(walker, substrate) == "symmetric"


# ── Deterministic sampling ──────────────────────────────────────────


def test_sample_decision_is_deterministic(monkeypatch, tmp_path):
    """Same file_hash → same sampling answer across calls. Determinism
    is the load-bearing property — an investigator chasing a missing
    log entry needs to ask 'would this file have been sampled?' and
    get a repeatable answer."""
    mod, _ = _reload_module_with_path(monkeypatch, tmp_path)
    h = mod._hash_path("handlers/preflight.py")
    assert mod._should_sample_agreement(h) == mod._should_sample_agreement(h)


def test_sample_rate_is_roughly_one_in_fifty(monkeypatch, tmp_path):
    """Across 1000 distinct paths, ~2% should sample (1-in-50). Allow
    ±1.5% slack since the hash uniformity isn't perfect at small N."""
    mod, _ = _reload_module_with_path(monkeypatch, tmp_path)
    sampled = sum(
        1 for i in range(1000) if mod._should_sample_agreement(mod._hash_path(f"f{i}.py"))
    )
    rate = sampled / 1000
    assert 0.005 <= rate <= 0.035, f"sampling rate {rate:.3f} outside [0.5%, 3.5%]"


# ── record_divergence — end-to-end behavior ─────────────────────────


def test_record_disagreement_always_logs(monkeypatch, tmp_path):
    """Any non-equal divergence_kind must log regardless of file_hash —
    we never miss a substrate-subset signal."""
    mod, log_path = _reload_module_with_path(monkeypatch, tmp_path)

    mod.record_divergence(
        language_id="python",
        mode="shadow-substrate",
        rel_path="handlers/preflight.py",
        walker_set={("foo", "function"), ("missing", "function")},
        substrate_set={("foo", "function")},
    )

    rows = _read_rows(log_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["event_type"] == "m_shadow_divergence"
    assert row["language_id"] == "python"
    assert row["mode"] == "shadow-substrate"
    assert row["divergence_kind"] == "substrate-subset"
    assert row["walker_count"] == 2
    assert row["substrate_count"] == 1
    assert row["walker_only"] == ["missing:function"]
    assert row["substrate_only"] == []
    # rel_path must not appear — only file_hash
    assert "rel_path" not in row
    assert "file_hash" in row
    assert row["file_hash"] != "handlers/preflight.py"


def test_record_agreement_samples_consistently(monkeypatch, tmp_path):
    """For an equal walker/substrate pair, sampling decision is
    deterministic per file_hash. Call twice — same outcome."""
    mod, log_path = _reload_module_with_path(monkeypatch, tmp_path)
    s = {("foo", "function")}

    mod.record_divergence(
        language_id="python",
        mode="shadow-substrate",
        rel_path="some/file.py",
        walker_set=s,
        substrate_set=s,
    )
    rows1 = _read_rows(log_path)

    mod.record_divergence(
        language_id="python",
        mode="shadow-substrate",
        rel_path="some/file.py",
        walker_set=s,
        substrate_set=s,
    )
    rows2 = _read_rows(log_path)

    # Either both calls landed (file sampled) or both were dropped
    # (file not sampled). Asymmetric outcome would mean
    # non-determinism — fail.
    assert len(rows2) == 2 * len(rows1), (
        f"non-deterministic sampling: first call wrote {len(rows1)} rows, "
        f"second call wrote {len(rows2) - len(rows1)} rows. Same input MUST "
        f"produce same outcome."
    )


def test_record_skips_non_shadow_modes(monkeypatch, tmp_path):
    """walker-only and substrate-only callers should never invoke
    record_divergence, but the function defends with a no-op anyway
    so a future refactor can't accidentally pollute the log with
    irrelevant single-path events."""
    mod, log_path = _reload_module_with_path(monkeypatch, tmp_path)

    mod.record_divergence(
        language_id="python",
        mode="walker-only",
        rel_path="x.py",
        walker_set={("a", "function")},
        substrate_set={("a", "function"), ("b", "function")},
    )
    mod.record_divergence(
        language_id="elixir",
        mode="substrate-only",
        rel_path="y.ex",
        walker_set=set(),
        substrate_set={("a", "function")},
    )

    assert _read_rows(log_path) == []


def test_record_writes_no_raw_path_anywhere(monkeypatch, tmp_path):
    """Even with a path that contains identifying info (a user's home
    dir, a private module name), the only thing on disk should be the
    sha256 hex — not the raw path. Privacy invariant load-bearing for
    the optional PostHog relay; this test pins it for the local mirror
    too (defense in depth)."""
    mod, log_path = _reload_module_with_path(monkeypatch, tmp_path)
    private_path = "/Users/sensitive/repo/secret_module.py"

    mod.record_divergence(
        language_id="python",
        mode="shadow-substrate",
        rel_path=private_path,
        walker_set={("foo", "function"), ("bar", "function")},
        substrate_set={("foo", "function")},
    )

    raw_log = log_path.read_text()
    assert private_path not in raw_log
    assert "secret_module" not in raw_log
    assert "/Users/sensitive" not in raw_log
