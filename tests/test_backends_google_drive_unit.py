"""Phase 2 unit tests for events.backends.google_drive.GoogleDriveAdapter (#277).

All Drive API calls are stubbed via unittest.mock; no network. The adapter's
contract under test:

  * push_events: idempotent on md5; uploads or updates as appropriate
  * pull_events: skips own file; only downloads when remote md5 differs;
    returns max modifiedTime as the next since-token
  * lock: creates/deletes a sentinel file, with cleanup on exception
  * verify_access: raises FolderNotFoundError on 404, ReadOnlyAccessError
    when capabilities.canEdit is False
  * create_folder: returns the new folder ID
  * _credentials: raises MissingOAuthClientError when neither env nor
    ~/.bicameral/google-drive-client.json is provisioned
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _md5(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


@pytest.fixture
def stub_drive():
    """Patch googleapiclient.discovery.build → MagicMock.

    Tests interact with the returned mock to set list/get/create/update
    behavior per scenario.
    """
    pytest.importorskip("googleapiclient")
    pytest.importorskip("google_auth_oauthlib")
    with patch("events.backends.google_drive._build_drive_service") as build:
        svc = MagicMock()
        build.return_value = svc
        yield svc


@pytest.fixture
def stub_credentials():
    """Bypass real OAuth — return a sentinel object."""
    with patch(
        "events.backends.google_drive.GoogleDriveAdapter._credentials",
        return_value=MagicMock(),
    ) as m:
        yield m


def _files_list(svc, returned_files):
    svc.files.return_value.list.return_value.execute.return_value = {"files": returned_files}


@pytest.mark.asyncio
async def test_push_skips_when_md5_matches(tmp_path: Path, stub_drive, stub_credentials):
    from events.backends.google_drive import GoogleDriveAdapter

    body = b'{"e":"alice"}\n'
    local = tmp_path / "alice@x.com.jsonl"
    local.write_bytes(body)
    _files_list(stub_drive, [{"id": "rid", "md5Checksum": _md5(body)}])

    adapter = GoogleDriveAdapter(folder_id="folder-1", author="alice@x.com")
    await adapter.push_events(local, "alice@x.com.jsonl")

    stub_drive.files.return_value.update.assert_not_called()
    stub_drive.files.return_value.create.assert_not_called()


@pytest.mark.asyncio
async def test_push_updates_when_md5_differs(tmp_path: Path, stub_drive, stub_credentials):
    from events.backends.google_drive import GoogleDriveAdapter

    body = b'{"e":"alice-v2"}\n'
    local = tmp_path / "alice@x.com.jsonl"
    local.write_bytes(body)
    _files_list(stub_drive, [{"id": "rid", "md5Checksum": "stale-md5"}])

    adapter = GoogleDriveAdapter(folder_id="folder-1", author="alice@x.com")
    await adapter.push_events(local, "alice@x.com.jsonl")

    stub_drive.files.return_value.update.assert_called_once()
    args, kwargs = stub_drive.files.return_value.update.call_args
    assert kwargs["fileId"] == "rid"


@pytest.mark.asyncio
async def test_push_creates_when_remote_missing(tmp_path: Path, stub_drive, stub_credentials):
    from events.backends.google_drive import GoogleDriveAdapter

    body = b"first event\n"
    local = tmp_path / "alice@x.com.jsonl"
    local.write_bytes(body)
    _files_list(stub_drive, [])

    adapter = GoogleDriveAdapter(folder_id="folder-1", author="alice@x.com")
    await adapter.push_events(local, "alice@x.com.jsonl")

    stub_drive.files.return_value.create.assert_called_once()


@pytest.mark.asyncio
async def test_pull_writes_only_changed_peer_files(tmp_path: Path, stub_drive, stub_credentials):
    from events.backends.google_drive import GoogleDriveAdapter

    local_dir = tmp_path / "local"
    local_dir.mkdir()
    bob_existing = b"bob-old\n"
    (local_dir / "bob@x.com.jsonl").write_bytes(bob_existing)

    _files_list(
        stub_drive,
        [
            {
                "id": "alice-id",
                "name": "alice@x.com.jsonl",
                "md5Checksum": "x",
                "modifiedTime": "2026-05-08T10:00:00Z",
            },
            {
                "id": "bob-id",
                "name": "bob@x.com.jsonl",
                "md5Checksum": _md5(bob_existing),
                "modifiedTime": "2026-05-08T11:00:00Z",
            },
            {
                "id": "carol-id",
                "name": "carol@x.com.jsonl",
                "md5Checksum": "y",
                "modifiedTime": "2026-05-08T12:00:00Z",
            },
        ],
    )

    # Stub the get_media chain: returns a request whose .execute() returns bytes.
    def _media_for(fileId):
        media = MagicMock()
        media.execute.return_value = b"new-content-for-" + fileId.encode()
        return media

    stub_drive.files.return_value.get_media.side_effect = _media_for

    adapter = GoogleDriveAdapter(folder_id="folder-1", author="alice@x.com")
    token = await adapter.pull_events(local_dir, since_token=None)

    # Carol is the only peer that should have been downloaded.
    # Alice is owned (skipped); Bob's md5 matches local (skipped).
    downloaded_ids = [
        c.kwargs.get("fileId") or c.args[0]
        for c in stub_drive.files.return_value.get_media.call_args_list
    ]
    assert downloaded_ids == ["carol-id"]
    assert (local_dir / "carol@x.com.jsonl").read_bytes() == b"new-content-for-carol-id"
    # Alice's own file must not be created locally
    assert not (local_dir / "alice@x.com.jsonl").exists()
    # since-token = max modifiedTime across all listed files
    assert token == "2026-05-08T12:00:00Z"


@pytest.mark.asyncio
async def test_lock_creates_then_deletes_sentinel(stub_drive, stub_credentials):
    from events.backends.google_drive import GoogleDriveAdapter

    create = stub_drive.files.return_value.create
    delete = stub_drive.files.return_value.delete
    create.return_value.execute.return_value = {"id": "lock-id"}

    adapter = GoogleDriveAdapter(folder_id="folder-1", author="alice@x.com")
    async with adapter.lock("alice@x.com.jsonl"):
        create.assert_called_once()
    delete.assert_called_once()
    assert delete.call_args.kwargs.get("fileId") == "lock-id"


@pytest.mark.asyncio
async def test_lock_releases_on_exception(stub_drive, stub_credentials):
    from events.backends.google_drive import GoogleDriveAdapter

    create = stub_drive.files.return_value.create
    delete = stub_drive.files.return_value.delete
    create.return_value.execute.return_value = {"id": "lock-id"}

    adapter = GoogleDriveAdapter(folder_id="folder-1", author="alice@x.com")
    with pytest.raises(RuntimeError, match="boom"):
        async with adapter.lock("alice@x.com.jsonl"):
            raise RuntimeError("boom")
    delete.assert_called_once()


@pytest.mark.asyncio
async def test_verify_access_raises_on_404(stub_drive, stub_credentials):
    pytest.importorskip("googleapiclient")
    from googleapiclient.errors import HttpError

    from events.backends.google_drive import (
        FolderNotFoundError,
        GoogleDriveAdapter,
    )

    fake_resp = MagicMock(status=404, reason="Not Found")
    stub_drive.files.return_value.get.return_value.execute.side_effect = HttpError(
        fake_resp, b"Not Found"
    )

    adapter = GoogleDriveAdapter(folder_id="missing-folder", author="alice@x.com")
    with pytest.raises(FolderNotFoundError, match="missing-folder"):
        adapter.verify_access()


@pytest.mark.asyncio
async def test_verify_access_raises_on_read_only(stub_drive, stub_credentials):
    from events.backends.google_drive import (
        GoogleDriveAdapter,
        ReadOnlyAccessError,
    )

    stub_drive.files.return_value.get.return_value.execute.return_value = {
        "id": "f-1",
        "capabilities": {"canEdit": False},
    }
    adapter = GoogleDriveAdapter(folder_id="f-1", author="alice@x.com")
    with pytest.raises(ReadOnlyAccessError):
        adapter.verify_access()


@pytest.mark.asyncio
async def test_verify_access_passes_when_can_edit(stub_drive, stub_credentials):
    from events.backends.google_drive import GoogleDriveAdapter

    stub_drive.files.return_value.get.return_value.execute.return_value = {
        "id": "f-1",
        "capabilities": {"canEdit": True},
    }
    adapter = GoogleDriveAdapter(folder_id="f-1", author="alice@x.com")
    adapter.verify_access()  # no exception


@pytest.mark.asyncio
async def test_create_folder_returns_id(stub_drive, stub_credentials):
    from events.backends.google_drive import GoogleDriveAdapter

    stub_drive.files.return_value.create.return_value.execute.return_value = {"id": "new123"}
    adapter = GoogleDriveAdapter(folder_id=None, author="alice@x.com")
    result = adapter.create_folder("bicameral-foo-ledger")

    assert result == "new123"
    body = stub_drive.files.return_value.create.call_args.kwargs["body"]
    assert body["name"] == "bicameral-foo-ledger"
    assert body["mimeType"] == "application/vnd.google-apps.folder"


def test_bundled_client_config_raises_when_placeholders_present(monkeypatch):
    """Placeholder client_id/secret in source must surface a clear error.

    Once Jin replaces the constants with the published OAuth client, this
    test is rewritten to assert the config dict has the right shape instead.
    """
    pytest.importorskip("googleapiclient")
    pytest.importorskip("google_auth_oauthlib")
    from events.backends import google_drive
    from events.backends.google_drive import OAuthClientNotProvisionedError

    if not google_drive._BUNDLED_CLIENT_ID.startswith(google_drive._PLACEHOLDER_PREFIX):
        pytest.skip("Bundled OAuth client published; placeholder detection no longer relevant.")

    with pytest.raises(OAuthClientNotProvisionedError, match="isn.t published"):
        google_drive._bundled_client_config()
