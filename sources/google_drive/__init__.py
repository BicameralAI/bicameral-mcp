"""Google Drive / Docs source adapter (#337 Phase 5 — active ingest).

Public API: ``GoogleDriveAdapter`` and ``parse_gdrive_url``.

Auth: OAuth credentials persisted in ``secrets_store`` under
``source_id="google_drive"``, key ``"oauth_token"`` as a JSON blob
(the output of ``credentials.to_json()``). OAuth handshake itself
(obtaining the token) is Phase 5b — operator currently uses the
existing setup_wizard team-backend flow or runs a one-off script.
"""

from sources.google_drive.adapter import GoogleDriveAdapter, parse_gdrive_url

__all__ = ["GoogleDriveAdapter", "parse_gdrive_url"]
