from __future__ import annotations

import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo

import pydantic as pydantic_module

from job_agent.config import get_model_name
from job_agent.runtime import AVAILABILITY_RECHECK_DAYS
from job_agent.tools.dedupe import build_duplicate_key, dedupe_jobs, normalize_text
from job_agent.tools._shared import resolve_function_tool


function_tool = resolve_function_tool()
BaseModel = cast(Any, pydantic_module).BaseModel
Field = cast(Any, pydantic_module).Field

FIT_BANDS = (
    (90, "excellent"),
    (80, "strong"),
    (70, "good"),
    (60, "maybe"),
    (0, "ignore"),
)

SOURCE_DOMAIN_HINTS = {
    "linkedin": ("linkedin.com",),
    "indeed": ("indeed.com",),
    "ziprecruiter": ("ziprecruiter.com",),
    "greenhouse": ("greenhouse.io", "boards.greenhouse.io", "job-boards.greenhouse.io"),
    "lever": ("jobs.lever.co", "lever.co"),
    "workday": ("myworkdayjobs.com",),
    "ashby": ("jobs.ashbyhq.com", "ashbyhq.com"),
    "smartrecruiters": ("smartrecruiters.com",),
    "google_jobs": ("google.com",),
}

SOURCE_DISPLAY_NAMES = {
    "linkedin": "LinkedIn",
    "indeed": "Indeed",
    "ziprecruiter": "ZipRecruiter",
    "greenhouse": "Greenhouse",
    "lever": "Lever",
    "workday": "Workday",
    "ashby": "Ashby",
    "smartrecruiters": "SmartRecruiters",
    "google_jobs": "Google Jobs",
    "company_sites": "company career sites",
}

REMOTE_VALUES = {"remote"}
LOCAL_VALUES = {"local", "hybrid"}
SEARCH_RETRY_ATTEMPTS = 2
MONTREAL_TIMEZONE = ZoneInfo("America/Montreal")
DAYS_PER_YEAR = 365.25
JOB_LINK_CHECK_TIMEOUT_SECONDS = 10
EXPERIENCE_RANGE_PATTERN = re.compile(r"\b(\d+)\s*(?:-|to)\s*(\d+)\s+years?(?:\s+of)?\s+experience\b", re.IGNORECASE)
EXPERIENCE_PLUS_PATTERN = re.compile(r"\b(\d+)\s*\+\s*years?(?:\s+of)?\s+experience\b", re.IGNORECASE)
EXPERIENCE_MINIMUM_PATTERN = re.compile(r"\bminimum of\s+(\d+)\s+years?(?:\s+of)?\s+experience\b", re.IGNORECASE)
EXPERIENCE_AT_LEAST_PATTERN = re.compile(r"\bat least\s+(\d+)\s+years?(?:\s+of)?\s+experience\b", re.IGNORECASE)
UNAVAILABLE_TEXT_MARKERS = (
    "job is no longer available",
    "job no longer available",
    "position is no longer available",
    "posting is no longer available",
    "no longer accepting applications",
    "this job has expired",
    "job has expired",
    "position has been filled",
    "job has been filled",
    "this position is closed",
    "job is closed",
    "posting closed",
)


class WebSearchJob(BaseModel):
    company: str | None = None
    role_title: str | None = None
    location: str | None = None
    source: str | None = None
    posting_url: str | None = None
    careers_url: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str | None = "USD"
    remote_or_local: Literal["remote", "local", "hybrid", "unknown"] = "unknown"
    distance_miles: float | None = None
    industry: str | None = None
    description: str | None = None
    posted_at: str | None = None
    posting_age_days: int | None = None
    reason: str | None = None


class WebSearchJobsResult(BaseModel):
    jobs: list[WebSearchJob] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class JobAvailabilityCheckResult(BaseModel):
    checked_url: str | None = None
    link_status: Literal["valid", "invalid", "missing", "unknown"] = "unknown"
    availability_status: Literal["available", "unavailable", "unknown"] = "unknown"
    checked_at: str
    next_check_at: str
    reason: str | None = None


