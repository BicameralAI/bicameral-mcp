"""Linear source adapter (#420 Phase 1a — active ingest).

Public API: ``LinearAdapter`` (the SourceAdapter implementation) and
``parse_linear_url`` (URL parsing helper, exposed for tests).

Auth: persisted via ``secrets_store`` under ``source_id="linear"``,
key ``"api_key"``. CLI / setup wizard puts the operator's Linear
personal API key into the OS keyring; the adapter reads it on each
fetch (no in-memory caching — keyring revocation propagates within
one tool call).
"""

from sources.linear.adapter import LinearAdapter, parse_linear_url

__all__ = ["LinearAdapter", "parse_linear_url"]
