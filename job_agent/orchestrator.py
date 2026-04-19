from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any

from job_agent.docs.service import DocumentationService
from job_agent.events import WorkflowEvent
from job_agent.models import CoverLetterArtifact, GmailUpdate, JobRecord, QAResult, ResumeArtifact, ReviewItem, TrackerUpdate, WorkflowOutput
from job_agent.qa import QAEventDispatcher
from job_agent.resume import generate_cover_letter_artifact_impl, generate_resume_artifact_impl
from job_agent.runtime import (
    AVAILABILITY_RECHECK_DAYS,
    DEFAULT_GMAIL_QUERIES,
    DEFAULT_SEARCH_SOURCES,
    FOLLOW_UP_DAYS,
    STALE_POSTING_DAYS,
)
from job_agent.state import (
    DecisionAction,
    DecisionRecord,
    FollowUpTask,
    GoalState,
    OutcomeEvent,
    PlanTask,
    RedisStateStore,
    StrategySnapshot,
    build_default_strategy_snapshot,
    build_follow_up_task,
    build_plan_run,
    clamp_weight,
    isoformat,
    role_slug,
    utc_now,
)
from job_agent.tools.dedupe import build_duplicate_key, normalize_text
from job_agent.tools.gmail import classify_email_payload, match_email_to_tracker_row_payload, search_gmail_job_updates_impl
from job_agent.tools.jobs import score_job_fit_impl, search_jobs_impl, verify_job_availability_impl
from job_agent.tools.sheets import find_matching_row, read_tracker_sheet_impl, upsert_tracker_row_impl

POSITIVE_SIGNAL_CLASSIFICATIONS = {"Recruiter Outreach", "Interview Request", "Assessment Request", "Offer"}
IMMEDIATE_REVIEW_CLASSIFICATIONS = {"Interview Request", "Assessment Request", "Offer"}
REFLECTION_LOOKBACK_DAYS = 14
AVAILABILITY_RECHECK_SKIP_STATUSES = {
    "applied",
    "assessment requested",
    "interview requested",
    "interviewing",
    "offer",
    "rejected",
    "withdrawn",
    "unavailable",
    "link invalid",
}
AVAILABILITY_STATUS_UPDATE_STATUSES = {"", "new", "needs review", "tracked"}
TRACKER_MATERIAL_ACTIONS = {"prioritize", "track", "queue_review"}


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


def should_tailor_resume(*, fit_score: Any, next_steps: str | None) -> str:
    _ = fit_score, next_steps
    return "yes"


def append_review(output: WorkflowOutput, *, kind: str, reason: str, details: str | None = None, company: str | None = None, role_title: str | None = None) -> None:
    output.needs_review.append(
        ReviewItem(
            kind=kind,
            company=company,
            role_title=role_title,
            reason=reason,
            details=details,
        )
    )


def build_job_record(job: dict[str, Any], fit: dict[str, Any], decision_reason: str | None = None) -> JobRecord:
    salary = format_salary(job)
    reason = combine_reason(
        job.get("reason"),
        fit.get("reason"),
        decision_reason,
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
        fit_score=optional_int(fit.get("fit_score")),
        match_summary=fit.get("fit_band"),
        required_experience_years=optional_float(fit.get("required_experience_years")),
        candidate_experience_years=optional_float(fit.get("candidate_experience_years")),
        experience_gap_years=optional_float(fit.get("experience_gap_years")),
        checked_url=job.get("checked_url"),
        link_check_status=job.get("link_check_status"),
        link_checked_at=job.get("link_checked_at"),
        availability_status=job.get("availability_status"),
        availability_checked_at=job.get("availability_checked_at"),
        availability_next_check_at=job.get("availability_next_check_at"),
        availability_notes=job.get("availability_notes"),
        duplicate_key=job.get("duplicate_key"),
        reason=reason,
    )


def build_tracker_row_from_job(
    job: JobRecord,
    *,
    status: str = "New",
    next_steps: str | None = None,
    priority: str | None = None,
    resume_version: str | None = None,
    cover_letter_version: str | None = None,
) -> dict[str, Any]:
    return {
        "company": job.company,
        "role_title": job.role_title,
        "location": job.location,
        "source": job.source,
        "posting_url": job.posting_url,
        "careers_url": job.careers_url,
        "status": status,
        "resume_version": resume_version,
        "cover_letter_version": cover_letter_version,
        "required_experience_years": job.required_experience_years,
        "candidate_experience_years": job.candidate_experience_years,
        "experience_gap_years": job.experience_gap_years,
        "checked_url": job.checked_url,
        "link_check_status": job.link_check_status,
        "link_checked_at": job.link_checked_at,
        "availability_status": job.availability_status,
        "availability_checked_at": job.availability_checked_at,
        "availability_next_check_at": job.availability_next_check_at,
        "availability_notes": job.availability_notes,
        "fit_score": job.fit_score,
        "match_summary": job.match_summary,
        "salary": job.salary,
        "remote_or_local": job.remote_or_local,
        "notes": job.reason,
        "duplicate_key": job.duplicate_key,
        "priority": priority or ("high" if (job.fit_score or 0) >= 85 else "normal"),
        "tailor_resume": should_tailor_resume(fit_score=job.fit_score, next_steps=next_steps),
        "next_steps": next_steps,
    }


def normalize_remote_or_local_value(value: Any) -> str:
    normalized = normalize_text(value)
    if normalized in {"remote", "local", "hybrid"}:
        return normalized
    return "unknown"


def blank_to_none(value: Any) -> Any:
    if value is None or value == []:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return value


def optional_int(value: Any) -> int | None:
    normalized = blank_to_none(value)
    if normalized is None:
        return None
    try:
        return int(normalized)
    except (TypeError, ValueError):
        try:
            return int(float(normalized))
        except (TypeError, ValueError):
            return None


def optional_float(value: Any) -> float | None:
    normalized = blank_to_none(value)
    if normalized is None:
        return None
    try:
        return float(normalized)
    except (TypeError, ValueError):
        return None


def tracker_row_to_job_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "company": row.get("company"),
        "role_title": row.get("role_title"),
        "location": row.get("location"),
        "source": row.get("source"),
        "posting_url": row.get("posting_url"),
        "careers_url": row.get("careers_url"),
        "remote_or_local": normalize_remote_or_local_value(row.get("remote_or_local")),
        "salary": row.get("salary"),
        "description": row.get("notes") or row.get("match_summary"),
        "fit_score": optional_int(row.get("fit_score")),
        "match_summary": row.get("match_summary"),
        "required_experience_years": optional_float(row.get("required_experience_years")),
        "candidate_experience_years": optional_float(row.get("candidate_experience_years")),
        "experience_gap_years": optional_float(row.get("experience_gap_years")),
        "checked_url": row.get("checked_url"),
        "link_check_status": row.get("link_check_status"),
        "link_checked_at": row.get("link_checked_at"),
        "availability_status": row.get("availability_status"),
        "availability_checked_at": row.get("availability_checked_at"),
        "availability_next_check_at": row.get("availability_next_check_at"),
        "availability_notes": row.get("availability_notes"),
        "duplicate_key": row.get("duplicate_key") or build_duplicate_key(row.get("company"), row.get("role_title"), row.get("location")),
    }


