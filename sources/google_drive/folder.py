"""Drive folder enumeration for Phase 5c passive ingest (#337).

Uses the Drive API ``files.list`` to enumerate Google Docs in a single
folder, filtered by ``modifiedTime`` so the polling adapter only fetches
items that have changed since the last watermark.

Non-recursive: subfolders are ignored. Operator must point the adapter
at one folder per source-config entry — same discipline as
``events/sources/local_directory.py`` (per the audit advisories for #344).

MIME-type filter: ``application/vnd.google-apps.document`` only. Other
shared content (spreadsheets, PDFs, images, etc.) is skipped — Phase 5
is Docs-only.
"""

from __future__ import annotations

_DOC_MIME = "application/vnd.google-apps.document"
_FOLDER_MIME = "application/vnd.google-apps.folder"


def list_visible_folders(creds) -> list[dict]:
    """Enumerate Google Drive folders the OAuth token has access to.

    Returns dicts shaped ``{"id", "name", "owners"}`` where owners is
    the comma-joined email list (Drive API field ``owners[].emailAddress``).
    Used by the ``bicameral-mcp source-list google_drive`` discovery
    primitive.

    Capped at 500 folders (5 pages × 100) — operators with deeper folder
    hierarchies should narrow by sharing only the relevant subset with
    the integration's service account.
    """
    from googleapiclient.discovery import build  # type: ignore[import-not-found]
    from googleapiclient.errors import HttpError  # type: ignore[import-not-found]

    service = build("drive", "v3", credentials=creds, cache_discovery=False)
    q = f"mimeType = '{_FOLDER_MIME}' and trashed = false"
    results: list[dict] = []
    page_token: str | None = None
    pages = 0
    while True:
        try:
            resp = (
                service.files()
                .list(
                    q=q,
                    fields="nextPageToken, files(id, name, owners(emailAddress))",
                    pageSize=_PAGE_SIZE,
                    pageToken=page_token,
                )
                .execute()
            )
        except HttpError as exc:
            raise RuntimeError(
                f"Drive folder enumeration failed: HTTP "
                f"{exc.resp.status if hasattr(exc, 'resp') else '?'}"
            ) from exc
        for f in resp.get("files") or []:
            owners = ",".join((o.get("emailAddress") or "") for o in (f.get("owners") or []))
            results.append({"id": f.get("id") or "", "name": f.get("name") or "", "owners": owners})
        pages += 1
        page_token = resp.get("nextPageToken")
        if not page_token or pages >= 5:
            break
    return results


_PAGE_SIZE = 100
_MAX_PAGES = 20  # 20 * 100 = 2000 docs max per pull; misuse bound


def list_docs_in_folder(
    creds,
    folder_id: str,
    *,
    modified_after: str | None = None,
):
    """Enumerate Google Docs in ``folder_id`` modified after ``modified_after``.

    Returns a list of dicts with keys ``id``, ``name``, ``modifiedTime``.
    Result is sorted ascending by ``modifiedTime`` so the caller can
    watermark on the last item.

    ``modified_after`` is an RFC 3339 timestamp string (the Drive API's
    native format). ``None`` returns every Doc in the folder, ordered by
    modifiedTime ascending.

    Raises ``RuntimeError`` on Drive API failures with an operator-facing
    message; the polling adapter catches and emits a warn-level log
    without advancing the watermark.
    """
    from googleapiclient.discovery import build  # type: ignore[import-not-found]
    from googleapiclient.errors import HttpError  # type: ignore[import-not-found]

    service = build("drive", "v3", credentials=creds, cache_discovery=False)

    # Compose the q expression. ``parents`` clause restricts to the
    # configured folder; mime clause filters to Google Docs only;
    # ``trashed=false`` excludes trash. The ``modifiedTime`` filter uses
    # ``>`` (strict) so the same RFC 3339 watermark doesn't replay the
    # last item every pull.
    q_parts = [
        f"'{folder_id}' in parents",
        f"mimeType = '{_DOC_MIME}'",
        "trashed = false",
    ]
    if modified_after:
        q_parts.append(f"modifiedTime > '{modified_after}'")
    q = " and ".join(q_parts)

    results: list[dict] = []
    page_token: str | None = None
    pages = 0
    while True:
        try:
            resp = (
                service.files()
                .list(
                    q=q,
                    fields="nextPageToken, files(id, name, modifiedTime)",
                    pageSize=_PAGE_SIZE,
                    orderBy="modifiedTime",
                    pageToken=page_token,
                )
                .execute()
            )
        except HttpError as exc:
            raise RuntimeError(
                f"Google Drive folder list failed for folder_id={folder_id!r}: "
                f"HTTP {exc.resp.status if hasattr(exc, 'resp') else '?'}"
            ) from exc
        except Exception as exc:  # noqa: BLE001 — surface as actionable error
            raise RuntimeError(
                f"Google Drive folder list failed for folder_id={folder_id!r}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

        results.extend(resp.get("files") or [])
        pages += 1
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
        if pages >= _MAX_PAGES:
            raise RuntimeError(
                f"folder {folder_id!r} has more than {_MAX_PAGES * _PAGE_SIZE} "
                "qualifying docs since the watermark — narrow the watch window "
                "or split the folder"
            )
    return results
