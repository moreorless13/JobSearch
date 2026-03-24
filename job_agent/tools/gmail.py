from __future__ import annotations

import base64
import json
import os
import re
from typing import Any

from agents import function_tool

from job_agent.tools.dedupe import normalize_text

GMAIL_READONLY_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
EMAIL_CLASSIFICATIONS = (
    ("Offer", ("offer", "compensation package")),
    ("Interview Request", ("interview", "availability", "schedule time")),
    ("Assessment Request", ("assessment", "take-home", "coding challenge")),
    ("Recruiter Outreach", ("recruiter", "would love to connect", "your background")),
    ("Application Confirmation", ("application received", "thanks for applying", "we received your application")),
    ("Follow-Up Needed", ("follow up", "circling back", "next steps")),
    ("Rejection", ("unfortunately", "not moving forward", "other candidates")),
    ("Informational / Marketing", ("job alert", "recommended jobs", "newsletter")),
)


def resolve_gmail_auth_mode() -> str:
    has_service_account = bool(os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON") or os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE"))
    has_delegated_user = bool(os.getenv("GMAIL_DELEGATED_USER"))
    has_token = bool(os.getenv("GMAIL_TOKEN_JSON") or os.getenv("GMAIL_TOKEN_FILE"))
    has_oauth_client = bool(os.getenv("GOOGLE_OAUTH_CLIENT_SECRET_JSON") or os.getenv("GOOGLE_OAUTH_CLIENT_SECRET_FILE"))

    if has_service_account and has_delegated_user:
        return "service_account"
    if has_token:
        return "oauth_token"
    if has_oauth_client:
        return "oauth_client"

    raise RuntimeError(
        "Gmail credentials are not configured. Use GOOGLE_SERVICE_ACCOUNT_* with GMAIL_DELEGATED_USER for "
        "Workspace domain-wide delegation, or configure GOOGLE_OAUTH_CLIENT_SECRET_* and optionally GMAIL_TOKEN_* "
        "for a standard Gmail OAuth flow."
    )


def load_gmail_credentials():
    auth_mode = resolve_gmail_auth_mode()

    if auth_mode == "service_account":
        from google.oauth2.service_account import Credentials as ServiceAccountCredentials

        delegated_user = os.environ["GMAIL_DELEGATED_USER"]
        credentials_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
        credentials_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")

        if credentials_json:
            credentials = ServiceAccountCredentials.from_service_account_info(
                json.loads(credentials_json),
                scopes=GMAIL_READONLY_SCOPES,
            )
        else:
            credentials = ServiceAccountCredentials.from_service_account_file(
                credentials_file,
                scopes=GMAIL_READONLY_SCOPES,
            )

        return credentials.with_subject(delegated_user)

    if auth_mode == "oauth_token":
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials as UserCredentials

        token_json = os.getenv("GMAIL_TOKEN_JSON")
        token_file = os.getenv("GMAIL_TOKEN_FILE")

        if token_json:
            credentials = UserCredentials.from_authorized_user_info(json.loads(token_json), GMAIL_READONLY_SCOPES)
        else:
            credentials = UserCredentials.from_authorized_user_file(token_file, GMAIL_READONLY_SCOPES)

        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            if token_file:
                with open(token_file, "w", encoding="utf-8") as handle:
                    handle.write(credentials.to_json())

        return credentials

    from google_auth_oauthlib.flow import InstalledAppFlow

    client_secret_json = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET_JSON")
    client_secret_file = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET_FILE")
    token_file = os.getenv("GMAIL_TOKEN_FILE", ".gmail_token.json")
    use_console = os.getenv("GMAIL_OAUTH_USE_CONSOLE", "").lower() in {"1", "true", "yes"}

    if client_secret_json:
        flow = InstalledAppFlow.from_client_config(json.loads(client_secret_json), GMAIL_READONLY_SCOPES)
    else:
        flow = InstalledAppFlow.from_client_secrets_file(client_secret_file, GMAIL_READONLY_SCOPES)

    credentials = flow.run_console() if use_console else flow.run_local_server(port=0)
    with open(token_file, "w", encoding="utf-8") as handle:
        handle.write(credentials.to_json())
    return credentials


def build_gmail_service():
    from googleapiclient.discovery import build

    credentials = load_gmail_credentials()
    return build("gmail", "v1", credentials=credentials, cache_discovery=False)


def extract_company_hint(email_from: str, email_body: str) -> str | None:
    domain_match = re.search(r"@([A-Za-z0-9.-]+\.[A-Za-z]{2,})", email_from)
    if domain_match:
        domain = domain_match.group(1).split(".")[0]
        if domain not in {"gmail", "googlemail", "mail", "notifications"}:
            return domain.replace("-", " ").title()

    for prefix in ("Company:", "Employer:", "Organization:"):
        if prefix in email_body:
            line = email_body.split(prefix, maxsplit=1)[1].splitlines()[0].strip()
            if line:
                return line
    return None