def job_is_remote(job: dict[str, Any]) -> bool:
    remote_value = str(job.get("remote_or_local", "")).lower()
    location_value = normalize_text(job.get("location"))
    return remote_value == "remote" or "remote" in location_value


def location_matches(job: dict[str, Any], candidate_profile: dict[str, Any]) -> bool:
    if job_is_remote(job):
        return bool(candidate_profile["location_rules"]["allow_remote"])

    max_radius = candidate_profile["location_rules"]["radius_miles"]
    radius = job.get("distance_miles")
    if radius is None:
        location_value = normalize_text(job.get("location"))
        origin_value = normalize_text(candidate_profile["location_rules"]["origin"])
        return origin_value in location_value

    return float(radius) <= float(max_radius)


def salary_meets_floor(job: dict[str, Any], salary_floor: int) -> bool:
    salary = job.get("salary_min")
    if salary is None:
        return True
    return int(salary) >= int(salary_floor)


def keyword_match_count(job: dict[str, Any], keywords: list[str]) -> tuple[int, bool]:
    title = normalize_text(job.get("role_title"))
    haystack = f"{title} {normalize_text(job.get('description'))}"
    matches = 0
    strong_title_match = False

    for keyword in keywords:
        normalized_keyword = normalize_text(keyword)
        if not normalized_keyword:
            continue
        if normalized_keyword in haystack:
            matches += 1
            if len(normalized_keyword.split()) > 1 and normalized_keyword in title:
                strong_title_match = True

    return matches, strong_title_match


def job_matches_keywords(job: dict[str, Any], keywords: list[str]) -> bool:
    if not keywords:
        return True

    matches, strong_title_match = keyword_match_count(job, keywords)
    return strong_title_match or matches >= 2


def fit_band(score: int) -> str:
    for floor, label in FIT_BANDS:
        if score >= floor:
            return label
    return "ignore"


def current_montreal_date() -> date:
    return datetime.now(MONTREAL_TIMEZONE).date()


def utc_timestamp(value: datetime | None = None) -> str:
    current = value or datetime.now(UTC)
    return current.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso_date(value: Any) -> date | None:
    if value in (None, "", []):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.split("T", 1)[0])
    except ValueError:
        return None


def normalize_experience_years(value: Any) -> float | None:
    if value in (None, "", []):
        return None
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return None


def merge_date_ranges(ranges: list[tuple[date, date]]) -> list[tuple[date, date]]:
    if not ranges:
        return []

    merged: list[tuple[date, date]] = []
    for start, end in sorted(ranges, key=lambda item: (item[0], item[1])):
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
            continue
        merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def experience_years_from_ranges(ranges: list[tuple[date, date]]) -> float | None:
    if not ranges:
        return None
    total_days = sum((end - start).days for start, end in merge_date_ranges(ranges))
    return round(total_days / DAYS_PER_YEAR, 1)


def build_experience_ranges(
    work_history: list[dict[str, Any]],
    *,
    relevant_only: bool,
    current_date: date | None = None,
) -> list[tuple[date, date]]:
    active_date = current_date or current_montreal_date()
    ranges: list[tuple[date, date]] = []
    for role in work_history:
        if relevant_only and not bool(role.get("counts_toward_relevant_experience")):
            continue

        start_date = parse_iso_date(role.get("start_date"))
        end_value = role.get("end_date")
        end_date = parse_iso_date(end_value) or (active_date if end_value in (None, "", []) else None)
        if start_date is None or end_date is None or end_date < start_date:
            continue

        # Convert inclusive source dates to half-open ranges so overlapping tenure counts once.
        ranges.append((start_date, end_date + timedelta(days=1)))
    return ranges


