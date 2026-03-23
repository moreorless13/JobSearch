from job_agent.models import WorkflowOutput
from job_agent.workflows import run_gmail_workflow, run_jobs_workflow


PROFILE = {
    "candidate_name": "James",
    "location_rules": {
        "allow_remote": True,
        "radius_miles": 25,
        "origin": "Cedar Knolls, NJ",
    },
    "salary_floor": 65000,
    "target_roles": ["Solutions Engineer", "Integration Engineer"],
    "target_industries": ["FinTech", "SaaS"],
    "keywords": ["API", "integrations", "payments"],
    "sheet_url": "https://example.com/sheet",
}


def test_run_jobs_workflow_returns_structured_jobs(monkeypatch) -> None:
    def fake_search_jobs(**_: object) -> dict:
        return {
            "implemented": True,
            "jobs": [
                {
                    "company": "Acme",
                    "role_title": "Solutions Engineer",
                    "location": "Remote - US",
                    "source": "linkedin",
                    "posting_url": "https://example.com/jobs/1",
                    "careers_url": "https://example.com/careers/1",
                    "salary_min": 90000,
                    "salary_max": 120000,
                    "salary_currency": "USD",
                    "remote_or_local": "remote",
                    "reason": "Found on company-preferred source.",
                    "duplicate_key": "acme::solutions engineer::remote us",
                }
            ],
            "summary": {"jobs_reviewed": 4, "duplicates_skipped": 1},
            "notes": [],
        }

    def fake_score_job_fit(job: dict, candidate_profile: dict) -> dict:
        assert candidate_profile["salary_floor"] == 65000
        return {
            "fit_score": 91,
            "fit_band": "excellent",
            "reason": "title aligns with target roles; 3 profile keywords matched",
        }

    def fake_upsert_tracker_row(**_: object) -> dict:
        return {"implemented": True, "status": "updated", "row": {"status": "New"}}

    monkeypatch.setattr("job_agent.workflows.search_jobs_impl", fake_search_jobs)
    monkeypatch.setattr("job_agent.workflows.score_job_fit_impl", fake_score_job_fit)
    monkeypatch.setattr("job_agent.workflows.upsert_tracker_row_impl", fake_upsert_tracker_row)

    result = run_jobs_workflow(PROFILE)

    assert isinstance(result, WorkflowOutput)
    assert result.summary.jobs_reviewed == 4
    assert result.summary.jobs_added == 1
    assert result.summary.duplicates_skipped == 1
    assert result.summary.tracker_rows_updated == 1
    assert result.new_jobs[0].company == "Acme"
    assert result.new_jobs[0].fit_score == 91
    assert result.new_jobs[0].salary == "$90,000 - $120,000"
    assert result.tracker_updates[0].status == "New"
    assert result.needs_review == []


def test_run_jobs_workflow_reports_persisted_tracker_status(monkeypatch) -> None:
    def fake_search_jobs(**_: object) -> dict:
        return {
            "implemented": True,
            "jobs": [
                {
                    "company": "Acme",
                    "role_title": "Solutions Engineer",
                    "location": "Remote",
                    "source": "linkedin",
                    "posting_url": "https://example.com/jobs/1",
                    "remote_or_local": "remote",
                    "duplicate_key": "acme::solutions engineer::remote",
                }
            ],
            "summary": {"jobs_reviewed": 1, "duplicates_skipped": 0},
            "notes": [],
        }

    monkeypatch.setattr("job_agent.workflows.search_jobs_impl", fake_search_jobs)
    monkeypatch.setattr(
        "job_agent.workflows.score_job_fit_impl",
        lambda *_args, **_kwargs: {"fit_score": 80, "fit_band": "strong", "reason": "good fit"},
    )
    monkeypatch.setattr(
        "job_agent.workflows.upsert_tracker_row_impl",
        lambda **_kwargs: {"implemented": True, "status": "updated", "row": {"status": "Interviewing"}},
    )

    result = run_jobs_workflow(PROFILE)

    assert result.summary.tracker_rows_updated == 1
    assert result.tracker_updates[0].status == "Interviewing"


def test_run_jobs_workflow_reports_tracker_stub(monkeypatch) -> None:
    def fake_search_jobs(**_: object) -> dict:
        return {
            "implemented": True,
            "jobs": [
                {
                    "company": "Acme",
                    "role_title": "Solutions Engineer",
                    "location": "Remote",
                    "source": "linkedin",
                    "posting_url": "https://example.com/jobs/1",
                    "remote_or_local": "remote",
                    "duplicate_key": "acme::solutions engineer::remote",
                }
            ],
            "summary": {"jobs_reviewed": 1, "duplicates_skipped": 0},
            "notes": [],
        }

    monkeypatch.setattr("job_agent.workflows.search_jobs_impl", fake_search_jobs)
    monkeypatch.setattr(
        "job_agent.workflows.score_job_fit_impl",
        lambda *_args, **_kwargs: {"fit_score": 80, "fit_band": "strong", "reason": "good fit"},
    )
    monkeypatch.setattr(
        "job_agent.workflows.upsert_tracker_row_impl",
        lambda **_kwargs: {"implemented": False, "reason": "sheet stub"},
    )

    result = run_jobs_workflow(PROFILE)

    assert result.summary.jobs_added == 1
    assert result.summary.tracker_rows_updated == 0
    assert len(result.needs_review) == 1
    assert result.needs_review[0].kind == "tracker_unavailable"


def test_run_jobs_workflow_does_not_sync_ignore_band_jobs(monkeypatch) -> None:
    def fake_search_jobs(**_: object) -> dict:
        return {
            "implemented": True,
            "jobs": [
                {
                    "company": "Acme",
                    "role_title": "Associate Implementation",
                    "location": "Fort Lee, NJ",
                    "source": "linkedin",
                    "posting_url": "https://example.com/jobs/1",
                    "remote_or_local": "local",
                    "duplicate_key": "acme::associate implementation::fort lee nj",
                }
            ],
            "summary": {"jobs_reviewed": 1, "duplicates_skipped": 0},
            "notes": [],
        }

    upsert_calls: list[dict] = []

    monkeypatch.setattr("job_agent.workflows.search_jobs_impl", fake_search_jobs)
    monkeypatch.setattr(
        "job_agent.workflows.score_job_fit_impl",
        lambda *_args, **_kwargs: {"fit_score": 45, "fit_band": "ignore", "reason": "weak fit"},
    )
    monkeypatch.setattr(
        "job_agent.workflows.upsert_tracker_row_impl",
        lambda **kwargs: upsert_calls.append(kwargs) or {"implemented": True, "status": "inserted", "row": {"status": "New"}},
    )

    result = run_jobs_workflow(PROFILE)

    assert result.summary.jobs_added == 1
    assert result.summary.tracker_rows_updated == 0
    assert result.tracker_updates == []
    assert upsert_calls == []


def test_run_gmail_workflow_reports_stub(monkeypatch) -> None:
    monkeypatch.setattr(
        "job_agent.workflows.search_gmail_job_updates_impl",
        lambda **_kwargs: {"implemented": False, "reason": "gmail stub", "messages": []},
    )

    result = run_gmail_workflow(PROFILE)

    assert result.summary.gmail_updates_processed == 0
    assert len(result.needs_review) == 1
    assert result.needs_review[0].kind == "gmail_unavailable"
