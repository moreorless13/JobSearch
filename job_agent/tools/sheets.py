from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from typing import Any

from agents import function_tool

from job_agent.tools.dedupe import build_duplicate_key, normalize_text

DEFAULT_MATCH_STRATEGY = "hybrid"
GOOGLE_SHEETS_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

PREFERRED_FIELD_ORDER = [
    "date_added",
    "company",
    "role_title",
    "location",
    "source",
    "posting_url",
    "careers_url",
    "recruiter_name",
    "contact_email",
    "status",
    "applied_date",
    "follow_up_date",
    "interview_date",
    "offer_date",
    "outcome",
    "resume_version",
    "cover_letter_version",
    "fit_score",
    "match_summary",
    "salary",
    "remote_or_local",
    "email_update_type",
    "last_email_update",
    "next_steps",
    "notes",
    "duplicate_key",
    "priority",
]

CANONICAL_HEADER_LABELS = {
    "date_added": "Date Added",
    "company": "Company",
    "role_title": "Role Title",
    "location": "Location",
    "source": "Source",
    "posting_url": "Posting URL",
    "careers_url": "Careers URL",
    "recruiter_name": "Recruiter Name",
    "contact_email": "Contact Email",
    "status": "Status",
    "applied_date": "Applied Date",
    "follow_up_date": "Follow-Up Date",
    "interview_date": "Interview Date",
    "offer_date": "Offer Date",
    "outcome": "Outcome",
    "resume_version": "Resume Version",
    "cover_letter_version": "Cover Letter Version",
    "fit_score": "Fit Score",
    "match_summary": "Match Summary",
    "salary": "Salary",
    "remote_or_local": "Remote or Local",
    "email_update_type": "Email Update Type",
    "last_email_update": "Last Email Update",
    "next_steps": "Next Steps",
    "notes": "Notes",
    "duplicate_key": "Duplicate Key",
    "priority": "Priority",
}

HEADER_ALIASES = {
    "date_added": ["date added", "date", "created", "created date"],
    "company": ["company", "employer", "organization"],
    "role_title": ["role title", "title", "role", "job title", "position"],
    "location": ["location", "job location", "office location"],
    "source": ["source", "job source", "board"],
    "posting_url": ["posting url", "job url", "job posting", "job link", "url"],
    "careers_url": ["careers url", "careers page", "company careers", "application url"],
    "recruiter_name": ["recruiter name", "recruiter", "contact name"],
    "contact_email": ["contact email", "email", "recruiter email"],
    "status": ["status", "stage"],
    "applied_date": ["applied date", "date applied"],
    "follow_up_date": ["follow-up date", "follow up date", "next follow up"],
    "interview_date": ["interview date", "interview"],
    "offer_date": ["offer date"],
    "outcome": ["outcome", "result"],
    "resume_version": ["resume version", "resume"],
    "cover_letter_version": ["cover letter version", "cover letter"],
    "fit_score": ["fit score", "score", "match score"],
    "match_summary": ["match summary", "fit summary", "summary"],
    "salary": ["salary", "compensation", "pay range"],
    "remote_or_local": ["remote or local", "remote/local", "work mode", "remote", "onsite/hybrid/remote"],
    "email_update_type": ["email update type", "email type"],
    "last_email_update": ["last email update", "last email"],
    "next_steps": ["next steps", "action items"],
    "notes": ["notes", "comments"],
    "duplicate_key": ["duplicate key", "dedupe key", "unique key"],
    "priority": ["priority"],
}

HEADER_LOOKUP = {
    normalize_text(alias): canonical
    for canonical, aliases in HEADER_ALIASES.items()
    for alias in [canonical.replace("_", " "), CANONICAL_HEADER_LABELS.get(canonical, canonical), *aliases]
}


def infer_duplicate_key(row: dict[str, Any]) -> str:
    return row.get("duplicate_key") or build_duplicate_key(
        row.get("company"),
        row.get("role_title"),
        row.get("location"),
    )