def derive_candidate_experience_years(
    candidate_profile: dict[str, Any],
    *,
    current_date: date | None = None,
) -> float | None:
    work_history = candidate_profile.get("work_history") or []
    if not isinstance(work_history, list):
        return None

    relevant_ranges = build_experience_ranges(work_history, relevant_only=True, current_date=current_date)
    if relevant_ranges:
        return experience_years_from_ranges(relevant_ranges)

    total_ranges = build_experience_ranges(work_history, relevant_only=False, current_date=current_date)
    return experience_years_from_ranges(total_ranges)


def parse_required_experience_years(text: str | None) -> float | None:
    if not text:
        return None

    minimums: list[int] = []
    minimums.extend(int(match.group(1)) for match in EXPERIENCE_PLUS_PATTERN.finditer(text))
    minimums.extend(int(match.group(1)) for match in EXPERIENCE_RANGE_PATTERN.finditer(text))
    minimums.extend(int(match.group(1)) for match in EXPERIENCE_MINIMUM_PATTERN.finditer(text))
    minimums.extend(int(match.group(1)) for match in EXPERIENCE_AT_LEAST_PATTERN.finditer(text))
    if not minimums:
        return None
    return float(max(minimums))


def resolve_required_experience_years(job: dict[str, Any]) -> float | None:
    explicit_value = normalize_experience_years(job.get("required_experience_years"))
    if explicit_value is not None:
        return explicit_value

    description = job.get("description")
    if not isinstance(description, str):
        return None
    return parse_required_experience_years(description)


def experience_adjustment(
    required_experience_years: float | None,
    candidate_experience_years: float | None,
) -> tuple[int, str]:
    if required_experience_years is None:
        return 0, "experience requirement not stated"
    if required_experience_years <= 0:
        return 12, "experience matches or exceeds the requirement"
    if candidate_experience_years is None:
        return 0, "candidate experience unavailable"

    ratio = candidate_experience_years / required_experience_years
    if ratio >= 1.0:
        return 12, "experience matches or exceeds the requirement"
    if ratio >= 0.85:
        return 0, "experience is slightly below the requirement"
    if ratio >= 0.60:
        return -15, "experience is materially below the requirement"
    return -25, "experience is materially below the requirement"


def calculate_fit_score(job: dict[str, Any], candidate_profile: dict[str, Any]) -> dict[str, Any]:
    title = normalize_text(job.get("role_title"))
    company = job.get("company")
    keywords = {normalize_text(keyword) for keyword in candidate_profile.get("keywords", [])}
    target_roles = {normalize_text(role) for role in candidate_profile.get("target_roles", [])}
    industries = {normalize_text(industry) for industry in candidate_profile.get("target_industries", [])}
    candidate_experience_years = derive_candidate_experience_years(candidate_profile)
    required_experience_years = resolve_required_experience_years(job)
    experience_gap_years = (
        round(candidate_experience_years - required_experience_years, 1)
        if candidate_experience_years is not None and required_experience_years is not None
        else None
    )

    title_score = 35 if any(target_role in title for target_role in target_roles if target_role) else 0
    keyword_hits = sum(1 for keyword in keywords if keyword and keyword in normalize_text(job.get("description", "")))
    keyword_score = min(keyword_hits * 4, 28)
    industry_score = 12 if normalize_text(job.get("industry")) in industries else 0
    location_score = 15 if location_matches(job, candidate_profile) else 0
    salary_score = 10 if salary_meets_floor(job, candidate_profile["salary_floor"]) else 0
    experience_score, experience_reason = experience_adjustment(required_experience_years, candidate_experience_years)

    score = max(min(title_score + keyword_score + industry_score + location_score + salary_score + experience_score, 100), 0)
    reasons: list[str] = []
    if title_score:
        reasons.append("title aligns with target roles")
    if keyword_score:
        reasons.append(f"{keyword_hits} profile keywords matched")
    if industry_score:
        reasons.append("industry aligns with target sectors")
    if location_score:
        reasons.append("location matches the candidate rules")
    if salary_score:
        reasons.append("salary meets or exceeds the floor")
    reasons.append(experience_reason)
    if not reasons:
        reasons.append("weak alignment against the current profile")

    return {
        "company": company,
        "role_title": job.get("role_title"),
        "fit_score": score,
        "fit_band": fit_band(score),
        "reason": "; ".join(reasons),
        "required_experience_years": required_experience_years,
        "candidate_experience_years": candidate_experience_years,
        "experience_gap_years": experience_gap_years,
        "duplicate_key": job.get("duplicate_key")
        or build_duplicate_key(job.get("company"), job.get("role_title"), job.get("location")),
    }


