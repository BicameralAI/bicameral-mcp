"""GitHub source adapter (#337 Phase 3 — active ingest).

Public API: ``GitHubAdapter`` and ``parse_github_url``.

Auth: persisted via ``secrets_store`` under ``source_id="github"``,
key ``"api_key"`` (a Personal Access Token with ``repo`` scope, OR a
GitHub App installation token). Sent as ``Authorization: Bearer <token>``.
"""

from sources.github.adapter import GitHubAdapter, parse_github_url

__all__ = ["GitHubAdapter", "parse_github_url"]