def classify_email_payload(email_subject: str, email_from: str, email_body: str) -> dict[str, Any]:
    normalized_subject = normalize_text(email_subject)
    normalized_body = normalize_text(email_body)
    combined = f"{normalized_subject} {normalized_body}"

    classification = "Unclear"
    for label, phrases in EMAIL_CLASSIFICATIONS:
        if any(normalize_text(phrase) in combined for phrase in phrases):
            classification = label
            break

    action = None
    if classification in {"Interview Request", "Assessment Request", "Follow-Up Needed", "Offer"}:
        action = "Respond promptly"
    elif classification == "Recruiter Outreach":
        action = "Review and decide whether to engage"

    deadline_match = re.search(r"\b(?:by|before)\s+([A-Z][a-z]+\s+\d{1,2})\b", email_subject + " " + email_body)
    deadline = deadline_match.group(1) if deadline_match else None

    return {
        "classification": classification,
        "company": extract_company_hint(email_from, email_body),
        "role_title": email_subject.strip() or None,
        "action": action,
        "deadline": deadline,
        "confidence": 0.75 if classification != "Unclear" else 0.35,
    }


def match_email_to_tracker_row_payload(
    classified_email: dict[str, Any],
    tracker_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    company = normalize_text(classified_email.get("company"))
    role_title = normalize_text(classified_email.get("role_title"))

    best_match = None
    best_score = -1

    for row in tracker_rows:
        score = 0
        if company and normalize_text(row.get("company")) == company:
            score += 2
        if role_title and role_title in normalize_text(row.get("role_title")):
            score += 1

        if score > best_score:
            best_match = row
            best_score = score

    return {
        "matched": bool(best_match and best_score > 0),
        "confidence": 0.8 if best_score >= 2 else 0.55 if best_score == 1 else 0.2,
        "row": best_match,
    }


def decode_gmail_body(data: str | None) -> str:
    if not data:
        return ""

    missing_padding = len(data) % 4
    if missing_padding:
        data += "=" * (4 - missing_padding)

    try:
        return base64.urlsafe_b64decode(data.encode("utf-8")).decode("utf-8", errors="ignore")
    except Exception:
        return ""


def extract_message_body(payload: dict[str, Any] | None) -> str:
    if not payload:
        return ""

    mime_type = payload.get("mimeType")
    body = payload.get("body", {})
    if mime_type == "text/plain":
        return decode_gmail_body(body.get("data"))

    parts = payload.get("parts") or []
    plain_parts: list[str] = []
    fallback_parts: list[str] = []
    for part in parts:
        part_text = extract_message_body(part)
        if not part_text:
            continue
        if part.get("mimeType") == "text/plain":
            plain_parts.append(part_text)
        else:
            fallback_parts.append(part_text)

    if plain_parts:
        return "\n".join(part for part in plain_parts if part).strip()

    if fallback_parts:
        return "\n".join(part for part in fallback_parts if part).strip()

    return decode_gmail_body(body.get("data"))


def headers_to_map(payload: dict[str, Any] | None) -> dict[str, str]:
    headers = (payload or {}).get("headers") or []
    return {
        header.get("name", "").lower(): header.get("value", "")
        for header in headers
        if header.get("name")
    }


def fetch_message_summaries(service: Any, queries: list[str], max_results: int) -> list[dict[str, Any]]:
    users_resource = service.users()
    messages_resource = users_resource.messages()

    message_order: list[str] = []
    seen_ids: set[str] = set()

    for query in queries:
        response = messages_resource.list(userId="me", q=query, maxResults=max_results).execute()
        for message in response.get("messages", []):
            message_id = message.get("id")
            if not message_id or message_id in seen_ids:
                continue
            seen_ids.add(message_id)
            message_order.append(message_id)
            if len(message_order) >= max_results:
                break
        if len(message_order) >= max_results:
            break

    summaries: list[dict[str, Any]] = []
    for message_id in message_order:
        raw_message = messages_resource.get(
            userId="me",
            id=message_id,
            format="full",
            metadataHeaders=["Subject", "From", "Date"],
        ).execute()
        payload = raw_message.get("payload") or {}
        headers = headers_to_map(payload)
        body = extract_message_body(payload).strip() or raw_message.get("snippet", "")
        summaries.append(
            {
                "id": raw_message.get("id"),
                "thread_id": raw_message.get("threadId"),
                "subject": headers.get("subject", ""),
                "from": headers.get("from", ""),
                "date": headers.get("date", ""),
                "body": body,
                "snippet": raw_message.get("snippet", ""),
                "label_ids": raw_message.get("labelIds", []),
                "internal_date": raw_message.get("internalDate"),
            }
        )

    summaries.sort(key=lambda message: int(message.get("internal_date") or 0), reverse=True)
    return summaries


def search_gmail_job_updates_impl(queries: list[str], max_results: int) -> dict[str, Any]:
    request = {
        "queries": queries,
        "max_results": max_results,
    }

    try:
        service = build_gmail_service()
        messages = fetch_message_summaries(service, queries=queries, max_results=max_results)
        return {
            "messages": messages,
            "implemented": True,
            "request": request,
        }
    except Exception as exc:
        return {
            "messages": [],
            "implemented": False,
            "reason": f"search_gmail_job_updates failed: {exc}",
            "request": request,
        }


@function_tool
def search_gmail_job_updates(queries: list[str], max_results: int) -> dict[str, Any]:
    """Search Gmail for job-related emails and return structured message summaries."""
    return search_gmail_job_updates_impl(queries, max_results)


@function_tool
def classify_job_email(email_subject: str, email_from: str, email_body: str) -> dict[str, Any]:
    """Classify a job-related email and extract company, role, action, and deadline."""
    return classify_email_payload(email_subject, email_from, email_body)


@function_tool(strict_mode=False)
def match_email_to_tracker(
    classified_email: dict[str, Any],
    tracker_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Match a classified email to an existing tracker row."""
    return match_email_to_tracker_row_payload(classified_email, tracker_rows)
