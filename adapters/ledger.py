"""Ledger adapter — SurrealDB decision ledger via embedded Python SDK.

Uses SurrealDBLedgerAdapter backed by embedded SurrealDB (Python SDK v1.x).
- Default URL: surrealkv://~/.bicameral/ledger.db (persistent)
- Override via SURREAL_URL env var (e.g. memory:// for tests, ws://host:port for server)

The adapter is a singleton per process — one connection, reused across tool calls.
"""

from __future__ import annotations

import os

# Singleton for the real adapter (one connection per process)
_real_ledger_instance = None


def get_ledger():
    """Return the SurrealDB ledger adapter (singleton)."""
    global _real_ledger_instance

    if _real_ledger_instance is None:
        from ledger.adapter import SurrealDBLedgerAdapter
        _real_ledger_instance = SurrealDBLedgerAdapter(
            url=os.getenv("SURREAL_URL", None),
        )
    return _real_ledger_instance


def reset_ledger_singleton() -> None:
    """Reset the singleton — used in tests to get a fresh adapter instance."""
    global _real_ledger_instance
    _real_ledger_instance = None


def get_drift_analyzer():
    """Return the drift analyzer (Layer 1 hash-only by default).

    Swap this factory return to use SemanticDriftAnalyzer (L2+L3)
    or CodeGenomeDriftAnalyzer when ready.
    """
    from ledger.drift import HashDriftAnalyzer
    return HashDriftAnalyzer()
