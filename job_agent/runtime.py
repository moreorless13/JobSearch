from __future__ import annotations

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

STALE_POSTING_DAYS = 21
FOLLOW_UP_DAYS = 3
