from job_agent.tools.dedupe import build_duplicate_key, dedupe_jobs


def test_build_duplicate_key_normalizes_values() -> None:
    duplicate_key = build_duplicate_key("Acme, Inc.", "Solutions Engineer", "Remote - US")
    assert duplicate_key == "acme inc::solutions engineer::remote us"


def test_dedupe_prefers_job_with_careers_url() -> None:
    jobs = [
        {
            "company": "Acme",
            "role_title": "Solutions Engineer",
            "location": "Remote",
            "source": "linkedin",
            "posting_url": "https://linkedin.example/job/1",
        },
        {
            "company": "Acme",
            "role_title": "Solutions Engineer",
            "location": "Remote",
            "source": "company_sites",
            "posting_url": "https://jobs.acme.com/roles/1",
            "careers_url": "https://jobs.acme.com/roles/1",
        },
    ]

    deduped, duplicates_skipped = dedupe_jobs(jobs)

    assert duplicates_skipped == 1
    assert len(deduped) == 1
    assert deduped[0]["source"] == "company_sites"