def filter_and_rank_jobs(jobs: list[dict[str, Any]], candidate_profile: dict[str, Any]) -> dict[str, Any]:
    deduped_jobs, duplicates_skipped = dedupe_jobs(jobs)
    eligible_jobs = [
        job
        for job in deduped_jobs
        if location_matches(job, candidate_profile) and salary_meets_floor(job, candidate_profile["salary_floor"])
    ]

    scored_jobs = []
    for job in eligible_jobs:
        score = calculate_fit_score(job, candidate_profile)
        merged = dict(job)
        merged.update(score)
        scored_jobs.append(merged)

    scored_jobs.sort(key=lambda item: item["fit_score"], reverse=True)
    return {
        "jobs": scored_jobs,
        "summary": {
            "jobs_reviewed": len(jobs),
            "jobs_kept": len(scored_jobs),
            "duplicates_skipped": duplicates_skipped,
        },
    }


def build_search_request_summary(
    keywords: list[str],
    location_mode: str,
    origin: str,
    radius_miles: int,
    salary_floor: int,
    sources: list[str],
) -> dict[str, Any]:
    return {
        "keywords": keywords,
        "location_mode": location_mode,
        "origin": origin,
        "radius_miles": radius_miles,
        "salary_floor": salary_floor,
        "sources": sources,
    }


def normalize_source_name(value: str | None, url: str | None = None) -> str:
    normalized_value = normalize_text(value)
    for source_name in SOURCE_DISPLAY_NAMES:
        if source_name in normalized_value:
            return source_name
        if normalize_text(SOURCE_DISPLAY_NAMES[source_name]) in normalized_value:
            return source_name

    if url:
        normalized_url = normalize_text(url)
        for source_name, domains in SOURCE_DOMAIN_HINTS.items():
            if any(domain.replace(".", " ") in normalized_url for domain in domains):
                return source_name

    return "company_sites"


def coerce_remote_or_local(value: str | None) -> Literal["remote", "local", "hybrid", "unknown"]:
    normalized_value = normalize_text(value)
    if normalized_value == "remote":
        return "remote"
    if normalized_value == "local" or normalized_value == "onsite" or normalized_value == "on site":
        return "local"
    if normalized_value == "hybrid":
        return "hybrid"
    return "unknown"


def approximate_user_location(origin: str) -> dict[str, str]:
    parts = [part.strip() for part in origin.split(",") if part.strip()]
    location: dict[str, str] = {"type": "approximate"}
    if parts:
        location["city"] = parts[0]
    if len(parts) > 1:
        location["region"] = parts[1]
        if len(parts[1]) == 2:
            location["country"] = "US"
    return location


def build_allowed_domains(sources: list[str]) -> list[str] | None:
    if "company_sites" in sources:
        return None

    domains: list[str] = []
    for source in sources:
        domains.extend(SOURCE_DOMAIN_HINTS.get(source, ()))
    return sorted(set(domains)) or None


