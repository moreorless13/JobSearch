from __future__ import annotations

import re
from typing import Any

from agents import function_tool

from job_agent.tools.dedupe import normalize_text

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


def search_gmail_job_updates_impl(queries: list[str], max_results: int) -> dict[str, Any]:
    return {
        "messages": [],
        "implemented": False,
        "reason": "search_gmail_job_updates is a scaffold stub. Plug Gmail access in here.",
        "request": {
            "queries": queries,
            "max_results": max_results,
        },
    }


@function_tool
def search_gmail_job_updates(queries: list[str], max_results: int) -> dict[str, Any]:
    """Search Gmail for job-related emails and return structured message summaries.

    This starter implementation is a stub. Replace it with Gmail API logic.
    """
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