def job_record_from_tracker_row(row: dict[str, Any]) -> JobRecord:
    return JobRecord(
        company=row.get("company"),
        role_title=row.get("role_title"),
        location=row.get("location"),
        source=row.get("source"),
        posting_url=row.get("posting_url"),
        careers_url=row.get("careers_url"),
        salary=row.get("salary"),
        remote_or_local=normalize_remote_or_local_value(row.get("remote_or_local")),
        fit_score=optional_int(row.get("fit_score")),
        match_summary=row.get("match_summary"),
        required_experience_years=optional_float(row.get("required_experience_years")),
        candidate_experience_years=optional_float(row.get("candidate_experience_years")),
        experience_gap_years=optional_float(row.get("experience_gap_years")),
        checked_url=row.get("checked_url"),
        link_check_status=row.get("link_check_status"),
        link_checked_at=row.get("link_checked_at"),
        availability_status=row.get("availability_status"),
        availability_checked_at=row.get("availability_checked_at"),
        availability_next_check_at=row.get("availability_next_check_at"),
        availability_notes=row.get("availability_notes"),
        duplicate_key=row.get("duplicate_key") or build_duplicate_key(row.get("company"), row.get("role_title"), row.get("location")),
        reason=row.get("notes"),
    )


def gmail_status_for_classification(classification: str, existing_status: str | None = None) -> str:
    status_map = {
        "Application Confirmation": "Applied",
        "Recruiter Outreach": "Recruiter Outreach",
        "Interview Request": "Interview Requested",
        "Assessment Request": "Assessment Requested",
        "Follow-Up Needed": "Follow-Up Needed",
        "Rejection": "Rejected",
        "Offer": "Offer",
    }
    if classification in status_map:
        return status_map[classification]
    if existing_status:
        return existing_status
    return "Needs Review"


def build_tracker_row_from_email_update(
    *,
    classified_email: dict[str, Any],
    matched_row: dict[str, Any] | None,
    message: dict[str, Any],
) -> dict[str, Any]:
    company = classified_email.get("company") or (matched_row or {}).get("company")
    role_title = classified_email.get("role_title") or (matched_row or {}).get("role_title")
    location = (matched_row or {}).get("location")
    existing_status = (matched_row or {}).get("status")
    status = gmail_status_for_classification(classified_email["classification"], existing_status=existing_status)
    next_steps = classified_email.get("action")
    note_lines = [
        f"Email update: {classified_email['classification']}",
        f"From: {message.get('from', '').strip()}",
        f"Subject: {message.get('subject', '').strip()}",
    ]
    if message.get("date"):
        note_lines.append(f"Date: {message['date']}")
    if message.get("snippet"):
        note_lines.append(f"Snippet: {message['snippet']}")

    return {
        "company": company,
        "role_title": role_title,
        "location": location,
        "status": status,
        "email_update_type": classified_email["classification"],
        "last_email_update": message.get("date") or message.get("subject"),
        "tailor_resume": should_tailor_resume(
            fit_score=(matched_row or {}).get("fit_score"),
            next_steps=next_steps,
        ),
        "next_steps": next_steps,
        "notes": "\n".join(line for line in note_lines if line),
        "duplicate_key": (matched_row or {}).get("duplicate_key") or build_duplicate_key(company, role_title, location),
    }


def email_duplicate_key(classified_email: dict[str, Any], matched_row: dict[str, Any]) -> str:
    return matched_row.get("duplicate_key") or build_duplicate_key(
        classified_email.get("company"),
        classified_email.get("role_title"),
        matched_row.get("location"),
    )


def build_gmail_update_record(classified_email: dict[str, Any], matched: dict[str, Any]) -> GmailUpdate:
    matched_row = matched.get("row") or {}
    return GmailUpdate(
        classification=classified_email["classification"],
        company=classified_email.get("company"),
        role_title=classified_email.get("role_title"),
        deadline=classified_email.get("deadline"),
        action=classified_email.get("action"),
        matched_duplicate_key=matched_row.get("duplicate_key"),
        confidence=matched.get("confidence"),
    )


def build_outcome_from_email(
    *,
    classified_email: dict[str, Any],
    matched_row: dict[str, Any],
    message: dict[str, Any],
    duplicate_key: str,
) -> OutcomeEvent:
    company = classified_email.get("company") or matched_row.get("company")
    role_title = classified_email.get("role_title") or matched_row.get("role_title")
    return OutcomeEvent(
        event_id=str(uuid.uuid4()),
        timestamp=isoformat(parse_date(message.get("date")) or utc_now()),
        duplicate_key=duplicate_key,
        company=company,
        role_title=role_title,
        role_slug=role_slug(role_title),
        source=matched_row.get("source"),
        industry=matched_row.get("industry"),
        event_type=classification_to_event_type(classified_email["classification"]),
        metadata={"subject": message.get("subject"), "from": message.get("from")},
    )


def parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    candidates = [value]
    if "T" not in value and len(value) == 10:
        candidates.append(f"{value}T00:00:00+00:00")
    for candidate in candidates:
        try:
            return datetime.fromisoformat(candidate.replace("Z", "+00:00")).astimezone(UTC)
        except ValueError:
            continue
    try:
        return parsedate_to_datetime(value).astimezone(UTC)
    except (TypeError, ValueError, IndexError):
        return None


def infer_posting_age_days(job: dict[str, Any]) -> int | None:
    age = job.get("posting_age_days")
    if age is not None:
        try:
            return max(int(age), 0)
        except (TypeError, ValueError):
            return None
    posted_at = parse_date(job.get("posted_at"))
    if posted_at is None:
        return None
    return max((utc_now() - posted_at).days, 0)


def tracker_row_is_due_for_availability_check(row: dict[str, Any], *, reference: datetime | None = None) -> bool:
    if not row.get("posting_url") and not row.get("careers_url"):
        return False

    normalized_status = normalize_text(row.get("status"))
    if normalized_status in AVAILABILITY_RECHECK_SKIP_STATUSES:
        return False

    current = reference or utc_now()
    checked_at = parse_date(row.get("availability_checked_at") or row.get("link_checked_at"))
    if checked_at is None:
        return True
    return checked_at <= current - timedelta(days=AVAILABILITY_RECHECK_DAYS)


def tracker_rows_due_for_availability_check(
    tracker_rows: list[dict[str, Any]],
    *,
    reference: datetime | None = None,
) -> list[dict[str, Any]]:
    return [row for row in tracker_rows if tracker_row_is_due_for_availability_check(row, reference=reference)]


def tracker_status_from_availability(row: dict[str, Any], check: dict[str, Any]) -> str | None:
    normalized_status = normalize_text(row.get("status"))
    if normalized_status not in AVAILABILITY_STATUS_UPDATE_STATUSES:
        return None
    if check.get("availability_status") == "unavailable":
        return "Unavailable"
    if check.get("link_status") == "invalid":
        return "Link Invalid"
    return None


def build_tracker_row_from_availability_check(row: dict[str, Any], check: dict[str, Any]) -> dict[str, Any]:
    note_lines = [
        f"Availability check: link={check.get('link_status')}, availability={check.get('availability_status')}",
    ]
    if check.get("reason"):
        note_lines.append(str(check["reason"]))

    return {
        "company": row.get("company"),
        "role_title": row.get("role_title"),
        "location": row.get("location"),
        "posting_url": row.get("posting_url"),
        "careers_url": row.get("careers_url"),
        "checked_url": check.get("checked_url"),
        "link_check_status": check.get("link_status"),
        "link_checked_at": check.get("checked_at"),
        "availability_status": check.get("availability_status"),
        "availability_checked_at": check.get("checked_at"),
        "availability_next_check_at": check.get("next_check_at"),
        "availability_notes": check.get("reason"),
        "status": tracker_status_from_availability(row, check),
        "notes": "\n".join(note_lines),
        "duplicate_key": row.get("duplicate_key") or build_duplicate_key(row.get("company"), row.get("role_title"), row.get("location")),
    }