def build_job_search_prompt(
    keywords: list[str],
    location_mode: str,
    origin: str,
    radius_miles: int,
    salary_floor: int,
    sources: list[str],
) -> str:
    source_labels = [SOURCE_DISPLAY_NAMES.get(source, source) for source in sources]
    allowed_domains = build_allowed_domains(sources)
    location_rule = {
        "remote": f"Only include fully remote roles relevant to candidates based in {origin}.",
        "radius": f"Only include roles within {radius_miles} miles of {origin}.",
        "both": f"Include fully remote roles and roles within {radius_miles} miles of {origin}.",
    }[location_mode]

    return (
        "Search the web for current job postings that match this request.\n"
        f"Keywords and profile hints: {', '.join(keywords)}\n"
        f"Preferred sources: {', '.join(source_labels)}\n"
        f"Location rule: {location_rule}\n"
        f"Salary rule: reject jobs with a listed base salary floor below ${salary_floor:,}. "
        "If salary is not listed, keep the role if it otherwise fits.\n"
        "Only include jobs whose title or responsibilities clearly align with the provided keywords.\n"
        "Return only real job postings, not blog posts or generic category pages.\n"
        "Prefer official company application URLs when available.\n"
        "Only return jobs when the posting or application link appears valid and the role still appears open.\n"
        "For each job, provide a concise description, the best available posting URL, and a careers URL when discoverable.\n"
        "Include posted_at when the posting date is discoverable, otherwise leave it null. "
        "Also estimate posting_age_days when the source makes recency clear.\n"
        "Estimate distance_miles when the office location is not remote and the city/state is clear. Leave it null if unclear.\n"
        "Use source names from this set when possible: "
        "linkedin, indeed, ziprecruiter, greenhouse, lever, workday, ashby, smartrecruiters, google_jobs, company_sites.\n"
        f"Prioritize these domains when applicable: {', '.join(allowed_domains or ['official company career sites'])}.\n"
        "Deduplicate near-identical postings across sources."
    )


def minimal_candidate_profile(origin: str, radius_miles: int, salary_floor: int, location_mode: str) -> dict[str, Any]:
    return {
        "location_rules": {
            "allow_remote": location_mode in {"remote", "both"},
            "radius_miles": radius_miles,
            "origin": origin,
        },
        "salary_floor": salary_floor,
        "keywords": [],
        "target_roles": [],
        "target_industries": [],
    }


def matches_requested_location_mode(job: dict[str, Any], location_mode: str, origin: str, radius_miles: int) -> bool:
    remote_or_local = coerce_remote_or_local(str(job.get("remote_or_local")))
    if location_mode == "remote":
        return remote_or_local in REMOTE_VALUES

    candidate_profile = minimal_candidate_profile(
        origin=origin,
        radius_miles=radius_miles,
        salary_floor=0,
        location_mode="both",
    )

    if location_mode == "radius":
        return remote_or_local in LOCAL_VALUES and location_matches(job, candidate_profile)

    if remote_or_local in REMOTE_VALUES:
        return True
    return remote_or_local in LOCAL_VALUES and location_matches(job, candidate_profile)


def normalize_web_search_job(raw_job: WebSearchJob) -> dict[str, Any]:
    job = raw_job.model_dump()
    job["source"] = normalize_source_name(job.get("source"), job.get("posting_url") or job.get("careers_url"))
    job["remote_or_local"] = coerce_remote_or_local(job.get("remote_or_local"))
    job["company"] = (job.get("company") or "").strip() or None
    job["role_title"] = (job.get("role_title") or "").strip() or None
    job["location"] = (job.get("location") or "").strip() or None
    job["duplicate_key"] = build_duplicate_key(job.get("company"), job.get("role_title"), job.get("location"))
    return job


def select_job_check_url(job: dict[str, Any]) -> str | None:
    for key in ("posting_url", "careers_url"):
        value = job.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def url_has_http_scheme_and_host(url: str | None) -> bool:
    if not url:
        return False
    parsed = urllib.parse.urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def infer_availability_from_html(body: str) -> tuple[str, str]:
    normalized_body = normalize_text(body)
    for marker in UNAVAILABLE_TEXT_MARKERS:
        if marker in normalized_body:
            return "unavailable", f"page contained closed-posting marker: {marker}"
    return "available", "link loaded and no closed-posting marker was found"


