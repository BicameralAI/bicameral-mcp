"""v0.4.12.1 — team adapter signature-drift regression tests.

The TeamWriteAdapter wraps SurrealDBLedgerAdapter via composition. As
new methods + kwargs are added to the inner adapter (v0.4.6 added
``authoritative_ref`` to ``ingest_commit``, v0.4.6 added ``ctx`` to
``ingest_payload``, v0.4.6 added ``backfill_empty_hashes`` /
``get_all_source_cursors`` / ``wipe_all_rows``), the wrapper has to
keep up — otherwise team-mode users hit silent TypeErrors and degraded
behavior on every call.

This silent failure mode was caught during v0.4.12 preflight dogfooding
on bicameral's own repo (which runs in team mode). Every link_commit
call had been failing since v0.4.6 — six releases of latent breakage.

These tests use ``inspect.signature`` to assert the wrapper's public
methods accept the same kwargs as the inner adapter, so any future
signature drift fails CI loudly.
"""

from __future__ import annotations

import inspect

import pytest

from events.team_adapter import TeamWriteAdapter
from ledger.adapter import SurrealDBLedgerAdapter


# Methods the wrapper MUST expose to forward to the inner adapter.
# Each entry is (method_name, kwargs_that_must_be_accepted).
_REQUIRED_FORWARDED = [
    # Write paths
    ("ingest_payload", {"ctx"}),                        # v0.4.6 added ctx
    ("ingest_commit", {"drift_analyzer", "authoritative_ref"}),  # v0.4.6 added authoritative_ref
    # Self-heal
    ("backfill_empty_hashes", {"drift_analyzer"}),      # v0.4.5 added entirely
    # Reset machinery
    ("get_all_source_cursors", set()),                  # v0.4.6 added entirely
    ("wipe_all_rows", set()),                           # v0.4.6 added entirely
    # Vocab cache
    ("lookup_vocab_cache", set()),
    ("upsert_vocab_cache", set()),
    # Read paths
    ("get_all_decisions", {"filter"}),
    ("search_by_query", set()),
    ("get_decisions_for_file", set()),
    ("get_undocumented_symbols", set()),
    ("get_source_cursor", set()),
    ("upsert_source_cursor", set()),
]


@pytest.mark.parametrize("method_name,required_kwargs", _REQUIRED_FORWARDED)
def test_team_adapter_method_exists(method_name, required_kwargs):
    """Every required method must exist on TeamWriteAdapter."""
    assert hasattr(TeamWriteAdapter, method_name), (
        f"TeamWriteAdapter is missing method {method_name!r} that the inner "
        f"SurrealDBLedgerAdapter exposes. Team-mode callers will hit "
        f"AttributeError or silent hasattr() degradation."
    )


@pytest.mark.parametrize("method_name,required_kwargs", _REQUIRED_FORWARDED)
def test_team_adapter_method_accepts_required_kwargs(method_name, required_kwargs):
    """Every required kwarg must appear in the wrapper's signature."""
    if not required_kwargs:
        return
    method = getattr(TeamWriteAdapter, method_name)
    sig = inspect.signature(method)
    params = set(sig.parameters.keys())
    missing = required_kwargs - params
    assert not missing, (
        f"TeamWriteAdapter.{method_name} is missing kwargs {missing}. "
        f"Inner adapter accepts these but wrapper doesn't — calls forwarding "
        f"these kwargs will fail with TypeError in team mode."
    )


def test_inner_and_wrapper_ingest_commit_kwargs_aligned():
    """Direct comparison: inner ingest_commit and wrapper ingest_commit
    must accept the same set of kwargs (modulo self).
    """
    inner_params = set(
        inspect.signature(SurrealDBLedgerAdapter.ingest_commit).parameters.keys()
    ) - {"self"}
    wrapper_params = set(
        inspect.signature(TeamWriteAdapter.ingest_commit).parameters.keys()
    ) - {"self"}
    missing_in_wrapper = inner_params - wrapper_params
    assert not missing_in_wrapper, (
        f"TeamWriteAdapter.ingest_commit is missing kwargs that inner "
        f"SurrealDBLedgerAdapter.ingest_commit accepts: {missing_in_wrapper}. "
        f"This is the exact regression that broke team mode in v0.4.6."
    )


def test_inner_and_wrapper_ingest_payload_kwargs_aligned():
    inner_params = set(
        inspect.signature(SurrealDBLedgerAdapter.ingest_payload).parameters.keys()
    ) - {"self"}
    wrapper_params = set(
        inspect.signature(TeamWriteAdapter.ingest_payload).parameters.keys()
    ) - {"self"}
    missing_in_wrapper = inner_params - wrapper_params
    assert not missing_in_wrapper, (
        f"TeamWriteAdapter.ingest_payload is missing kwargs that inner "
        f"SurrealDBLedgerAdapter.ingest_payload accepts: {missing_in_wrapper}."
    )