def source_bonus(source: str | None) -> int:
    bonuses = {
        "company_sites": 5,
        "greenhouse": 4,
        "lever": 4,
        "ashby": 4,
        "workday": 4,
        "smartrecruiters": 3,
        "linkedin": 1,
        "indeed": 0,
        "google_jobs": 0,
        "ziprecruiter": -1,
    }
    return bonuses.get(str(source or "").lower(), 0)


def strategy_bonus(job: dict[str, Any], snapshot: StrategySnapshot, candidate_profile: dict[str, Any]) -> int:
    bonus = snapshot.source_weights.get(str(job.get("source") or "").lower(), 0.0)
    title = normalize_text(job.get("role_title"))
    for key, weight in snapshot.role_weights.items():
        if key and key in title:
            bonus += weight
            break
    industry = normalize_text(job.get("industry"))
    if industry:
        bonus += snapshot.industry_weights.get(industry, 0.0)
    company_priorities = {
        normalize_text(company): float(weight)
        for company, weight in (candidate_profile.get("company_priorities") or {}).items()
    }
    company_key = normalize_text(job.get("company"))
    if company_key:
        bonus += company_priorities.get(company_key, 0.0)
    return int(round(bonus * 10))


def freshness_bonus(job: dict[str, Any], base_fit_score: int) -> tuple[int, bool]:
    age_days = infer_posting_age_days(job)
    if age_days is None:
        return 0, False
    if age_days > STALE_POSTING_DAYS and base_fit_score < 90:
        return 0, True
    if age_days <= 7:
        return 10, False
    if age_days <= 14:
        return 5, False
    return 0, False


def effort_penalty(job: dict[str, Any]) -> int:
    if not job.get("posting_url") or not job.get("company") or not job.get("role_title"):
        return 5
    return 0


def resolve_decision_thresholds(candidate_profile: dict[str, Any]) -> dict[str, int]:
    defaults = {
        "prioritize": 85,
        "track": 70,
        "queue_review": 60,
        "stale_days": STALE_POSTING_DAYS,
    }
    configured = candidate_profile.get("decision_thresholds") or {}
    return {
        "prioritize": int(configured.get("prioritize", defaults["prioritize"])),
        "track": int(configured.get("track", defaults["track"])),
        "queue_review": int(configured.get("queue_review", defaults["queue_review"])),
        "stale_days": int(configured.get("stale_days", defaults["stale_days"])),
    }


def build_decision_record(
    *,
    workflow: str,
    job: dict[str, Any],
    fit: dict[str, Any],
    action: DecisionAction,
    final_score: int,
    freshness_points: int,
    source_points: int,
    strategy_points: int,
    effort_points: int,
    rationale: str,
) -> DecisionRecord:
    return DecisionRecord(
        decision_id=str(uuid.uuid4()),
        timestamp=isoformat(utc_now()),
        workflow=workflow,
        duplicate_key=job.get("duplicate_key"),
        company=job.get("company"),
        role_title=job.get("role_title"),
        role_slug=role_slug(job.get("role_title")),
        industry=job.get("industry"),
        source=job.get("source"),
        action=action,
        final_score=final_score,
        base_fit_score=optional_int(fit.get("fit_score")) or 0,
        freshness_bonus=freshness_points,
        source_bonus=source_points,
        strategy_bonus=strategy_points,
        effort_penalty=effort_points,
        rationale=rationale,
        metadata={
            "posting_url": job.get("posting_url"),
            "posting_age_days": infer_posting_age_days(job),
            "salary_min": job.get("salary_min"),
        },
    )


def decide_job_action(
    job: dict[str, Any],
    fit: dict[str, Any],
    candidate_profile: dict[str, Any],
    snapshot: StrategySnapshot,
) -> tuple[DecisionAction, int, str, int, int, int, int]:
    thresholds = resolve_decision_thresholds(candidate_profile)
    base_fit_score = optional_int(fit.get("fit_score")) or 0

    salary_min = job.get("salary_min")
    normalized_salary_min = optional_int(salary_min)
    if normalized_salary_min is not None and normalized_salary_min < int(candidate_profile["salary_floor"]):
        return "skip", base_fit_score, "Listed salary is below the configured floor.", 0, 0, 0, 0

    freshness_points, stale_skip = freshness_bonus(job, base_fit_score)
    if stale_skip:
        return "skip", base_fit_score, "Posting appears stale and fit is not strong enough to keep.", 0, 0, 0, 0

    source_points = source_bonus(job.get("source"))
    strategy_points = strategy_bonus(job, snapshot, candidate_profile)
    effort_points = effort_penalty(job)
    final_score = max(min(base_fit_score + freshness_points + source_points + strategy_points - effort_points, 100), 0)

    if not job.get("company") or not job.get("role_title"):
        return "queue_review", final_score, "Critical job fields are missing and need manual review.", freshness_points, source_points, strategy_points, effort_points
    if final_score >= thresholds["prioritize"]:
        return "prioritize", final_score, "High-priority fit after freshness, source, and strategy weighting.", freshness_points, source_points, strategy_points, effort_points
    if final_score >= thresholds["track"]:
        return "track", final_score, "Strong enough to track automatically.", freshness_points, source_points, strategy_points, effort_points
    if final_score >= thresholds["queue_review"]:
        return "queue_review", final_score, "Borderline fit kept for manual review.", freshness_points, source_points, strategy_points, effort_points
    return "skip", final_score, "Below the tracking threshold after deterministic scoring.", freshness_points, source_points, strategy_points, effort_points


def due_follow_up_datetime(reference: datetime | None = None) -> datetime:
    current = reference or utc_now()
    due = current
    business_days = 0
    while business_days < FOLLOW_UP_DAYS:
        due += timedelta(days=1)
        if due.weekday() < 5:
            business_days += 1
    return due


def tracker_due_follow_ups(tracker_rows: list[dict[str, Any]]) -> list[FollowUpTask]:
    tasks: list[FollowUpTask] = []
    positive_statuses = {"Interview Requested", "Assessment Requested", "Offer", "Rejected"}
    for row in tracker_rows:
        status = str(row.get("status") or "")
        if status != "Applied":
            continue
        if status in positive_statuses:
            continue
        applied_at = parse_date(row.get("applied_date"))
        if applied_at is None:
            continue
        due_at = due_follow_up_datetime(applied_at)
        if due_at > utc_now():
            continue
        tasks.append(
            build_follow_up_task(
                duplicate_key=row.get("duplicate_key"),
                company=row.get("company"),
                role_title=row.get("role_title"),
                due_at=due_at,
                reason="No positive signal received within 3 business days of application.",
            )
        )
    return tasks