def check_url_availability(url: str, *, timeout: int = JOB_LINK_CHECK_TIMEOUT_SECONDS) -> tuple[str, str, str]:
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; JobSearchAgent/1.0; +https://example.com/jobsearch-agent)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status_code = int(getattr(response, "status", response.getcode()))
            body = response.read(65536).decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as exc:
        status_code = int(exc.code)
        body = exc.read(65536).decode("utf-8", errors="ignore")
        if status_code in {401, 403, 429}:
            return "unknown", "unknown", f"link check was blocked with HTTP {status_code}"
        if status_code in {404, 410}:
            return "invalid", "unavailable", f"link returned HTTP {status_code}"
        if body:
            availability_status, reason = infer_availability_from_html(body)
            if availability_status == "unavailable":
                return "valid", "unavailable", reason
        return "invalid", "unknown", f"link returned HTTP {status_code}"
    except (TimeoutError, urllib.error.URLError, socket.timeout) as exc:
        return "unknown", "unknown", f"link check could not complete: {exc}"

    if 200 <= status_code < 400:
        availability_status, reason = infer_availability_from_html(body)
        return "valid", availability_status, reason
    if status_code in {401, 403, 429}:
        return "unknown", "unknown", f"link check was blocked with HTTP {status_code}"
    if status_code in {404, 410}:
        return "invalid", "unavailable", f"link returned HTTP {status_code}"
    return "invalid", "unknown", f"link returned HTTP {status_code}"


def verify_job_availability_impl(job: dict[str, Any]) -> dict[str, Any]:
    checked_at = datetime.now(UTC).replace(microsecond=0)
    next_check_at = checked_at + timedelta(days=AVAILABILITY_RECHECK_DAYS)
    url = select_job_check_url(job)
    if not url:
        return JobAvailabilityCheckResult(
            checked_url=None,
            link_status="missing",
            availability_status="unknown",
            checked_at=utc_timestamp(checked_at),
            next_check_at=utc_timestamp(next_check_at),
            reason="job has no posting_url or careers_url to verify",
        ).model_dump()

    if not url_has_http_scheme_and_host(url):
        return JobAvailabilityCheckResult(
            checked_url=url,
            link_status="invalid",
            availability_status="unknown",
            checked_at=utc_timestamp(checked_at),
            next_check_at=utc_timestamp(next_check_at),
            reason="job link is not a valid HTTP(S) URL",
        ).model_dump()

    link_status, availability_status, reason = check_url_availability(url)
    return JobAvailabilityCheckResult(
        checked_url=url,
        link_status=cast(Any, link_status),
        availability_status=cast(Any, availability_status),
        checked_at=utc_timestamp(checked_at),
        next_check_at=utc_timestamp(next_check_at),
        reason=reason,
    ).model_dump()


def merge_availability_check(job: dict[str, Any], check: dict[str, Any]) -> dict[str, Any]:
    merged = dict(job)
    merged["checked_url"] = check.get("checked_url")
    merged["link_check_status"] = check.get("link_status")
    merged["link_checked_at"] = check.get("checked_at")
    merged["availability_status"] = check.get("availability_status")
    merged["availability_checked_at"] = check.get("checked_at")
    merged["availability_next_check_at"] = check.get("next_check_at")
    merged["availability_notes"] = check.get("reason")
    return merged


def should_drop_for_availability(check: dict[str, Any]) -> bool:
    return check.get("link_status") in {"missing", "invalid"} or check.get("availability_status") == "unavailable"


