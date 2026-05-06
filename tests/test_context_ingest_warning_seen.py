"""Behavioral tests for the session-scoped `seen_ingest_warning` flag
on `BicameralContext` (#200 Phase 2).

Persistence model: in-memory only, lives inside the existing
`_sync_state` dict (frozen-dataclass-safe — the dict reference stays
pinned, only contents mutate). Never persisted across sessions, so
the operator sees the pre-ingest leak warning at first ingest of
every fresh `claude -p` session.
"""

from __future__ import annotations

from context import BicameralContext


def _minimal_ctx() -> BicameralContext:
    """Construct a BicameralContext with stub adapters for tests that
    only exercise the `_sync_state` flag — no ledger / code-graph
    interaction needed."""
    return BicameralContext(
        repo_path=".",
        head_sha="0" * 40,
        ledger=object(),
        code_graph=object(),
        drift_analyzer=object(),
    )


def test_seen_ingest_warning_default_is_false() -> None:
    ctx = _minimal_ctx()
    assert ctx.seen_ingest_warning is False


def test_seen_ingest_warning_set_to_true_persists_within_session() -> None:
    ctx = _minimal_ctx()
    ctx.set_seen_ingest_warning(True)
    assert ctx.seen_ingest_warning is True
