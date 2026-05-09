"""GoogleDriveAdapter — BackendAdapter against Google Drive Files API (#277).

Security posture:

  * Bundled OAuth client. We ship Bicameral's own ``client_id`` +
    ``client_secret`` for the desktop-app OAuth client. Per RFC 8252, the
    secret in installed apps is NOT confidential — it's a shared identifier,
    not auth credential, exactly like ``gh`` / ``gcloud`` / ``cursor``. The
    user-facing security model is the consent screen + Google's verified-app
    badge, not secret confidentiality.
  * Drive scope: ``https://www.googleapis.com/auth/drive.file`` only —
    Bicameral can only see files it created. Other Drive content stays
    invisible.
  * Token cache: ``~/.bicameral/google-drive-token.json`` written 0600.

Pull-only sync model — no daemons, no webhooks. Caller drives push/pull
cadence (typically once per tool-call lifecycle).

GCP / OAuth verification submission text lives at
``docs/google-oauth-verification-submission.md``.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

logger = logging.getLogger(__name__)

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file"
DEFAULT_TOKEN_PATH = Path.home() / ".bicameral" / "google-drive-token.json"
FOLDER_MIMETYPE = "application/vnd.google-apps.folder"

# Bundled OAuth client (#277). Replace these placeholders with the real values
# once the GCP project is provisioned. See docs/google-oauth-verification-submission.md
# for the verification process.
#
# Placeholder sentinel: detected at runtime so users get a clear error before
# Bicameral ships the real credentials, rather than an opaque OAuth failure.
_BUNDLED_CLIENT_ID = "734983128365-199hrimc908o5uam4kvgqgegrra5ta0j.apps.googleusercontent.com"
_BUNDLED_CLIENT_SECRET = "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"
_PLACEHOLDER_PREFIX = "REPLACE_WITH_"


class OAuthClientNotProvisionedError(RuntimeError):
    """Bicameral's bundled Drive OAuth client hasn't been published yet.

    Raised when the source still carries the placeholder client_id/secret.
    Once Jin provisions the GCP project and replaces the constants, this
    error becomes unreachable.
    """


class FolderNotFoundError(RuntimeError):
    """The configured Drive folder ID does not exist or is not shared with us."""


class ReadOnlyAccessError(RuntimeError):
    """The Drive folder is shared with us but we lack Editor access."""


def _md5_bytes(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


def _md5_file(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _build_drive_service(creds):
    """Construct the Drive v3 service. Stub seam for tests."""
    from googleapiclient.discovery import build  # type: ignore[import-not-found]

    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _bundled_client_config() -> dict:
    """Return the bundled Bicameral OAuth client config.

    Raises ``OAuthClientNotProvisionedError`` when the placeholder constants
    are still in source — this happens during local dev before the GCP
    project is provisioned. Once Jin replaces the constants, this branch
    becomes unreachable.
    """
    if _BUNDLED_CLIENT_ID.startswith(_PLACEHOLDER_PREFIX) or _BUNDLED_CLIENT_SECRET.startswith(
        _PLACEHOLDER_PREFIX
    ):
        raise OAuthClientNotProvisionedError(
            "Bicameral's Google Drive OAuth client isn't published yet. "
            "If you're a Bicameral developer, see "
            "docs/google-oauth-verification-submission.md for the GCP setup. "
            "If you're a user seeing this error, please file an issue — "
            "you got here ahead of the official release."
        )
    return {
        "installed": {
            "client_id": _BUNDLED_CLIENT_ID,
            "client_secret": _BUNDLED_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }


class GoogleDriveAdapter:
    """BackendAdapter against a single Google Drive folder."""

    def __init__(
        self,
        folder_id: str | None,
        author: str,
        token_path: Path | None = None,
    ) -> None:
        self._folder_id = folder_id
        self._author = author
        self._token_path = token_path or DEFAULT_TOKEN_PATH
        self._service = None
        self._service_lock = asyncio.Lock()

    # ── OAuth ────────────────────────────────────────────────────────────

    def _credentials(self):
        """Resolve cached or freshly-minted user credentials.

        Loads token from ``self._token_path`` if present and valid; refreshes
        on expiry; otherwise launches the local-loopback OAuth flow.
        Persists the resulting token at mode 0600.
        """
        from google.auth.transport.requests import Request  # type: ignore[import-not-found]
        from google.oauth2.credentials import Credentials  # type: ignore[import-not-found]
        from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore[import-not-found]

        creds = None
        if self._token_path.exists():
            creds = Credentials.from_authorized_user_file(str(self._token_path), [DRIVE_SCOPE])
        if creds and creds.valid:
            return creds
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            client_config = _bundled_client_config()
            flow = InstalledAppFlow.from_client_config(client_config, [DRIVE_SCOPE])
            creds = flow.run_local_server(port=0)

        self._token_path.parent.mkdir(parents=True, exist_ok=True)
        self._token_path.write_text(creds.to_json(), encoding="utf-8")
        try:
            os.chmod(self._token_path, 0o600)
        except OSError:
            pass  # Windows / non-POSIX
        return creds

    async def _service_ready(self):
        async with self._service_lock:
            if self._service is None:
                self._service = _build_drive_service(self._credentials())
            return self._service

    # ── Push / pull / list / lock ───────────────────────────────────────

    async def push_events(self, local_path: Path, remote_name: str) -> None:
        from googleapiclient.http import MediaFileUpload  # type: ignore[import-not-found]

        svc = await self._service_ready()
        existing = (
            svc.files()
            .list(
                q=f"'{self._folder_id}' in parents and name='{remote_name}' and trashed=false",
                fields="files(id, md5Checksum)",
                pageSize=1,
            )
            .execute()
            .get("files", [])
        )
        local_md5 = _md5_file(local_path)
        if existing and existing[0].get("md5Checksum") == local_md5:
            return
        media = MediaFileUpload(str(local_path), mimetype="application/x-ndjson", resumable=False)
        if existing:
            svc.files().update(fileId=existing[0]["id"], media_body=media).execute()
        else:
            svc.files().create(
                body={"name": remote_name, "parents": [self._folder_id]},
                media_body=media,
                fields="id",
            ).execute()

    async def pull_events(self, local_dir: Path, since_token: str | None) -> str:
        svc = await self._service_ready()
        local_dir.mkdir(parents=True, exist_ok=True)
        own_name = f"{self._author}.jsonl"

        q_parts = [f"'{self._folder_id}' in parents", "trashed=false", "name contains '.jsonl'"]
        if since_token:
            q_parts.append(f"modifiedTime > '{since_token}'")
        files = (
            svc.files()
            .list(
                q=" and ".join(q_parts),
                fields="files(id, name, md5Checksum, modifiedTime)",
                pageSize=1000,
            )
            .execute()
            .get("files", [])
        )
        max_modified = since_token or ""
        for f in files:
            name = f.get("name", "")
            if name == own_name or not name.endswith(".jsonl"):
                continue
            local_path = local_dir / name
            local_md5 = _md5_file(local_path) if local_path.exists() else None
            if local_md5 and local_md5 == f.get("md5Checksum"):
                if f.get("modifiedTime", "") > max_modified:
                    max_modified = f["modifiedTime"]
                continue
            data = svc.files().get_media(fileId=f["id"]).execute()
            local_path.write_bytes(data if isinstance(data, bytes) else bytes(data))
            if f.get("modifiedTime", "") > max_modified:
                max_modified = f["modifiedTime"]
        return max_modified

    @asynccontextmanager
    async def lock(self, remote_name: str):
        """Best-effort sentinel-file lock. No-blocking: caller handles races."""
        svc = await self._service_ready()
        sentinel_name = f"{remote_name}.lock"
        created = (
            svc.files()
            .create(
                body={"name": sentinel_name, "parents": [self._folder_id]},
                fields="id",
            )
            .execute()
        )
        lock_id = created.get("id")
        try:
            yield
        finally:
            try:
                svc.files().delete(fileId=lock_id).execute()
            except Exception as exc:
                logger.warning("[gdrive] failed to release sentinel lock: %s", exc)

    async def list_peers(self) -> AsyncIterator[str]:
        svc = await self._service_ready()
        files = (
            svc.files()
            .list(
                q=f"'{self._folder_id}' in parents and name contains '.jsonl' and trashed=false",
                fields="files(name)",
                pageSize=1000,
            )
            .execute()
            .get("files", [])
        )
        for f in files:
            name = f.get("name", "")
            if name.endswith(".jsonl"):
                yield name[: -len(".jsonl")]

    # ── Helpers used by setup wizard (Phase 3) ──────────────────────────

    def create_folder(self, name: str) -> str:
        """Create a new shared folder in the operator's Drive root. Returns ID."""
        # Synchronous: setup wizard runs outside the event loop.
        creds = self._credentials()
        svc = _build_drive_service(creds)
        result = (
            svc.files()
            .create(
                body={"name": name, "mimeType": FOLDER_MIMETYPE},
                fields="id",
            )
            .execute()
        )
        return result["id"]

    def verify_access(self) -> None:
        """Confirm we can list + write to the configured folder. Sync.

        Raises FolderNotFoundError on 404, ReadOnlyAccessError when
        capabilities.canEdit is False.
        """
        from googleapiclient.errors import HttpError  # type: ignore[import-not-found]

        creds = self._credentials()
        svc = _build_drive_service(creds)
        try:
            meta = svc.files().get(fileId=self._folder_id, fields="id,capabilities").execute()
        except HttpError as exc:
            if getattr(exc, "resp", None) is not None and exc.resp.status == 404:
                raise FolderNotFoundError(
                    f"Drive folder {self._folder_id!r} not found. Check the ID, "
                    "or ask the founding member to share it with your Google account."
                ) from exc
            raise
        if not meta.get("capabilities", {}).get("canEdit", False):
            raise ReadOnlyAccessError(
                f"Drive folder {self._folder_id!r} is read-only for this account. "
                "Ask the founding member to grant Editor access."
            )
