from __future__ import annotations

from typing import Any, Literal, cast

import pydantic as pydantic_module

from job_agent.config import get_model_name
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


def calculate_fit_score(job: dict[str, Any], candidate_profile: dict[str, Any]) -> dict[str, Any]:
    title = normalize_text(job.get("role_title"))
    company = job.get("company")
    keywords = {normalize_text(keyword) for keyword in candidate_profile.get("keywords", [])}
    target_roles = {normalize_text(role) for role in candidate_profile.get("target_roles", [])}
    industries = {normalize_text(industry) for industry in candidate_profile.get("target_industries", [])}

    title_score = 35 if any(target_role in title for target_role in target_roles if target_role) else 0
    keyword_hits = sum(1 for keyword in keywords if keyword and keyword in normalize_text(job.get("description", "")))
    keyword_score = min(keyword_hits * 4, 28)
    industry_score = 12 if normalize_text(job.get("industry")) in industries else 0
    location_score = 15 if location_matches(job, candidate_profile) else 0
    salary_score = 10 if salary_meets_floor(job, candidate_profile["salary_floor"]) else 0

    score = min(title_score + keyword_score + industry_score + location_score + salary_score, 100)
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
    if not reasons:
        reasons.append("weak alignment against the current profile")

    return {
        "company": company,
        "role_title": job.get("role_title"),
        "fit_score": score,
        "fit_band": fit_band(score),
        "reason": "; ".join(reasons),
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

        if attempt_count > 1 and selected_jobs:
            selected_notes.append(f"Recovered results after {attempt_count} search attempts.")
        elif attempt_count > 1 and not selected_jobs:
            selected_notes.append(f"No qualifying jobs were found after {attempt_count} search attempts.")

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