def rows_match(existing: dict[str, Any], candidate: dict[str, Any], match_strategy: str = DEFAULT_MATCH_STRATEGY) -> bool:
    existing_duplicate_key = infer_duplicate_key(existing)
    candidate_duplicate_key = infer_duplicate_key(candidate)

    if match_strategy in {"duplicate_key", "hybrid"} and existing_duplicate_key == candidate_duplicate_key:
        return True

    if match_strategy in {"posting_url", "hybrid"}:
        existing_posting = normalize_text(existing.get("posting_url"))
        candidate_posting = normalize_text(candidate.get("posting_url"))
        if existing_posting and existing_posting == candidate_posting:
            return True

    if match_strategy in {"company_title_location", "hybrid"}:
        fields = ("company", "role_title", "location")
        if all(normalize_text(existing.get(field)) == normalize_text(candidate.get(field)) for field in fields):
            return True

    return False


def merge_tracker_rows(existing: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(existing)

    for key, value in update.items():
        if key.startswith("__"):
            continue
        if value in (None, "", []):
            continue
        if key == "notes" and merged.get("notes"):
            merged["notes"] = f"{merged['notes']}\n{value}"
            continue
        if key == "status" and merged.get("status") and normalize_text(value) == "new":
            # Preserve a meaningful tracker state instead of downgrading updates back to the default intake status.
            continue
        merged[key] = value

    merged["duplicate_key"] = infer_duplicate_key(merged)
    return merged


def extract_spreadsheet_id(sheet_url: str) -> str:
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", sheet_url)
    if not match:
        raise ValueError("Could not extract spreadsheet ID from the provided sheet URL.")
    return match.group(1)


def sheet_range(sheet_name: str, a1_range: str = "") -> str:
    escaped = sheet_name.replace("'", "''")
    prefix = f"'{escaped}'"
    return f"{prefix}!{a1_range}" if a1_range else prefix


def normalize_header(header: str) -> str:
    return normalize_text(header)


def resolve_header_mapping(headers: list[str]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for index, header in enumerate(headers):
        canonical = HEADER_LOOKUP.get(normalize_header(header))
        if canonical and canonical not in mapping:
            mapping[canonical] = index
    return mapping


def tab_score(headers: list[str], row_count: int) -> int:
    mapping = resolve_header_mapping(headers)
    score = len(mapping) * 10 + min(row_count, 20)
    for required in ("company", "role_title", "status"):
        if required in mapping:
            score += 8
    if "duplicate_key" in mapping:
        score += 4
    return score


def choose_active_tab(tabs: list[dict[str, Any]]) -> dict[str, Any]:
    if not tabs:
        raise ValueError("The spreadsheet does not contain any tabs.")
    return max(tabs, key=lambda tab: (tab["score"], tab["row_count"], tab["name"]))


def serialize_cell_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (list, dict)):
        return json.dumps(value, sort_keys=True)
    return str(value)


def column_letter(index: int) -> str:
    if index < 1:
        raise ValueError("Column index must be 1-based and positive.")
    letters = []
    while index:
        index, remainder = divmod(index - 1, 26)
        letters.append(chr(65 + remainder))
    return "".join(reversed(letters))


def preferred_header_for_field(field: str) -> str:
    return CANONICAL_HEADER_LABELS.get(field, field.replace("_", " ").title())


def row_from_sheet_values(sheet_name: str, headers: list[str], values: list[str], row_number: int) -> dict[str, Any]:
    raw_by_header = {header: values[index] if index < len(values) else "" for index, header in enumerate(headers)}
    mapping = resolve_header_mapping(headers)
    row: dict[str, Any] = {
        canonical: values[index] if index < len(values) else ""
        for canonical, index in mapping.items()
    }
    row["duplicate_key"] = infer_duplicate_key(row)
    row["__sheet_name"] = sheet_name
    row["__row_number"] = row_number
    row["__raw_by_header"] = raw_by_header
    return row


def project_headers(existing_headers: list[str], row: dict[str, Any]) -> list[str]:
    headers = list(existing_headers)
    mapping = resolve_header_mapping(headers)

    if not headers:
        for field in PREFERRED_FIELD_ORDER:
            if row.get(field) not in (None, "", []):
                headers.append(preferred_header_for_field(field))
        if not headers:
            headers = [preferred_header_for_field("company"), preferred_header_for_field("role_title"), preferred_header_for_field("status")]
        return headers

    for field in PREFERRED_FIELD_ORDER:
        if field in mapping:
            continue
        if row.get(field) in (None, "", []):
            continue
        headers.append(preferred_header_for_field(field))

    return headers


def render_row_values(headers: list[str], row: dict[str, Any], existing_row: dict[str, Any] | None = None) -> list[Any]:
    raw_by_header = deepcopy(existing_row.get("__raw_by_header", {})) if existing_row else {}
    for header in headers:
        raw_by_header.setdefault(header, "")

    mapping = resolve_header_mapping(headers)
    for field, value in row.items():
        if field.startswith("__") or value in (None, "", []):
            continue
        header = headers[mapping[field]] if field in mapping else preferred_header_for_field(field)
        raw_by_header[header] = serialize_cell_value(value)

    return [raw_by_header.get(header, "") for header in headers]


def find_matching_row(rows: list[dict[str, Any]], candidate: dict[str, Any], match_strategy: str) -> dict[str, Any] | None:
    for row in rows:
        if rows_match(row, candidate, match_strategy=match_strategy):
            return row
    return None


def load_service_account_credentials():
    credentials_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE") or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    credentials_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

    from google.oauth2.service_account import Credentials

    if credentials_json:
        return Credentials.from_service_account_info(json.loads(credentials_json), scopes=GOOGLE_SHEETS_SCOPES)
    if credentials_file:
        return Credentials.from_service_account_file(credentials_file, scopes=GOOGLE_SHEETS_SCOPES)
    raise RuntimeError(
        "Google Sheets credentials are not configured. Set GOOGLE_SERVICE_ACCOUNT_FILE or GOOGLE_SERVICE_ACCOUNT_JSON."
    )


def build_sheets_service():
    from googleapiclient.discovery import build

    credentials = load_service_account_credentials()
    return build("sheets", "v4", credentials=credentials, cache_discovery=False)


def fetch_sheet_state(service: Any, spreadsheet_id: str) -> dict[str, Any]:
    metadata = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields="properties.title,sheets.properties",
    ).execute()

    sheet_names = [sheet["properties"]["title"] for sheet in metadata.get("sheets", [])]
    if not sheet_names:
        raise ValueError("The spreadsheet has no tabs to read.")

    values_response = service.spreadsheets().values().batchGet(
        spreadsheetId=spreadsheet_id,
        ranges=[sheet_range(name) for name in sheet_names],
        majorDimension="ROWS",
    ).execute()

    value_ranges = values_response.get("valueRanges", [])
    tabs: list[dict[str, Any]] = []
    for index, name in enumerate(sheet_names):
        values = value_ranges[index].get("values", []) if index < len(value_ranges) else []
        headers = values[0] if values else []
        body_rows = values[1:] if len(values) > 1 else []
        rows = [
            row_from_sheet_values(name, headers, row_values, row_number=row_index + 2)
            for row_index, row_values in enumerate(body_rows)
            if any(cell not in ("", None) for cell in row_values)
        ]
        tabs.append(
            {
                "name": name,
                "headers": headers,
                "rows": rows,
                "row_count": len(rows),
                "score": tab_score(headers, len(rows)),
            }
        )

    active_tab = choose_active_tab(tabs)
    return {
        "spreadsheet_title": metadata.get("properties", {}).get("title"),
        "tabs": tabs,
        "active_tab": active_tab,
    }


