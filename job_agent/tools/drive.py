from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, cast

from job_agent.tools._shared import (
    load_google_credentials,
    resolve_delegated_google_user,
)

GOOGLE_DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]
GOOGLE_DOC_MIME_TYPE = "application/vnd.google-apps.document"
DOCX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_UNSET = object()


def resolve_drive_delegated_user() -> str | None:
    drive_specific_user = os.getenv("RESUME_GOOGLE_DRIVE_DELEGATED_USER")
    if drive_specific_user is not None:
        return drive_specific_user.strip() or None

    drive_specific_user = os.getenv("GOOGLE_DRIVE_DELEGATED_USER")
    if drive_specific_user is not None:
        return drive_specific_user.strip() or None

    if not drive_delegation_enabled():
        return None

    return resolve_delegated_google_user()


def drive_delegation_enabled() -> bool:
    for name in ("RESUME_GOOGLE_DRIVE_USE_DELEGATION", "GOOGLE_DRIVE_USE_DELEGATION"):
        raw_value = os.getenv(name)
        if raw_value is None:
            continue
        return raw_value.strip().lower() not in {"0", "false", "no", "off"}
    return True


def extract_drive_folder_id(value: str | None) -> str | None:
    normalized = (value or "").strip()
    if not normalized:
        return None

    folder_match = re.search(r"/folders/([a-zA-Z0-9_-]+)", normalized)
    if folder_match:
        return folder_match.group(1)

    id_match = re.search(r"[?&]id=([a-zA-Z0-9_-]+)", normalized)
    if id_match:
        return id_match.group(1)

    if re.fullmatch(r"[a-zA-Z0-9_-]+", normalized):
        return normalized

    return None


def load_drive_credentials(delegated_user: str | None | object = _UNSET) -> Any:
    resolved_delegated_user = resolve_drive_delegated_user() if delegated_user is _UNSET else delegated_user
    return load_google_credentials(
        scopes=GOOGLE_DRIVE_SCOPES,
        delegated_user=cast(str | None, resolved_delegated_user),
        missing_credentials_message=(
            "Google Drive credentials are not configured. "
            "Use Application Default Credentials or set GOOGLE_SERVICE_ACCOUNT_FILE, "
            "GOOGLE_APPLICATION_CREDENTIALS, or GOOGLE_SERVICE_ACCOUNT_JSON."
        ),
    )


def build_drive_service(delegated_user: str | None | object = _UNSET) -> Any:
    import googleapiclient.discovery as google_discovery_module

    credentials = load_drive_credentials(delegated_user=delegated_user)
    build = cast(Any, google_discovery_module).build
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def _drive_auth_mode(delegated_user: str | None) -> str:
    return f"delegated:{delegated_user}" if delegated_user else "service_account"


def _create_google_doc_from_docx(
    *,
    service: Any,
    source_path: Path,
    name: str,
    folder_id: str,
) -> dict[str, Any]:
    from googleapiclient.http import MediaFileUpload

    media = MediaFileUpload(str(source_path), mimetype=DOCX_MIME_TYPE, resumable=False)
    metadata: dict[str, Any] = {
        "name": name,
        "mimeType": GOOGLE_DOC_MIME_TYPE,
        "parents": [folder_id],
    }
    return service.files().create(
        body=metadata,
        media_body=media,
        fields="id,name,webViewLink,parents",
        supportsAllDrives=True,
    ).execute()


def upload_docx_as_google_doc_impl(
    *,
    docx_path: str,
    name: str,
    folder_id: str | None = None,
    folder_url: str | None = None,
) -> dict[str, Any]:
    source_path = Path(docx_path)
    resolved_folder_id = folder_id or extract_drive_folder_id(folder_url)
    if not source_path.exists():
        return {
            "implemented": False,
            "reason": f"DOCX artifact does not exist: {source_path}",
        }
    if not resolved_folder_id:
        return {
            "implemented": False,
            "reason": "A Google Drive folder ID or folder URL is required before publishing Google Docs.",
        }

    delegated_user = resolve_drive_delegated_user()
    attempted_modes: list[str] = []
    errors: list[str] = []
    for candidate_delegated_user in [delegated_user, None] if delegated_user else [None]:
        auth_mode = _drive_auth_mode(candidate_delegated_user)
        if auth_mode in attempted_modes:
            continue
        attempted_modes.append(auth_mode)
        try:
            service = build_drive_service(delegated_user=candidate_delegated_user)
            created = _create_google_doc_from_docx(
                service=service,
                source_path=source_path,
                name=name,
                folder_id=resolved_folder_id,
            )
            return {
                "implemented": True,
                "google_doc_id": created.get("id"),
                "google_doc_url": created.get("webViewLink"),
                "name": created.get("name"),
                "folder_id": resolved_folder_id,
                "auth_mode": auth_mode,
                "attempted_auth_modes": attempted_modes,
            }
        except Exception as exc:
            errors.append(f"{auth_mode}: {exc}")

    return {
        "implemented": False,
        "reason": " ; ".join(errors) or "Google Drive upload failed.",
        "folder_id": resolved_folder_id,
        "attempted_auth_modes": attempted_modes,
    }
