"""Google Drive / Docs source adapter (#337 Phase 5 — active ingest).

Active path: operator pastes a Google Docs URL → adapter fetches the
document via the Docs API → text extracted from the structured body →
normalized to an IngestPayload.

URL forms accepted:
    https://docs.google.com/document/d/<id>/edit
    https://docs.google.com/document/d/<id>/edit?usp=sharing
    https://docs.google.com/document/d/<id>
    https://drive.google.com/file/d/<id>/view

The trailing ``<id>`` is a 25–60-char base64url segment.

Text extraction walks Google's structured-document model:
- ``body.content[]`` is a list of structural elements.
- ``paragraph`` elements contain ``paragraph.elements[]`` each with
  ``textRun.content``.
- ``table`` elements contain rows × cells × paragraphs.
- ``sectionBreak`` and other structural elements are skipped.

Decoration: paragraphs that have a ``namedStyleType`` of
``HEADING_1`` / ``HEADING_2`` / ``HEADING_3`` get markdown decoration
so the downstream gap-judge chain has topic anchors (mirrors the
Notion adapter pattern).

Auth model: OAuth token JSON loaded from secrets_store. The Docs API
``documents.get`` requires the ``https://www.googleapis.com/auth/documents.readonly``
scope at minimum.
"""

from __future__ import annotations

import re

_URL_RE = re.compile(
    r"^https?://(?:docs\.google\.com/document/d/|drive\.google\.com/file/d/)"
    r"(?P<id>[A-Za-z0-9_-]{25,128})(?:[/?#].*)?$"
)


def parse_gdrive_url(url: str) -> str:
    """Extract the document ID from a Google Docs/Drive URL.

    Raises:
        ValueError: URL doesn't match the expected shape.
    """
    m = _URL_RE.match(url.strip())
    if not m:
        raise ValueError(
            f"not a recognized Google Docs/Drive URL: {url!r}. "
            "Expected docs.google.com/document/d/<id> or drive.google.com/file/d/<id>."
        )
    return m.group("id")


def _extract_text_from_paragraph(para: dict) -> str:
    """Concatenate textRun content across a paragraph's elements."""
    pieces: list[str] = []
    for elem in para.get("elements") or []:
        run = elem.get("textRun") or {}
        content = run.get("content") or ""
        pieces.append(content)
    return "".join(pieces).strip()


def _decorate_paragraph(text: str, named_style: str) -> str:
    """Markdown-decorate a paragraph based on its namedStyleType."""
    if not text:
        return ""
    if named_style == "HEADING_1":
        return f"# {text}"
    if named_style == "HEADING_2":
        return f"## {text}"
    if named_style == "HEADING_3":
        return f"### {text}"
    if named_style == "HEADING_4":
        return f"#### {text}"
    return text


def _walk_table(table: dict) -> list[str]:
    """Flatten table cells into a list of paragraph strings."""
    out: list[str] = []
    for row in table.get("tableRows") or []:
        for cell in row.get("tableCells") or []:
            for sub_elem in cell.get("content") or []:
                if "paragraph" in sub_elem:
                    para = sub_elem["paragraph"]
                    style = (para.get("paragraphStyle") or {}).get("namedStyleType") or ""
                    text = _extract_text_from_paragraph(para)
                    decorated = _decorate_paragraph(text, style)
                    if decorated:
                        out.append(decorated)
    return out


def extract_document_text(document: dict) -> str:
    """Walk the Google Doc structured body and return joined plain text."""
    blocks: list[str] = []
    body = document.get("body") or {}
    for elem in body.get("content") or []:
        if "paragraph" in elem:
            para = elem["paragraph"]
            style = (para.get("paragraphStyle") or {}).get("namedStyleType") or ""
            text = _extract_text_from_paragraph(para)
            decorated = _decorate_paragraph(text, style)
            if decorated:
                blocks.append(decorated)
        elif "table" in elem:
            blocks.extend(_walk_table(elem["table"]))
        # sectionBreak, tableOfContents, etc. — skipped intentionally.
    return "\n".join(blocks).strip()


def normalize_document_to_payload(document: dict, doc_id: str) -> dict:
    """Build the ingest payload from a Google Docs document response."""
    title = document.get("title") or doc_id
    text = extract_document_text(document)
    decisions = [{"description": text, "title": title}] if text else []
    return {
        "query": title,
        "source": "google_drive",
        "title": title,
        "date": "",  # Docs API documents.get doesn't surface modifiedTime; Drive API does. Phase 5b.
        "participants": [],  # similarly — Drive API permissions list is Phase 5b.
        "decisions": decisions,
    }


class GoogleDriveAdapter:
    """SourceAdapter implementation for Google Drive / Docs (active path)."""

    source_id = "google_drive"

    def can_handle_url(self, url: str) -> bool:
        return bool(_URL_RE.match(url.strip()))

    def fetch_active(self, url: str) -> dict:
        doc_id = parse_gdrive_url(url)
        document = self._fetch_document(doc_id)
        return normalize_document_to_payload(document, doc_id)

    def _fetch_document(self, doc_id: str) -> dict:
        """Resolve credentials + Docs service + execute ``documents.get``.

        Split out from ``fetch_active`` so tests can override the whole
        network path without monkey-patching googleapiclient.discovery.
        """
        creds = self._resolve_credentials()
        service = self._build_docs_service(creds)
        try:
            return service.documents().get(documentId=doc_id).execute()
        except Exception as exc:  # noqa: BLE001 — surface as RuntimeError per SourceAdapter contract
            raise RuntimeError(f"Google Docs API call failed: {exc}") from exc

    def _resolve_credentials(self):
        """Build google.oauth2 Credentials from the stored token JSON.

        Raises ``RuntimeError`` when no token is stored — with a hint
        directing the operator to the (future) OAuth handshake flow.
        """
        import json as _json

        from secrets_store import get_secret

        token_json = get_secret(source_id=self.source_id, key="oauth_token")
        if not token_json:
            raise RuntimeError(
                "Google Drive OAuth token not configured. The Phase 5b OAuth "
                "handshake flow will populate this automatically; for now, "
                "store the token JSON (output of `credentials.to_json()`) via:\n"
                '  python -c "from secrets_store import put_secret; '
                "put_secret(source_id='google_drive', key='oauth_token', value='<json>')\""
            )
        try:
            from google.oauth2.credentials import Credentials  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "google-auth not installed; reinstall bicameral-mcp to pick up the dep"
            ) from exc
        info = _json.loads(token_json)
        return Credentials.from_authorized_user_info(
            info, scopes=["https://www.googleapis.com/auth/documents.readonly"]
        )

    def _build_docs_service(self, creds):
        """Build the Docs API service. Separate method for test override."""
        from googleapiclient.discovery import build  # type: ignore[import-not-found]

        return build("docs", "v1", credentials=creds, cache_discovery=False)
