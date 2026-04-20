"""Event envelope model (v0.4.20: moved to writer.py; re-exported here for
back-compat with any importer outside this package)."""

from __future__ import annotations

from .writer import EventEnvelope

__all__ = ["EventEnvelope"]