def read_tracker_sheet_impl(sheet_url: str) -> dict[str, Any]:
    try:
        spreadsheet_id = extract_spreadsheet_id(sheet_url)
        service = build_sheets_service()
        state = fetch_sheet_state(service, spreadsheet_id)
        active_tab = state["active_tab"]
        return {
            "implemented": True,
            "sheet_url": sheet_url,
            "spreadsheet_title": state.get("spreadsheet_title"),
            "active_tab": active_tab["name"],
            "headers": active_tab["headers"],
            "rows": active_tab["rows"],
            "tabs": [
                {
                    "name": tab["name"],
                    "headers": tab["headers"],
                    "row_count": tab["row_count"],
                    "score": tab["score"],
                }
                for tab in state["tabs"]
            ],
        }
    except Exception as exc:
        return {
            "implemented": False,
            "reason": str(exc),
            "sheet_url": sheet_url,
            "tabs": [],
            "rows": [],
        }


def update_headers_if_needed(service: Any, spreadsheet_id: str, sheet_name: str, headers: list[str], target_headers: list[str]) -> None:
    if headers == target_headers:
        return
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=sheet_range(sheet_name, "1:1"),
        valueInputOption="USER_ENTERED",
        body={"values": [target_headers]},
    ).execute()


def upsert_tracker_row_impl(
    sheet_url: str,
    row: dict[str, Any],
    duplicate_key: str,
    match_strategy: str,
) -> dict[str, Any]:
    prepared_row = dict(row)
    prepared_row["duplicate_key"] = duplicate_key or infer_duplicate_key(prepared_row)

    try:
        spreadsheet_id = extract_spreadsheet_id(sheet_url)
        service = build_sheets_service()
        state = fetch_sheet_state(service, spreadsheet_id)
        active_tab = state["active_tab"]
        sheet_name = active_tab["name"]
        existing_headers = active_tab["headers"]
        existing_rows = active_tab["rows"]

        existing_row = find_matching_row(existing_rows, prepared_row, match_strategy=match_strategy)
        merged_row = merge_tracker_rows(existing_row, prepared_row) if existing_row else prepared_row

        target_headers = project_headers(existing_headers, merged_row)
        update_headers_if_needed(service, spreadsheet_id, sheet_name, existing_headers, target_headers)
        row_values = render_row_values(target_headers, merged_row, existing_row=existing_row)

        values_resource = service.spreadsheets().values()
        if existing_row:
            row_number = existing_row["__row_number"]
            target_range = sheet_range(sheet_name, f"A{row_number}:{column_letter(len(target_headers))}{row_number}")
            values_resource.update(
                spreadsheetId=spreadsheet_id,
                range=target_range,
                valueInputOption="USER_ENTERED",
                body={"values": [row_values]},
            ).execute()
            return {
                "implemented": True,
                "status": "updated",
                "sheet_url": sheet_url,
                "sheet_name": sheet_name,
                "row_number": row_number,
                "row": merged_row,
                "match_strategy": match_strategy,
            }

        append_response = values_resource.append(
            spreadsheetId=spreadsheet_id,
            range=sheet_range(sheet_name),
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [row_values]},
        ).execute()
        updated_range = append_response.get("updates", {}).get("updatedRange", "")
        appended_row_number = None
        match = re.search(r"![A-Z]+(\d+):", updated_range)
        if match:
            appended_row_number = int(match.group(1))

        return {
            "implemented": True,
            "status": "inserted",
            "sheet_url": sheet_url,
            "sheet_name": sheet_name,
            "row_number": appended_row_number,
            "row": merged_row,
            "match_strategy": match_strategy,
        }
    except Exception as exc:
        return {
            "implemented": False,
            "status": "error",
            "reason": str(exc),
            "sheet_url": sheet_url,
            "row": prepared_row,
            "match_strategy": match_strategy,
        }


@function_tool
def read_tracker_sheet(sheet_url: str) -> dict[str, Any]:
    """Read the Google Sheets tracker workbook and return tabs, headers, and rows."""
    return read_tracker_sheet_impl(sheet_url)


@function_tool(strict_mode=False)
def upsert_tracker_row(
    sheet_url: str,
    row: dict[str, Any],
    duplicate_key: str,
    match_strategy: str,
) -> dict[str, Any]:
    """Insert or update a tracker row using duplicate detection."""
    return upsert_tracker_row_impl(
        sheet_url=sheet_url,
        row=row,
        duplicate_key=duplicate_key,
        match_strategy=match_strategy,
    )
