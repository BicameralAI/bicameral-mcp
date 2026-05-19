"""Notion source adapter (#420 Phase 2 — active ingest).

Public API: ``NotionAdapter`` (the SourceAdapter implementation) and
``parse_notion_url`` (URL parsing helper, exposed for tests).

Auth: persisted via ``secrets_store`` under ``source_id="notion"``,
key ``"api_key"``. The Notion internal-integration token model means
the operator creates an integration in Notion's admin UI, shares the
specific page/database with it, then stores the token here.
"""

from sources.notion.adapter import NotionAdapter, parse_notion_url

__all__ = ["NotionAdapter", "parse_notion_url"]
