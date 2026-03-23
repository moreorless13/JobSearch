from job_agent.tools.jobs import (
    WebSearchJob,
    WebSearchJobsResult,
    build_allowed_domains,
    filter_jobs_with_reasons,
    job_matches_keywords,
    matches_requested_location_mode,
    normalize_source_name,
    post_process_search_results,
    search_jobs_impl,
)


def test_build_allowed_domains_omits_filter_when_company_sites_requested() -> None:
    assert build_allowed_domains(["linkedin", "company_sites"]) is None


def test_normalize_source_name_uses_url_hint() -> None:
    assert normalize_source_name(None, "https://jobs.lever.co/acme/123") == "lever"


def test_matches_requested_location_mode_accepts_remote_for_both() -> None:
    job = {"remote_or_local": "remote", "location": "Remote - US"}
    assert matches_requested_location_mode(job, "both", "Cedar Knolls, NJ", 25) is True


def test_job_matches_keywords_requires_meaningful_overlap() -> None:
    good_job = {
        "role_title": "Solutions Engineer",
        "description": "Own API integrations for enterprise customers.",
    }
    bad_job = {
        "role_title": "Digital Strategist",
        "description": "Lead marketing campaigns and brand strategy.",
    }

    assert job_matches_keywords(good_job, ["Solutions Engineer", "API", "integrations"]) is True
    assert job_matches_keywords(bad_job, ["Solutions Engineer", "API", "integrations"]) is False


def test_post_process_search_results_filters_low_salary_and_dedupes() -> None:
    jobs = [
        WebSearchJob(
            company="Acme",
            role_title="Solutions Engineer",
            location="Remote - US",
            source="linkedin",
            posting_url="https://www.linkedin.com/jobs/view/123",
            remote_or_local="remote",
            salary_min=90000,
        ),
        WebSearchJob(
            company="Acme",
            role_title="Solutions Engineer",
            location="Remote - US",
            source="company_sites",
            careers_url="https://jobs.acme.com/roles/123",
            posting_url="https://jobs.acme.com/roles/123",
            remote_or_local="remote",
            salary_min=90000,
        ),
        WebSearchJob(
            company="BudgetCo",
            role_title="Solutions Engineer",
            location="Remote - US",
            source="indeed",
            posting_url="https://www.indeed.com/viewjob?jk=xyz",
            remote_or_local="remote",
            salary_min=50000,
        ),
    ]

    results, duplicates_skipped = post_process_search_results(
        jobs,
        keywords=["Solutions Engineer", "API", "integrations"],
        location_mode="both",
        origin="Cedar Knolls, NJ",
        radius_miles=25,
        salary_floor=65000,
    )

    assert duplicates_skipped == 1
    assert len(results) == 1
    assert results[0]["source"] == "company_sites"


def test_filter_jobs_with_reasons_returns_drop_reasons() -> None:
    jobs = [
        WebSearchJob(
            company="FarAwayCo",
            role_title="Solutions Engineer",
            location="Bellevue, WA",
            source="linkedin",
            posting_url="https://www.linkedin.com/jobs/view/999",
            remote_or_local="local",
            salary_min=120000,
        ),
        WebSearchJob(
            company="BudgetCo",
            role_title="Solutions Engineer",
            location="Remote - US",
            source="indeed",
            posting_url="https://www.indeed.com/viewjob?jk=xyz",
            remote_or_local="remote",
            salary_min=50000,
        ),
    ]

    results, duplicates_skipped, dropped_jobs = filter_jobs_with_reasons(
        jobs,
        keywords=["Solutions Engineer", "API", "integrations"],
        location_mode="both",
        origin="Cedar Knolls, NJ",
        radius_miles=25,
        salary_floor=65000,
    )

    assert results == []
    assert duplicates_skipped == 0
    assert dropped_jobs == [
        {"job": "FarAwayCo / Solutions Engineer / Bellevue, WA", "reasons": ["location mismatch"]},
        {"job": "BudgetCo / Solutions Engineer / Remote - US", "reasons": ["salary below floor"]},
    ]


def test_search_jobs_impl_retries_after_empty_first_pass(monkeypatch) -> None:
    responses = [
        WebSearchJobsResult(
            jobs=[
                WebSearchJob(
                    company="FarAwayCo",
                    role_title="Solutions Engineer",
                    location="Bellevue, WA",
                    source="linkedin",
                    posting_url="https://www.linkedin.com/jobs/view/999",
                    remote_or_local="local",
                    salary_min=120000,
                )
            ],
            notes=[],
        ),
        WebSearchJobsResult(
            jobs=[
                WebSearchJob(
                    company="Acme",
                    role_title="Solutions Engineer",
                    location="Remote - US",
                    source="linkedin",
                    posting_url="https://www.linkedin.com/jobs/view/123",
                    remote_or_local="remote",
                    salary_min=120000,
                    description="Build API integrations for enterprise customers.",
                )
            ],
            notes=[],
        ),
    ]

    monkeypatch.setattr("job_agent.tools.jobs.perform_web_search_job_lookup", lambda **_kwargs: responses.pop(0))

    result = search_jobs_impl(
        keywords=["Solutions Engineer", "API", "integrations"],
        location_mode="both",
        origin="Cedar Knolls, NJ",
        radius_miles=25,
        salary_floor=65000,
        sources=["linkedin"],
    )

    assert result["implemented"] is True
    assert result["summary"]["jobs_returned"] == 1
    assert result["diagnostics"]["attempts"] == 2
    assert "Recovered results after 2 search attempts." in result["notes"]
