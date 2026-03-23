from __future__ import annotations

import os
from typing import Any

from job_agent.models import GmailUpdate, JobRecord, ReviewItem, SummaryCounts, TrackerUpdate, WorkflowOutput
from job_agent.tools.gmail import classify_email_payload, match_email_to_tracker_row_payload, search_gmail_job_updates_impl
from job_agent.tools.jobs import score_job_fit_impl, search_jobs_impl
from job_agent.tools.sheets import read_tracker_sheet_impl, upsert_tracker_row_impl

DEFAULT_SEARCH_SOURCES = [
    "linkedin",
    "indeed",
    "ziprecruiter",
    "greenhouse",
    "lever",
    "workday",
    "ashby",
    "smartrecruiters",
    "google_jobs",
    "company_sites",
]

DEFAULT_GMAIL_QUERIES = [
    "from:(greenhouse.io OR lever.co OR myworkdayjobs.com OR smartrecruiters.com) newer_than:30d",
    'subject:("interview" OR "application" OR "offer" OR "assessment" OR "recruiter") newer_than:30d',
]
MIN_TRACKER_SYNC_FIT_SCORE = 60


def dedupe_list(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value not in seen:
            deduped.append(value)
            seen.add(value)
    return deduped


def build_search_keywords(candidate_profile: dict[str, Any]) -> list[str]:
    seed_keywords = [
        *candidate_profile.get("target_roles", []),
        "API",
        "integrations",
        "payments",
        "fintech",
        "implementation",
        *candidate_profile.get("keywords", []),
    ]
    return dedupe_list([keyword for keyword in seed_keywords if keyword])


def format_salary(job: dict[str, Any]) -> str | None:
    salary_min = job.get("salary_min")
    salary_max = job.get("salary_max")
    currency = job.get("salary_currency") or "USD"

    if salary_min is None and salary_max is None:
        return None

    if currency == "USD":
        if salary_min is not None and salary_max is not None:
            return f"${salary_min:,} - ${salary_max:,}"
        if salary_min is not None:
            return f"${salary_min:,}+"
        return f"Up to ${salary_max:,}"

    if salary_min is not None and salary_max is not None:
        return f"{salary_min:,} - {salary_max:,} {currency}"
    if salary_min is not None:
        return f"{salary_min:,}+ {currency}"
    return f"Up to {salary_max:,} {currency}"


def combine_reason(*parts: str | None) -> str | None:
    values = [part.strip() for part in parts if part and part.strip()]
    if not values:
        return None
    return " | ".join(values)


def build_job_record(job: dict[str, Any], candidate_profile: dict[str, Any]) -> JobRecord:
    fit = score_job_fit_impl(job, candidate_profile)
    salary = format_salary(job)
    reason = combine_reason(
        job.get("reason"),
        fit.get("reason"),
        "salary not listed" if salary is None else None,
    )
    return JobRecord(
        company=job.get("company"),
        role_title=job.get("role_title"),
        location=job.get("location"),
        source=job.get("source"),
        posting_url=job.get("posting_url"),
        careers_url=job.get("careers_url"),
        salary=salary,
        remote_or_local=job.get("remote_or_local", "unknown"),
        fit_score=fit.get("fit_score"),
        match_summary=fit.get("fit_band"),
        duplicate_key=job.get("duplicate_key"),
        reason=reason,
    )


def build_tracker_row_from_job(job: JobRecord) -> dict[str, Any]:
    return {
        "company": job.company,
        "role_title": job.role_title,
        "location": job.location,
        "source": job.source,
        "posting_url": job.posting_url,
        "careers_url": job.careers_url,
        "status": "New",
        "fit_score": job.fit_score,
        "match_summary": job.match_summary,
        "salary": job.salary,
        "remote_or_local": job.remote_or_local,
        "notes": job.reason,
        "duplicate_key": job.duplicate_key,
        "priority": "high" if (job.fit_score or 0) >= 85 else "normal",
    }


def append_review(output: WorkflowOutput, *, kind: str, reason: str, details: str | None = None) -> None:
    output.needs_review.append(ReviewItem(kind=kind, reason=reason, details=details))


def sync_jobs_to_tracker(output: WorkflowOutput, jobs: list[JobRecord], candidate_profile: dict[str, Any]) -> None:
    jobs_to_sync = [
        job for job in jobs if (job.fit_score or 0) >= MIN_TRACKER_SYNC_FIT_SCORE and job.match_summary != "ignore"
    ]

    if not jobs_to_sync:
        return

    sheet_url = candidate_profile["sheet_url"]
    updated_count = 0
    tracker_stub_reported = False

    for job in jobs_to_sync:
        tracker_row = build_tracker_row_from_job(job)
        result = upsert_tracker_row_impl(
            sheet_url=sheet_url,
            row=tracker_row,
            duplicate_key=job.duplicate_key or "",
            match_strategy="hybrid",
        )

        if not result.get("implemented", True):
            if not tracker_stub_reported:
                append_review(
                    output,
                    kind="tracker_unavailable",
                    reason="Tracker updates could not be applied because Google Sheets is not configured or returned an error.",
                    details=result.get("reason"),
                )
                tracker_stub_reported = True
            continue

        updated_count += 1
        persisted_row = result.get("row") or {}
        output.tracker_updates.append(
            TrackerUpdate(
                company=job.company,
                role_title=job.role_title,
                status=str(persisted_row.get("status") or tracker_row.get("status") or ""),
                duplicate_key=job.duplicate_key,
                update_type=str(result.get("status", "updated")),
                notes=job.reason,
            )
        )

    output.summary.tracker_rows_updated += updated_count


def run_jobs_workflow(candidate_profile: dict[str, Any]) -> WorkflowOutput:
    output = WorkflowOutput()
    keywords = build_search_keywords(candidate_profile)
    search_result = search_jobs_impl(
        keywords=keywords,
        location_mode="both",
        origin=candidate_profile["location_rules"]["origin"],
        radius_miles=int(candidate_profile["location_rules"]["radius_miles"]),
        salary_floor=int(candidate_profile["salary_floor"]),
        sources=DEFAULT_SEARCH_SOURCES,
    )

    summary = search_result.get("summary", {})
    output.summary.jobs_reviewed = int(summary.get("jobs_reviewed", 0))
    output.summary.duplicates_skipped = int(summary.get("duplicates_skipped", 0))

    if not search_result.get("implemented", False):
        append_review(
            output,
            kind="job_search_unavailable",
            reason="Job search could not be completed because the search tool failed or is unavailable.",
            details=search_result.get("reason"),
        )
        return output

    records = [build_job_record(job, candidate_profile) for job in search_result.get("jobs", [])]
    records.sort(key=lambda job: job.fit_score or 0, reverse=True)
    output.new_jobs.extend(records)
    output.summary.jobs_added = len(records)

    for note in search_result.get("notes", []):
        append_review(output, kind="job_search_note", reason="Job search returned a note that may need review.", details=note)

    sync_jobs_to_tracker(output, records, candidate_profile)
    return output


def run_gmail_workflow(candidate_profile: dict[str, Any]) -> WorkflowOutput:
    output = WorkflowOutput()
    max_results = int(os.getenv("GMAIL_SEARCH_MAX_RESULTS", "25"))
    gmail_result = search_gmail_job_updates_impl(queries=DEFAULT_GMAIL_QUERIES, max_results=max_results)

    if not gmail_result.get("implemented", False):
        append_review(
            output,
            kind="gmail_unavailable",
            reason="Gmail updates could not be processed because the Gmail integration is still stubbed.",
            details=gmail_result.get("reason"),
        )
        return output

    sheet_result = read_tracker_sheet_impl(candidate_profile["sheet_url"])
    tracker_rows = sheet_result.get("rows", []) if sheet_result.get("implemented", False) else []
    if not sheet_result.get("implemented", False):
        append_review(
            output,
            kind="tracker_unavailable",
            reason="Tracker matching could not be completed because Google Sheets is not configured or returned an error.",
            details=sheet_result.get("reason"),
        )

    processed_count = 0
    for message in gmail_result.get("messages", []):
        classified = classify_email_payload(
            email_subject=message.get("subject", ""),
            email_from=message.get("from", ""),
            email_body=message.get("body", ""),
        )
        matched = match_email_to_tracker_row_payload(classified_email=classified, tracker_rows=tracker_rows)
        output.gmail_updates.append(
            GmailUpdate(
                classification=classified["classification"],
                company=classified.get("company"),
                role_title=classified.get("role_title"),
                deadline=classified.get("deadline"),
                action=classified.get("action"),
                matched_duplicate_key=(matched.get("row") or {}).get("duplicate_key"),
                confidence=matched.get("confidence"),
            )
        )
        processed_count += 1

    output.summary.gmail_updates_processed = processed_count
    return output


def merge_workflow_outputs(*outputs: WorkflowOutput) -> WorkflowOutput:
    merged = WorkflowOutput()
    for output in outputs:
        merged.summary.jobs_reviewed += output.summary.jobs_reviewed
        merged.summary.jobs_added += output.summary.jobs_added
        merged.summary.duplicates_skipped += output.summary.duplicates_skipped
        merged.summary.gmail_updates_processed += output.summary.gmail_updates_processed
        merged.summary.tracker_rows_updated += output.summary.tracker_rows_updated
        merged.new_jobs.extend(output.new_jobs)
        merged.gmail_updates.extend(output.gmail_updates)
        merged.tracker_updates.extend(output.tracker_updates)
        merged.needs_review.extend(output.needs_review)
    return merged


def run_preset_workflow(workflow: str, candidate_profile: dict[str, Any]) -> WorkflowOutput:
    if workflow == "jobs":
        return run_jobs_workflow(candidate_profile)
    if workflow == "gmail":
        return run_gmail_workflow(candidate_profile)
    if workflow == "daily":
        return merge_workflow_outputs(
            run_jobs_workflow(candidate_profile),
            run_gmail_workflow(candidate_profile),
        )
    raise ValueError(f"Unsupported workflow preset: {workflow}")