def build_plan_tasks(workflow: str, goal_state: GoalState | None, due_follow_ups: list[FollowUpTask]) -> list[PlanTask]:
    tasks: list[PlanTask] = []
    if workflow in {"jobs", "daily"}:
        tasks.append(
            PlanTask(
                task_id=str(uuid.uuid4()),
                kind="search_jobs",
                priority=1.0,
                reason="Refresh the opportunity funnel with new roles aligned to the active objective.",
            )
        )
    if workflow in {"gmail", "daily"}:
        tasks.append(
            PlanTask(
                task_id=str(uuid.uuid4()),
                kind="scan_gmail",
                priority=0.9,
                reason="Capture recruiter, ATS, and interview signals from Gmail.",
            )
        )
    if workflow in {"reflect", "daily"}:
        tasks.append(
            PlanTask(
                task_id=str(uuid.uuid4()),
                kind="reflect",
                priority=0.8,
                reason="Re-rank subgoals and strategy weights based on recent outcomes.",
            )
        )
    for follow_up in due_follow_ups[:5]:
        tasks.append(
            PlanTask(
                task_id=follow_up.task_id,
                kind="follow_up_due",
                priority=1.1,
                reason=follow_up.reason,
                due_at=follow_up.due_at,
            )
        )
    if goal_state is not None:
        for subgoal in goal_state.subgoals[:3]:
            tasks.append(
                PlanTask(
                    task_id=str(uuid.uuid4()),
                    kind="subgoal_focus",
                    priority=subgoal.priority,
                    reason=f"Current subgoal focus: {subgoal.label}",
                )
            )
    tasks.sort(key=lambda task: task.priority, reverse=True)
    return tasks


def classification_to_event_type(classification: str) -> str:
    mapping = {
        "Application Confirmation": "application_confirmation",
        "Recruiter Outreach": "positive_signal",
        "Interview Request": "interview_request",
        "Assessment Request": "assessment_request",
        "Follow-Up Needed": "follow_up_needed",
        "Rejection": "rejection",
        "Offer": "offer",
        "Informational / Marketing": "marketing",
        "Unclear": "unclear",
    }
    return mapping.get(classification, "unclear")


def role_hits(title: str | None, keys: list[str]) -> list[str]:
    normalized_title = normalize_text(title)
    return [key for key in keys if key and key in normalized_title]


def reflect_strategy(
    *,
    candidate_profile: dict[str, Any],
    snapshot: StrategySnapshot,
    goal_state: GoalState | None,
    decisions: list[DecisionRecord],
    outcomes: list[OutcomeEvent],
    due_follow_ups: list[FollowUpTask],
) -> tuple[StrategySnapshot, GoalState | None]:
    positive_events = {"positive_signal", "interview_request", "assessment_request", "offer"}
    negative_events = {"rejection"}

    role_adjustments = dict(snapshot.role_weights)
    industry_adjustments = dict(snapshot.industry_weights)
    source_adjustments = dict(snapshot.source_weights)

    target_roles = [role_slug(role) for role in candidate_profile.get("target_roles", []) if role]
    role_outcomes = {key: {"decisions": 0, "positive": 0, "negative": 0} for key in target_roles}
    source_outcomes: dict[str, dict[str, int]] = {}
    industry_outcomes: dict[str, dict[str, int]] = {}

    for decision in decisions:
        for key in role_hits(decision.role_title, target_roles):
            role_outcomes.setdefault(key, {"decisions": 0, "positive": 0, "negative": 0})
            role_outcomes[key]["decisions"] += 1
        if decision.source:
            source_outcomes.setdefault(decision.source, {"decisions": 0, "positive": 0, "negative": 0})
            source_outcomes[decision.source]["decisions"] += 1
        if decision.industry:
            industry_key = normalize_text(decision.industry)
            industry_outcomes.setdefault(industry_key, {"decisions": 0, "positive": 0, "negative": 0})
            industry_outcomes[industry_key]["decisions"] += 1

    for outcome in outcomes:
        role_keys = role_hits(outcome.role_title, target_roles)
        for key in role_keys:
            role_outcomes.setdefault(key, {"decisions": 0, "positive": 0, "negative": 0})
            if outcome.event_type in positive_events:
                role_outcomes[key]["positive"] += 1
            elif outcome.event_type in negative_events:
                role_outcomes[key]["negative"] += 1
        if outcome.source:
            source_outcomes.setdefault(outcome.source, {"decisions": 0, "positive": 0, "negative": 0})
            if outcome.event_type in positive_events:
                source_outcomes[outcome.source]["positive"] += 1
            elif outcome.event_type in negative_events:
                source_outcomes[outcome.source]["negative"] += 1
        if outcome.industry:
            industry_key = normalize_text(outcome.industry)
            industry_outcomes.setdefault(industry_key, {"decisions": 0, "positive": 0, "negative": 0})
            if outcome.event_type in positive_events:
                industry_outcomes[industry_key]["positive"] += 1
            elif outcome.event_type in negative_events:
                industry_outcomes[industry_key]["negative"] += 1

    summary_parts: list[str] = []
    for key, counts in role_outcomes.items():
        delta = 0.0
        if counts["positive"] > 0:
            delta += 0.1
        if counts["negative"] > counts["positive"]:
            delta -= 0.1
        if counts["decisions"] >= 3 and counts["positive"] == 0:
            delta -= 0.1
        if delta:
            role_adjustments[key] = clamp_weight(role_adjustments.get(key, 0.0) + delta)
            summary_parts.append(f"role {key} {delta:+.1f}")

    for collection, adjustments, label in (
        (source_outcomes, source_adjustments, "source"),
        (industry_outcomes, industry_adjustments, "industry"),
    ):
        for key, counts in collection.items():
            delta = 0.0
            if counts["positive"] > 0:
                delta += 0.1
            if counts["negative"] > counts["positive"]:
                delta -= 0.1
            if counts["decisions"] >= 4 and counts["positive"] == 0:
                delta -= 0.1
            if delta:
                adjustments[key] = clamp_weight(adjustments.get(key, 0.0) + delta)
                summary_parts.append(f"{label} {key} {delta:+.1f}")

    subgoal_priorities = dict(snapshot.subgoal_priorities)
    if due_follow_ups:
        subgoal_priorities["follow_up_hygiene"] = 1.3
    else:
        subgoal_priorities["follow_up_hygiene"] = 1.0

    if goal_state is not None:
        for subgoal in goal_state.subgoals:
            if subgoal.subgoal_id.startswith("role:"):
                key = subgoal.subgoal_id.split(":", 1)[1]
                subgoal.priority = round(1.0 + role_adjustments.get(key, 0.0), 2)
            else:
                subgoal.priority = round(subgoal_priorities.get(subgoal.subgoal_id, subgoal.priority), 2)

    updated_snapshot = StrategySnapshot(
        updated_at=isoformat(utc_now()),
        reflection_summary="; ".join(summary_parts) if summary_parts else "No strategy changes from recent history.",
        role_weights=role_adjustments,
        industry_weights=industry_adjustments,
        source_weights=source_adjustments,
        subgoal_priorities=subgoal_priorities,
    )
    return updated_snapshot, goal_state


