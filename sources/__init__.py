"""External source adapters (#420 Phase 1+).

Each subpackage (e.g. ``sources.linear``) implements a single external
system's active-ingest pull and (in later phases) its passive/polling
loop. Adapters share the ``SourceAdapter`` protocol from
``sources.protocol`` so the CLI and future UI can register them
uniformly without per-source branching in the orchestration layer.
"""
