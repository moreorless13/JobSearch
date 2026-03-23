from __future__ import annotations

import re
from typing import Any


SOURCE_PRIORITY = {
    "company_sites": 0,
    "greenhouse": 1,
    "lever": 2,
    "ashby": 3,
    "smartrecruiters": 4,
    "workday": 5,
    "linkedin": 6,
    "indeed": 7,
    "ziprecruiter": 8,
    "google_jobs": 9,
}


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    value = value.casefold()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def build_duplicate_key(company: str | None, role_title: str | None, location: str | None) -> str:
    parts = [normalize_text(company), normalize_text(role_title), normalize_text(location)]
    return "::".join(parts)


def choose_preferred_job(existing: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    existing_priority = SOURCE_PRIORITY.get(str(existing.get("source", "")).lower(), 100)
    candidate_priority = SOURCE_PRIORITY.get(str(candidate.get("source", "")).lower(), 100)

    existing_has_careers_url = bool(existing.get("careers_url"))
    candidate_has_careers_url = bool(candidate.get("careers_url"))

    if candidate_has_careers_url and not existing_has_careers_url:
        return candidate
    if existing_has_careers_url and not candidate_has_careers_url:
        return existing
    if candidate_priority < existing_priority:
        return candidate
    return existing


def dedupe_jobs(jobs: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    deduped: dict[str, dict[str, Any]] = {}
    duplicates_skipped = 0

    for job in jobs:
        duplicate_key = job.get("duplicate_key") or build_duplicate_key(
            job.get("company"),
            job.get("role_title"),
            job.get("location"),
        )
        job["duplicate_key"] = duplicate_key

        if duplicate_key in deduped:
            duplicates_skipped += 1
            deduped[duplicate_key] = choose_preferred_job(deduped[duplicate_key], job)
        else:
            deduped[duplicate_key] = job

    return list(deduped.values()), duplicates_skipped