class JobSearchOrchestrator:
    def __init__(self, candidate_profile: dict[str, Any]) -> None:
        self.candidate_profile = candidate_profile
        self.state_store = RedisStateStore.from_env()
        self.goal_state = self.state_store.ensure_goal_state(candidate_profile)
        self.strategy_snapshot = self.state_store.get_strategy_snapshot(candidate_profile) or build_default_strategy_snapshot(candidate_profile)
        self.qa_dispatcher = QAEventDispatcher(candidate_profile, self.state_store)
        self.documentation_service = DocumentationService(
            candidate_profile=candidate_profile,
            state_store=self.state_store,
            strategy_snapshot=self.strategy_snapshot,
        )

    def _append_degraded_mode_notice(self, output: WorkflowOutput) -> None:
        if not self.state_store.status.available and not any(item.kind == "state_store_unavailable" for item in output.needs_review):
            append_review(
                output,
                kind="state_store_unavailable",
                reason="Redis state is unavailable, so the workflow is running in degraded stateless mode.",
                details=self.state_store.status.degraded_reason,
            )

    def _save_plan(self, workflow: str, output: WorkflowOutput, tracker_rows: list[dict[str, Any]] | None = None) -> list[FollowUpTask]:
        due_tasks = tracker_due_follow_ups(tracker_rows or [])
        for task in due_tasks:
            self.state_store.save_follow_up_task(task)
        all_due_tasks = self.state_store.list_follow_up_tasks()
        plan_tasks = build_plan_tasks(workflow, self.goal_state, all_due_tasks)
        self.state_store.save_plan_run(build_plan_run(workflow, plan_tasks))
        self._append_degraded_mode_notice(output)
        return all_due_tasks

    def _record_qa_result(self, output: WorkflowOutput, qa_result: QAResult) -> None:
        output.qa_results.append(qa_result)
        output.summary.qa_evaluations += 1
        if qa_result.verdict == "approve":
            output.summary.qa_approved += 1
        elif qa_result.verdict == "flag":
            output.summary.qa_flagged += 1
        else:
            output.summary.qa_rejected += 1

    def _append_qa_review(
        self,
        output: WorkflowOutput,
        *,
        qa_result: QAResult,
        company: str | None,
        role_title: str | None,
    ) -> None:
        verdict_label = "flagged" if qa_result.verdict == "flag" else "rejected"
        append_review(
            output,
            kind=f"qa_{qa_result.verdict}",
            reason=f"QA {verdict_label} {qa_result.event_type} during {qa_result.stage}.",
            details=" | ".join(qa_result.reasons) if qa_result.reasons else None,
            company=company,
            role_title=role_title,
        )

    def _refresh_documentation(self, workflow: str, output: WorkflowOutput) -> None:
        self.documentation_service.strategy_snapshot = self.strategy_snapshot
        self.documentation_service.refresh(workflow=workflow, output=output)

    def _prepare_tracker_context(self, workflow: str, output: WorkflowOutput) -> tuple[dict[str, Any], list[dict[str, Any]], list[FollowUpTask]]:
        sheet_result = read_tracker_sheet_impl(self.candidate_profile["sheet_url"])
        tracker_rows = sheet_result.get("rows", []) if sheet_result.get("implemented", False) else []
        due_tasks = self._save_plan(workflow, output, tracker_rows=tracker_rows)
        return sheet_result, tracker_rows, due_tasks

    def _record_tracker_update(
        self,
        output: WorkflowOutput,
        *,
        persisted_row: dict[str, Any],
        fallback_row: dict[str, Any],
        update_type: str,
        notes: str | None,
        company: str | None = None,
        role_title: str | None = None,
        duplicate_key: str | None = None,
    ) -> None:
        output.summary.tracker_rows_updated += 1
        output.tracker_updates.append(
            TrackerUpdate(
                company=company or persisted_row.get("company") or fallback_row.get("company"),
                role_title=role_title or persisted_row.get("role_title") or fallback_row.get("role_title"),
                status=str(persisted_row.get("status") or fallback_row.get("status") or ""),
                duplicate_key=duplicate_key or persisted_row.get("duplicate_key") or fallback_row.get("duplicate_key"),
                update_type=update_type,
                notes=notes,
            )
        )

    def _append_tracker_write_failure(
        self,
        output: WorkflowOutput,
        *,
        kind: str,
        reason: str,
        details: str | None,
        company: str | None = None,
        role_title: str | None = None,
    ) -> None:
        append_review(
            output,
            kind=kind,
            reason=reason,
            details=details,
            company=company,
            role_title=role_title,
        )

    def _sync_job_to_tracker(
        self,
        output: WorkflowOutput,
        job: JobRecord,
        *,
        action: str,
        resume_version: str | None = None,
        cover_letter_version: str | None = None,
    ) -> None:
        if action not in TRACKER_MATERIAL_ACTIONS:
            return
        if not resume_version or not cover_letter_version:
            self._append_tracker_write_failure(
                output,
                kind="application_materials_missing",
                reason="A new job was kept, but tracker sync was skipped because both generated material versions are required.",
                details=f"resume_version={resume_version or 'missing'}, cover_letter_version={cover_letter_version or 'missing'}",
                company=job.company,
                role_title=job.role_title,
            )
            return
        next_steps_by_action = {
            "prioritize": "Use the tailored resume and cover letter for application prep.",
            "track": "Use the tailored resume and cover letter, then track and monitor for updates.",
            "queue_review": "Review the fit, then use the tailored resume and cover letter if applying.",
        }
        tracker_row = build_tracker_row_from_job(
            job,
            status="Needs Review" if action == "queue_review" else "New",
            next_steps=next_steps_by_action[action],
            priority="high" if action == "prioritize" else "normal",
            resume_version=resume_version,
            cover_letter_version=cover_letter_version,
        )
        result = upsert_tracker_row_impl(
            sheet_url=self.candidate_profile["sheet_url"],
            row=tracker_row,
            duplicate_key=job.duplicate_key or "",
            match_strategy="hybrid",
        )
        if not result.get("implemented", True):
            self._append_tracker_write_failure(
                output,
                kind="tracker_unavailable",
                reason="Tracker updates could not be applied because Google Sheets is not configured or returned an error.",
                details=result.get("reason"),
                company=job.company,
                role_title=job.role_title,
            )
            return
        self._record_tracker_update(
            output,
            persisted_row=result.get("row") or {},
            fallback_row=tracker_row,
            update_type=str(result.get("status", "updated")),
            notes=job.reason,
            company=job.company,
            role_title=job.role_title,
            duplicate_key=job.duplicate_key,
        )

    def _maybe_generate_resume_artifact(
        self,
        output: WorkflowOutput,
        *,
        job: dict[str, Any],
        record: JobRecord,
        action: str,
    ) -> str | None:
        if action not in TRACKER_MATERIAL_ACTIONS:
            return None
        if not self.candidate_profile.get("resume_reference_documents"):
            return None

        try:
            result = generate_resume_artifact_impl(candidate_profile=self.candidate_profile, job=job)
        except Exception as exc:
            append_review(
                output,
                kind="resume_generation_unavailable",
                reason="Resume tailoring was requested but the tailored resume could not be generated.",
                details=str(exc),
                company=record.company,
                role_title=record.role_title,
            )
            return None
        if not result.get("implemented", False):
            append_review(
                output,
                kind="resume_generation_unavailable",
                reason="Resume tailoring was requested but the tailored resume could not be generated.",
                details=result.get("reason"),
                company=record.company,
                role_title=record.role_title,
            )
            return None

        try:
            artifact = ResumeArtifact.model_validate(result.get("artifact") or {})
        except Exception as exc:
            append_review(
                output,
                kind="resume_generation_unavailable",
                reason="Resume tailoring returned an invalid artifact payload.",
                details=str(exc),
                company=record.company,
                role_title=record.role_title,
            )
            return None
        output.resume_artifacts.append(artifact)
        if artifact.google_doc_error:
            append_review(
                output,
                kind="resume_google_doc_unavailable",
                reason="The tailored resume was generated locally, but Google Docs publishing did not complete.",
                details=artifact.google_doc_error,
                company=record.company,
                role_title=record.role_title,
            )
        return artifact.version

    def _maybe_generate_cover_letter_artifact(
        self,
        output: WorkflowOutput,
        *,
        job: dict[str, Any],
        record: JobRecord,
        action: str,
    ) -> str | None:
        if action not in TRACKER_MATERIAL_ACTIONS:
            return None
        if not self.candidate_profile.get("resume_reference_documents"):
            return None

        try:
            result = generate_cover_letter_artifact_impl(candidate_profile=self.candidate_profile, job=job)
        except Exception as exc:
            append_review(
                output,
                kind="cover_letter_generation_unavailable",
                reason="Cover letter drafting was requested but the cover letter could not be generated.",
                details=str(exc),
                company=record.company,
                role_title=record.role_title,
            )
            return None
        if not result.get("implemented", False):
            append_review(
                output,
                kind="cover_letter_generation_unavailable",
                reason="Cover letter drafting was requested but the cover letter could not be generated.",
                details=result.get("reason"),
                company=record.company,
                role_title=record.role_title,
            )
            return None

        try:
            artifact = CoverLetterArtifact.model_validate(result.get("artifact") or {})
        except Exception as exc:
            append_review(
                output,
                kind="cover_letter_generation_unavailable",
                reason="Cover letter drafting returned an invalid artifact payload.",
                details=str(exc),
                company=record.company,
                role_title=record.role_title,
            )
            return None
        output.cover_letter_artifacts.append(artifact)
        if artifact.google_doc_error:
            append_review(
                output,
                kind="cover_letter_google_doc_unavailable",
                reason="The tailored cover letter was generated locally, but Google Docs publishing did not complete.",
                details=artifact.google_doc_error,
                company=record.company,
                role_title=record.role_title,
            )
        return artifact.version

    def _job_with_tracker_experience_overrides(
        self,
        job: dict[str, Any],
        tracker_rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        existing_row = find_matching_row(tracker_rows, job, match_strategy="hybrid")
        if existing_row is None:
            return job

        merged_job = dict(job)
        for field in ("required_experience_years", "candidate_experience_years", "experience_gap_years"):
            existing_value = existing_row.get(field)
            if blank_to_none(existing_value) is not None:
                merged_job[field] = existing_value
        return merged_job

    def _backfill_tracker_materials(
        self,
        *,
        generate_resume: bool,
        generate_cover_letter: bool,
        overwrite_existing: bool = True,
        refresh_docs: bool = True,
    ) -> WorkflowOutput:
        output = WorkflowOutput()
        sheet_result = read_tracker_sheet_impl(self.candidate_profile["sheet_url"])
        tracker_rows = sheet_result.get("rows", []) if sheet_result.get("implemented", False) else []
        output.summary.jobs_reviewed = len(tracker_rows)

        if not sheet_result.get("implemented", False):
            append_review(
                output,
                kind="tracker_unavailable",
                reason="Tracker rows could not be read for application-material backfill.",
                details=sheet_result.get("reason"),
            )
            return output

        for row in tracker_rows:
            record = job_record_from_tracker_row(row)
            if not record.company or not record.role_title:
                append_review(
                    output,
                    kind="tracker_row_missing_job_identity",
                    reason="A tracker row needs company and role title before application materials can be generated.",
                    company=record.company,
                    role_title=record.role_title,
                )
                continue

            job_payload = tracker_row_to_job_payload(row)
            resume_version = row.get("resume_version")
            cover_letter_version = row.get("cover_letter_version")

            if generate_resume and (overwrite_existing or not resume_version):
                resume_version = self._maybe_generate_resume_artifact(
                    output,
                    job=job_payload,
                    record=record,
                    action="track",
                )
            if generate_cover_letter and (overwrite_existing or not cover_letter_version):
                cover_letter_version = self._maybe_generate_cover_letter_artifact(
                    output,
                    job=job_payload,
                    record=record,
                    action="track",
                )

            update_row = {
                "company": record.company,
                "role_title": record.role_title,
                "location": record.location,
                "duplicate_key": record.duplicate_key,
                "tailor_resume": "yes",
                "resume_version": resume_version if generate_resume else None,
                "cover_letter_version": cover_letter_version if generate_cover_letter else None,
            }
            if (
                update_row["resume_version"] is None
                and update_row["cover_letter_version"] is None
                and row.get("tailor_resume")
            ):
                continue

            result = upsert_tracker_row_impl(
                sheet_url=self.candidate_profile["sheet_url"],
                row=update_row,
                duplicate_key=record.duplicate_key or "",
                match_strategy="hybrid",
            )
            if not result.get("implemented", True):
                self._append_tracker_write_failure(
                    output,
                    kind="tracker_unavailable",
                    reason="Generated application-material versions could not be written back to the tracker.",
                    details=result.get("reason"),
                    company=record.company,
                    role_title=record.role_title,
                )
                continue
            self._record_tracker_update(
                output,
                persisted_row=result.get("row") or {},
                fallback_row=update_row,
                update_type=str(result.get("status", "updated")),
                notes="Backfilled application materials.",
                company=record.company,
                role_title=record.role_title,
                duplicate_key=record.duplicate_key,
            )

        output.assistant_response = (
            f"Backfilled {len(output.resume_artifacts)} resumes and "
            f"{len(output.cover_letter_artifacts)} cover letters across {len(tracker_rows)} tracker rows."
        )
        if refresh_docs:
            self._refresh_documentation("backfill_materials", output)
        return output

    def backfill_tracker_resumes(self, *, overwrite_existing: bool = True, refresh_docs: bool = True) -> WorkflowOutput:
        return self._backfill_tracker_materials(
            generate_resume=True,
            generate_cover_letter=False,
            overwrite_existing=overwrite_existing,
            refresh_docs=refresh_docs,
        )

    def backfill_tracker_cover_letters(self, *, overwrite_existing: bool = True, refresh_docs: bool = True) -> WorkflowOutput:
        return self._backfill_tracker_materials(
            generate_resume=False,
            generate_cover_letter=True,
            overwrite_existing=overwrite_existing,
            refresh_docs=refresh_docs,
        )

    def backfill_tracker_application_materials(self, *, overwrite_existing: bool = True, refresh_docs: bool = True) -> WorkflowOutput:
        return self._backfill_tracker_materials(
            generate_resume=True,
            generate_cover_letter=True,
            overwrite_existing=overwrite_existing,
            refresh_docs=refresh_docs,
        )

    def run_availability_checks(self, *, refresh_docs: bool = True) -> WorkflowOutput:
        output = WorkflowOutput()
        sheet_result = read_tracker_sheet_impl(self.candidate_profile["sheet_url"])
        tracker_rows = sheet_result.get("rows", []) if sheet_result.get("implemented", False) else []
        due_rows = tracker_rows_due_for_availability_check(tracker_rows)
        output.summary.jobs_reviewed = len(due_rows)

        if not sheet_result.get("implemented", False):
            append_review(
                output,
                kind="tracker_unavailable",
                reason="Tracker rows could not be read for job availability checks.",
                details=sheet_result.get("reason"),
            )
            if refresh_docs:
                self._refresh_documentation("availability", output)
            return output

        for row in due_rows:
            check = verify_job_availability_impl(tracker_row_to_job_payload(row))
            update_row = build_tracker_row_from_availability_check(row, check)
            result = upsert_tracker_row_impl(
                sheet_url=self.candidate_profile["sheet_url"],
                row=update_row,
                duplicate_key=update_row["duplicate_key"] or "",
                match_strategy="hybrid",
            )
            output.summary.availability_checks_processed += 1

            if check.get("availability_status") == "unavailable" or check.get("link_status") == "invalid":
                append_review(
                    output,
                    kind="job_availability_problem",
                    reason="A tracked job link is invalid or the posting appears unavailable.",
                    details=check.get("reason"),
                    company=row.get("company"),
                    role_title=row.get("role_title"),
                )
            elif check.get("link_status") in {"missing", "unknown"} or check.get("availability_status") == "unknown":
                append_review(
                    output,
                    kind="job_availability_uncertain",
                    reason="A tracked job could not be conclusively verified.",
                    details=check.get("reason"),
                    company=row.get("company"),
                    role_title=row.get("role_title"),
                )

            if not result.get("implemented", False):
                self._append_tracker_write_failure(
                    output,
                    kind="availability_tracker_update_failed",
                    reason="A job availability check could not be written back to Google Sheets.",
                    details=result.get("reason"),
                    company=row.get("company"),
                    role_title=row.get("role_title"),
                )
                continue

            self._record_tracker_update(
                output,
                persisted_row=result.get("row") or update_row,
                fallback_row=update_row,
                update_type="availability_check",
                notes=update_row.get("notes"),
                company=row.get("company"),
                role_title=row.get("role_title"),
                duplicate_key=update_row.get("duplicate_key"),
            )

        if output.summary.availability_checks_processed:
            output.assistant_response = f"Rechecked {output.summary.availability_checks_processed} tracked job postings for link health and availability."
        if refresh_docs:
            self._refresh_documentation("availability", output)
        return output

    def run_jobs(self, *, refresh_docs: bool = True) -> WorkflowOutput:
        output = WorkflowOutput()
        _sheet_result, tracker_rows, _due_tasks = self._prepare_tracker_context("jobs", output)

        keywords = build_search_keywords(self.candidate_profile)
        search_result = search_jobs_impl(
            keywords=keywords,
            location_mode="both",
            origin=self.candidate_profile["location_rules"]["origin"],
            radius_miles=int(self.candidate_profile["location_rules"]["radius_miles"]),
            salary_floor=int(self.candidate_profile["salary_floor"]),
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
            if refresh_docs:
                self._refresh_documentation("jobs", output)
            return output

        for note in search_result.get("notes", []):
            append_review(output, kind="job_search_note", reason="Job search returned a note that may need review.", details=note)

        kept_count = 0
        for job in search_result.get("jobs", []):
            scoring_job = self._job_with_tracker_experience_overrides(job, tracker_rows)
            fit = score_job_fit_impl(scoring_job, self.candidate_profile)
            action, final_score, rationale, freshness_points, source_points, strategy_points, effort_points = decide_job_action(
                job,
                fit,
                self.candidate_profile,
                self.strategy_snapshot,
            )
            decision = build_decision_record(
                workflow="jobs",
                job=job,
                fit=fit,
                action=action,
                final_score=final_score,
                freshness_points=freshness_points,
                source_points=source_points,
                strategy_points=strategy_points,
                effort_points=effort_points,
                rationale=rationale,
            )
            self.state_store.append_decision(decision)
            if action == "skip":
                continue

            decision_reason = f"Decision: {action} (score {final_score}). {rationale}"
            record = build_job_record(job, fit, decision_reason=decision_reason)
            qa_result = self.qa_dispatcher.evaluate(
                workflow="jobs",
                event_type=WorkflowEvent.JOB_FOUND,
                stage="pre_action",
                entity_key=record.duplicate_key,
                payload={
                    "job": job,
                    "fit": fit,
                    "decision": decision.model_dump(),
                },
                context={"tracker_rows": tracker_rows},
            )
            self._record_qa_result(output, qa_result)
            if qa_result.verdict == "reject":
                self._append_qa_review(output, qa_result=qa_result, company=record.company, role_title=record.role_title)
                continue

            kept_count += 1
            output.new_jobs.append(record)
            if action == "queue_review":
                append_review(
                    output,
                    kind="job_requires_review",
                    reason="A job passed the search filters but still needs manual review.",
                    details=rationale,
                    company=record.company,
                    role_title=record.role_title,
                )
            if qa_result.verdict == "flag":
                self._append_qa_review(output, qa_result=qa_result, company=record.company, role_title=record.role_title)
                continue
            resume_version = self._maybe_generate_resume_artifact(
                output,
                job=job,
                record=record,
                action=action,
            )
            cover_letter_version = self._maybe_generate_cover_letter_artifact(
                output,
                job=job,
                record=record,
                action=action,
            )
            self._sync_job_to_tracker(
                output,
                record,
                action=action,
                resume_version=resume_version,
                cover_letter_version=cover_letter_version,
            )

        output.new_jobs.sort(key=lambda job: job.fit_score or 0, reverse=True)
        output.summary.jobs_added = kept_count
        if kept_count:
            output.assistant_response = f"Reviewed {output.summary.jobs_reviewed} jobs and kept {kept_count} after deterministic scoring."
        if refresh_docs:
            self._refresh_documentation("jobs", output)
        return output

    def run_gmail(self, *, refresh_docs: bool = True) -> WorkflowOutput:
        output = WorkflowOutput()
        sheet_result, tracker_rows, due_tasks = self._prepare_tracker_context("gmail", output)

        if not sheet_result.get("implemented", False):
            append_review(
                output,
                kind="tracker_unavailable",
                reason="Tracker matching could not be completed because Google Sheets is not configured or returned an error.",
                details=sheet_result.get("reason"),
            )

        max_results = int(os.getenv("GMAIL_SEARCH_MAX_RESULTS", "25"))
        gmail_result = search_gmail_job_updates_impl(queries=DEFAULT_GMAIL_QUERIES, max_results=max_results)
        if not gmail_result.get("implemented", False):
            append_review(
                output,
                kind="gmail_unavailable",
                reason="Gmail updates could not be processed because the Gmail integration is unavailable.",
                details=gmail_result.get("reason"),
            )
            if refresh_docs:
                self._refresh_documentation("gmail", output)
            return output

        processed_count = 0
        for message in gmail_result.get("messages", []):
            classified = classify_email_payload(
                email_subject=message.get("subject", ""),
                email_from=message.get("from", ""),
                email_body=message.get("body", ""),
            )
            matched = match_email_to_tracker_row_payload(classified_email=classified, tracker_rows=tracker_rows)
            matched_row = matched.get("row") or {}
            duplicate_key = email_duplicate_key(classified, matched_row)
            output.gmail_updates.append(build_gmail_update_record(classified, matched))

            qa_result = self.qa_dispatcher.evaluate(
                workflow="gmail",
                event_type=WorkflowEvent.EMAIL_RECEIVED,
                stage="pre_action",
                entity_key=duplicate_key or message.get("id"),
                payload={
                    "message": message,
                    "classified": classified,
                    "matched": matched,
                    "matched_row": matched_row,
                },
                context={"tracker_rows": tracker_rows},
            )
            self._record_qa_result(output, qa_result)
            if qa_result.verdict in {"flag", "reject"}:
                self._append_qa_review(
                    output,
                    qa_result=qa_result,
                    company=classified.get("company") or matched_row.get("company"),
                    role_title=classified.get("role_title") or matched_row.get("role_title"),
                )
                processed_count += 1
                continue

            self.state_store.append_outcome(
                build_outcome_from_email(
                    classified_email=classified,
                    matched_row=matched_row,
                    message=message,
                    duplicate_key=duplicate_key,
                )
            )

            if classified["classification"] in POSITIVE_SIGNAL_CLASSIFICATIONS:
                self.state_store.mark_follow_up_completed(duplicate_key)

            if classified["classification"] in IMMEDIATE_REVIEW_CLASSIFICATIONS:
                task = build_follow_up_task(
                    duplicate_key=duplicate_key,
                    company=classified.get("company") or matched_row.get("company"),
                    role_title=classified.get("role_title") or matched_row.get("role_title"),
                    due_at=utc_now(),
                    reason=f"{classified['classification']} requires immediate review.",
                )
                self.state_store.save_follow_up_task(task)
                append_review(
                    output,
                    kind="gmail_action_required",
                    reason=f"{classified['classification']} requires prompt attention.",
                    details=classified.get("action"),
                    company=task.company,
                    role_title=task.role_title,
                )

            if sheet_result.get("implemented", False):
                tracker_row = build_tracker_row_from_email_update(
                    classified_email=classified,
                    matched_row=matched_row if matched.get("matched") else None,
                    message=message,
                )
                upsert_result = upsert_tracker_row_impl(
                    sheet_url=self.candidate_profile["sheet_url"],
                    row=tracker_row,
                    duplicate_key=tracker_row["duplicate_key"] or "",
                    match_strategy="hybrid",
                )
                if upsert_result.get("implemented", False):
                    self._record_tracker_update(
                        output,
                        persisted_row=upsert_result.get("row") or tracker_row,
                        fallback_row=tracker_row,
                        update_type=str(upsert_result.get("status", "updated")),
                        notes=tracker_row.get("notes"),
                    )
                else:
                    self._append_tracker_write_failure(
                        output,
                        kind="gmail_tracker_update_failed",
                        reason="A Gmail-derived tracker update could not be written to Google Sheets.",
                        details=upsert_result.get("reason"),
                    )
            processed_count += 1

        for task in due_tasks:
            append_review(
                output,
                kind="follow_up_due",
                reason="A tracked application is due for follow-up review.",
                details=task.reason,
                company=task.company,
                role_title=task.role_title,
            )

        output.summary.gmail_updates_processed = processed_count
        if processed_count:
            output.assistant_response = f"Processed {processed_count} Gmail updates and surfaced any immediate review items."
        if refresh_docs:
            self._refresh_documentation("gmail", output)
        return output

    def run_reflect(self, *, refresh_docs: bool = True) -> WorkflowOutput:
        output = WorkflowOutput()
        sheet_result = read_tracker_sheet_impl(self.candidate_profile["sheet_url"])
        tracker_rows = sheet_result.get("rows", []) if sheet_result.get("implemented", False) else []
        due_tasks = self._save_plan("reflect", output, tracker_rows=tracker_rows)

        decisions = self.state_store.list_decisions(lookback_days=REFLECTION_LOOKBACK_DAYS)
        outcomes = self.state_store.list_outcomes(lookback_days=REFLECTION_LOOKBACK_DAYS)
        updated_snapshot, updated_goal_state = reflect_strategy(
            candidate_profile=self.candidate_profile,
            snapshot=self.strategy_snapshot,
            goal_state=self.goal_state,
            decisions=decisions,
            outcomes=outcomes,
            due_follow_ups=due_tasks,
        )
        qa_result = self.qa_dispatcher.evaluate(
            workflow="reflect",
            event_type=WorkflowEvent.STRATEGY_REFLECTED,
            stage="pre_action",
            entity_key=self.goal_state.goal_id if self.goal_state is not None else "default_goal",
            payload={
                "previous_snapshot": self.strategy_snapshot,
                "updated_snapshot": updated_snapshot,
                "decisions": decisions,
                "outcomes": outcomes,
                "due_follow_ups": due_tasks,
            },
        )
        self._record_qa_result(output, qa_result)
        if qa_result.verdict == "approve":
            self.strategy_snapshot = updated_snapshot
            self.goal_state = updated_goal_state
            self.state_store.save_strategy_snapshot(updated_snapshot)
            if updated_goal_state is not None:
                self.state_store.save_goal_state(updated_goal_state)
            output.assistant_response = updated_snapshot.reflection_summary
            if refresh_docs:
                self._refresh_documentation("reflect", output)
            return output

        self._append_qa_review(output, qa_result=qa_result, company=None, role_title=None)
        output.assistant_response = f"QA blocked reflection persistence. {updated_snapshot.reflection_summary}"
        if refresh_docs:
            self._refresh_documentation("reflect", output)
        return output

    def run_daily(self) -> WorkflowOutput:
        jobs_output = self.run_jobs(refresh_docs=False)
        availability_output = self.run_availability_checks(refresh_docs=False)
        gmail_output = self.run_gmail(refresh_docs=False)
        reflect_output = self.run_reflect(refresh_docs=False)
        merged = WorkflowOutput(
            summary=jobs_output.summary.model_copy(deep=True),
            new_jobs=[*jobs_output.new_jobs],
            gmail_updates=[*gmail_output.gmail_updates],
            resume_artifacts=[*jobs_output.resume_artifacts],
            cover_letter_artifacts=[*jobs_output.cover_letter_artifacts],
            tracker_updates=[*jobs_output.tracker_updates, *availability_output.tracker_updates, *gmail_output.tracker_updates],
            qa_results=[*jobs_output.qa_results, *gmail_output.qa_results, *reflect_output.qa_results],
            documentation_updates=[],
            needs_review=[*jobs_output.needs_review, *availability_output.needs_review, *gmail_output.needs_review, *reflect_output.needs_review],
            follow_up_questions=[],
            assistant_response=reflect_output.assistant_response or gmail_output.assistant_response or availability_output.assistant_response or jobs_output.assistant_response,
        )
        merged.summary.availability_checks_processed += availability_output.summary.availability_checks_processed
        merged.summary.gmail_updates_processed += gmail_output.summary.gmail_updates_processed
        merged.summary.tracker_rows_updated += availability_output.summary.tracker_rows_updated + gmail_output.summary.tracker_rows_updated
        merged.summary.jobs_reviewed = jobs_output.summary.jobs_reviewed
        merged.summary.jobs_added = jobs_output.summary.jobs_added
        merged.summary.duplicates_skipped = jobs_output.summary.duplicates_skipped
        merged.summary.qa_evaluations += gmail_output.summary.qa_evaluations + reflect_output.summary.qa_evaluations
        merged.summary.qa_approved += gmail_output.summary.qa_approved + reflect_output.summary.qa_approved
        merged.summary.qa_flagged += gmail_output.summary.qa_flagged + reflect_output.summary.qa_flagged
        merged.summary.qa_rejected += gmail_output.summary.qa_rejected + reflect_output.summary.qa_rejected
        self._refresh_documentation("daily", merged)
        return merged
