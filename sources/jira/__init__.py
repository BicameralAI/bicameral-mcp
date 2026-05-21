"""Jira source adapter (#337 Phase A — active ingest).

Public API: ``JiraAdapter`` (the SourceAdapter implementation),
``parse_jira_url`` (URL parsing helper, exposed for tests), and
``flatten_adf`` (the pure ADF -> plain-text flattener).

Auth: persisted via ``secrets_store`` under ``source_id="jira"``, keys
``"api_email"`` and ``"api_token"``. Jira Cloud uses HTTP Basic auth —
``base64(email:token)`` — so two secret values are required (see
``docs/vendor/jira/auth.md``). The adapter reads them on each fetch (no
in-memory caching — keyring revocation propagates within one tool call).
"""

from sources.jira.adapter import JiraAdapter, normalize_issue_to_payload, parse_jira_url
from sources.jira.adf import flatten_adf

__all__ = [
    "JiraAdapter",
    "flatten_adf",
    "normalize_issue_to_payload",
    "parse_jira_url",
]