def verify_search_result_jobs(jobs: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    kept_jobs: list[dict[str, Any]] = []
    dropped_jobs: list[dict[str, Any]] = []
    for job in jobs:
        check = verify_job_availability_impl(job)
        checked_job = merge_availability_check(job, check)
        if should_drop_for_availability(check):
            dropped_jobs.append(
                {
                    "job": describe_job(job),
                    "reasons": [str(check.get("reason") or "job link or availability check failed")],
                }
            )
            continue
        kept_jobs.append(checked_job)
    return kept_jobs, dropped_jobs


def post_process_search_results(
    jobs: list[WebSearchJob],
    *,
    keywords: list[str],
    location_mode: str,
    origin: str,
    radius_miles: int,
    salary_floor: int,
) -> tuple[list[dict[str, Any]], int]:
    processed_jobs, duplicates_skipped, _ = filter_jobs_with_reasons(
        jobs,
        keywords=keywords,
        location_mode=location_mode,
        origin=origin,
        radius_miles=radius_miles,
        salary_floor=salary_floor,
    )
    return processed_jobs, duplicates_skipped


def describe_job(job: dict[str, Any]) -> str:
    parts = [job.get("company"), job.get("role_title"), job.get("location")]
    return " / ".join(str(part).strip() for part in parts if part)


def filter_jobs_with_reasons(
    jobs: list[WebSearchJob],
    *,
    keywords: list[str],
    location_mode: str,
    origin: str,
    radius_miles: int,
    salary_floor: int,
) -> tuple[list[dict[str, Any]], int, list[dict[str, Any]]]:
    normalized_jobs = [normalize_web_search_job(job) for job in jobs]
    filtered_jobs: list[dict[str, Any]] = []
    dropped_jobs: list[dict[str, Any]] = []

    for job in normalized_jobs:
        drop_reasons: list[str] = []
        if not job_matches_keywords(job, keywords):
            drop_reasons.append("keyword mismatch")
        if not matches_requested_location_mode(job, location_mode, origin, radius_miles):
            drop_reasons.append("location mismatch")
        if not salary_meets_floor(job, salary_floor):
            drop_reasons.append("salary below floor")

        if drop_reasons:
            dropped_jobs.append(
                {
                    "job": describe_job(job),
                    "reasons": drop_reasons,
                }
            )
            continue

        filtered_jobs.append(job)

    deduped_jobs, duplicates_skipped = dedupe_jobs(filtered_jobs)
    return deduped_jobs, duplicates_skipped, dropped_jobs


def build_filter_diagnostics(
    *,
    attempts: int,
    reviewed_jobs: int,
    returned_jobs: int,
    duplicates_skipped: int,
    dropped_jobs: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "attempts": attempts,
        "jobs_reviewed": reviewed_jobs,
        "jobs_returned": returned_jobs,
        "duplicates_skipped": duplicates_skipped,
        "dropped_jobs": dropped_jobs,
    }


def perform_web_search_job_lookup(
    *,
    keywords: list[str],
    location_mode: str,
    origin: str,
    radius_miles: int,
    salary_floor: int,
    sources: list[str],
) -> WebSearchJobsResult:
    import openai as openai_module

    client = cast(Any, openai_module).OpenAI()
    tools: list[dict[str, Any]] = [
        {
            "type": "web_search_preview",
            "search_context_size": "high",
            "user_location": approximate_user_location(origin),
        }
    ]

    response = client.responses.parse(
        model=get_model_name(),
        input=build_job_search_prompt(
            keywords=keywords,
            location_mode=location_mode,
            origin=origin,
            radius_miles=radius_miles,
            salary_floor=salary_floor,
            sources=sources,
        ),
        tools=cast(Any, tools),
        text_format=WebSearchJobsResult,
        include=["web_search_call.action.sources"],
        max_output_tokens=3000,
        max_tool_calls=6,
        parallel_tool_calls=False,
    )

    if response.output_parsed:
        return response.output_parsed

    return WebSearchJobsResult()


def search_jobs_impl(
    keywords: list[str],
    location_mode: str,
    origin: str,
    radius_miles: int,
    salary_floor: int,
    sources: list[str],
) -> dict[str, Any]:
    request = build_search_request_summary(
        keywords=keywords,
        location_mode=location_mode,
        origin=origin,
        radius_miles=radius_miles,
        salary_floor=salary_floor,
        sources=sources,
    )

    try:
        selected_jobs: list[dict[str, Any]] = []
        selected_notes: list[str] = []
        selected_reviewed_jobs = 0
        selected_duplicates_skipped = 0
        selected_dropped_jobs: list[dict[str, Any]] = []
        attempt_count = 0

        for attempt_number in range(1, SEARCH_RETRY_ATTEMPTS + 1):
            parsed_result = perform_web_search_job_lookup(
                keywords=keywords,
                location_mode=location_mode,
                origin=origin,
                radius_miles=radius_miles,
                salary_floor=salary_floor,
                sources=sources,
            )
            jobs, duplicates_skipped, dropped_jobs = filter_jobs_with_reasons(
                parsed_result.jobs,
                keywords=keywords,
                location_mode=location_mode,
                origin=origin,
                radius_miles=radius_miles,
                salary_floor=salary_floor,
            )
            attempt_count = attempt_number

            if len(jobs) >= len(selected_jobs):
                selected_jobs = jobs
                selected_notes = list(parsed_result.notes)
                selected_reviewed_jobs = len(parsed_result.jobs)
                selected_duplicates_skipped = duplicates_skipped
                selected_dropped_jobs = dropped_jobs

            if jobs:
                break

        availability_dropped_jobs: list[dict[str, Any]] = []
        if selected_jobs:
            selected_jobs, availability_dropped_jobs = verify_search_result_jobs(selected_jobs)

        if attempt_count > 1 and selected_jobs:
            selected_notes.append(f"Recovered results after {attempt_count} search attempts.")
        elif attempt_count > 1 and not selected_jobs:
            selected_notes.append(f"No qualifying jobs were found after {attempt_count} search attempts.")

        if availability_dropped_jobs:
            selected_dropped_jobs = [*selected_dropped_jobs, *availability_dropped_jobs]
            drop_summaries = ", ".join(
                f"{item['job']} ({', '.join(item['reasons'])})" for item in availability_dropped_jobs[:5]
            )
            selected_notes.append(f"Filtered out jobs after link and availability checks: {drop_summaries}.")

        if not selected_jobs and selected_dropped_jobs:
            drop_summaries = ", ".join(
                f"{item['job']} ({', '.join(item['reasons'])})" for item in selected_dropped_jobs[:5]
            )
            selected_notes.append(f"Filtered out reviewed jobs: {drop_summaries}.")

        return {
            "jobs": selected_jobs,
            "implemented": True,
            "notes": selected_notes,
            "summary": {
                "jobs_reviewed": selected_reviewed_jobs,
                "jobs_returned": len(selected_jobs),
                "duplicates_skipped": selected_duplicates_skipped,
            },
            "diagnostics": build_filter_diagnostics(
                attempts=attempt_count,
                reviewed_jobs=selected_reviewed_jobs,
                returned_jobs=len(selected_jobs),
                duplicates_skipped=selected_duplicates_skipped,
                dropped_jobs=selected_dropped_jobs,
            ),
            "request": request,
        }
    except Exception as exc:
        return {
            "jobs": [],
            "implemented": False,
            "reason": f"search_jobs failed: {exc}",
            "request": request,
        }


def score_job_fit_impl(job: dict[str, Any], candidate_profile: dict[str, Any]) -> dict[str, Any]:
    return calculate_fit_score(job, candidate_profile)


@function_tool
def search_jobs(
    keywords: list[str],
    location_mode: str,
    origin: str,
    radius_miles: int,
    salary_floor: int,
    sources: list[str],
) -> dict[str, Any]:
    """Search job platforms and return normalized job postings."""
    return search_jobs_impl(
        keywords=keywords,
        location_mode=location_mode,
        origin=origin,
        radius_miles=radius_miles,
        salary_floor=salary_floor,
        sources=sources,
    )


@function_tool(strict_mode=False)
def score_job_fit(job: dict[str, Any], candidate_profile: dict[str, Any]) -> dict[str, Any]:
    """Score a normalized job posting against the candidate profile."""
    return score_job_fit_impl(job, candidate_profile)


@function_tool(strict_mode=False)
def verify_job_availability(job: dict[str, Any]) -> dict[str, Any]:
    """Check whether a job link is reachable and whether the posting still appears open."""
    return verify_job_availability_impl(job)
