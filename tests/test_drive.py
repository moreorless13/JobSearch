from __future__ import annotations

from pathlib import Path
from typing import Any

from job_agent.tools import drive
from job_agent.tools.drive import extract_drive_folder_id


def test_extract_drive_folder_id_from_folder_url() -> None:
    folder_id = extract_drive_folder_id("https://drive.google.com/drive/folders/folder123?usp=sharing")

    assert folder_id == "folder123"


def test_extract_drive_folder_id_from_raw_id() -> None:
    folder_id = extract_drive_folder_id("folder_123-ABC")

    assert folder_id == "folder_123-ABC"


def test_resolve_drive_delegated_user_can_disable_generic_delegation(monkeypatch: Any) -> None:
    monkeypatch.setenv("GOOGLE_DELEGATED_USER", "user@example.com")
    monkeypatch.setenv("RESUME_GOOGLE_DRIVE_USE_DELEGATION", "false")

    assert drive.resolve_drive_delegated_user() is None


def test_upload_docx_as_google_doc_falls_back_to_service_account(monkeypatch: Any, tmp_path: Path) -> None:
    docx_path = tmp_path / "resume.docx"
    docx_path.write_bytes(b"docx")
    attempted_delegated_users: list[str | None] = []

    monkeypatch.setenv("GOOGLE_DELEGATED_USER", "user@example.com")
    monkeypatch.setattr(
        drive,
        "build_drive_service",
        lambda delegated_user=drive._UNSET: attempted_delegated_users.append(delegated_user) or object(),
    )

    def fake_create_google_doc_from_docx(**kwargs: Any) -> dict[str, str]:
        if attempted_delegated_users[-1] == "user@example.com":
            raise RuntimeError("domain-wide delegation is not authorized for Drive")
        return {
            "id": "doc123",
            "name": kwargs["name"],
            "webViewLink": "https://docs.google.com/document/d/doc123/edit",
        }

    monkeypatch.setattr(drive, "_create_google_doc_from_docx", fake_create_google_doc_from_docx)

    result = drive.upload_docx_as_google_doc_impl(
        docx_path=str(docx_path),
        name="Resume",
        folder_id="folder123",
    )

    assert result["implemented"] is True
    assert result["auth_mode"] == "service_account"
    assert result["attempted_auth_modes"] == ["delegated:user@example.com", "service_account"]
