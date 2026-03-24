from __future__ import annotations

from datetime import UTC, datetime

from job_agent.models import WorkflowOutput
from job_agent.state import NullStateStore, StateStoreStatus, build_default_goal_state, build_default_strategy_snapshot
from job_agent.workflows import run_gmail_workflow, run_jobs_workflow, run_preset_workflow, run_reflect_workflow


PROFILE = {
    "candidate_name": "James",
    "location_rules": {
        "allow_remote": True,
        "radius_miles": 25,
        "origin": "Cedar Knolls, NJ",
    },
    "salary_floor": 65000,
    "top_level_objective": "maximize qualified interview probability within 30 days while respecting salary, location, and role constraints",
    "company_priorities": {},
    "decision_thresholds": {
        "prioritize": 85,
        "track": 70,
        "queue_review": 60,
        "stale_days": 21,
    },
    "target_roles": ["Solutions Engineer", "Integration Engineer"],
    "target_industries": ["FinTech", "SaaS"],
    "keywords": ["API", "integrations", "payments"],
    "sheet_url": "https://example.com/sheet",
}


class FakeStateStore:
    def __init__(self) -> None:
        self.status = StateStoreStatus(available=True, degraded_reason=None)
        self.goal_state = build_default_goal_state(PROFILE)
        self.strategy_snapshot = build_default_strategy_snapshot(PROFILE)
        self.decisions = []
        self.outcomes = []
        self.follow_up_tasks = []
        self.plan_runs = []

    def ensure_goal_state(self, _candidate_profile):
        return self.goal_state

    def save_goal_state(self, goal_state):
        self.goal_state = goal_state

    def get_strategy_snapshot(self, _candidate_profile):
        return self.strategy_snapshot

    def save_strategy_snapshot(self, snapshot):
        self.strategy_snapshot = snapshot

    def save_plan_run(self, plan_run):
        self.plan_runs.append(plan_run)

    def append_decision(self, decision):
        self.decisions.append(decision)

    def append_outcome(self, event):
        self.outcomes.append(event)

    def list_decisions(self, *, lookback_days=None):
        return list(self.decisions)

    def list_outcomes(self, *, lookback_days=None):
        return list(self.outcomes)

    def list_follow_up_tasks(self):
        return [task for task in self.follow_up_tasks if task.status == "planned"]

    def save_follow_up_task(self, task):
        if task.duplicate_key and any(existing.duplicate_key == task.duplicate_key for existing in self.follow_up_tasks):
            return
        self.follow_up_tasks.append(task)

    def mark_follow_up_completed(self, duplicate_key):
        for task in self.follow_up_tasks:
            if task.duplicate_key == duplicate_key:
                task.status = "completed"


def patch_store(monkeypatch, store) -> None:
    monkeypatch.setattr("job_agent.orchestrator.RedisStateStore.from_env", lambda: store)


def test_run_jobs_workflow_uses_decision_engine_and_tracker_sync(monkeypatch) -> None:
    store = FakeStateStore()
    patch_store(monkeypatch, store)

    monkeypatch.setattr(
        "job_agent.orchestrator.search_jobs_impl",
        lambda **_kwargs: {
            "implemented": True,
            "jobs": [
                {
                    "company": "Acme",
                    "role_title": "Solutions Engineer",
                    "location": "Remote - US",
                    "source": "company_sites",
                    "posting_url": "https://example.com/jobs/1",
                    "careers_url": "https://example.com/careers/1",
                    "remote_or_local": "remote",
                    "posting_age_days": 3,
                    "duplicate_key": "acme::solutions engineer::remote us",
                },
                {
                    "company": "Beta",
                    "role_title": "Integration Engineer",
                    "location": "Remote - US",
                    "source": "linkedin",
                    "posting_url": "",
                    "remote_or_local": "remote",
                    "posting_age_days": 10,
                    "duplicate_key": "beta::integration engineer::remote us",
                },
            ],
            "summary": {"jobs_reviewed": 4, "duplicates_skipped": 1},
            "notes": [],
        },
    )

    def fake_score_job_fit(job, _candidate_profile):
        if job["company"] == "Acme":
            return {"fit_score": 82, "fit_band": "strong", "reason": "strong fit"}
        return {"fit_score": 64, "fit_band": "maybe", "reason": "borderline fit"}

    monkeypatch.setattr("job_agent.orchestrator.score_job_fit_impl", fake_score_job_fit)
    monkeypatch.setattr(
        "job_agent.orchestrator.upsert_tracker_row_impl",
        lambda **_kwargs: {"implemented": True, "status": "updated", "row": {"status": "New"}},
    )

    result = run_jobs_workflow(PROFILE)

    assert isinstance(result, WorkflowOutput)
    assert result.summary.jobs_reviewed == 4
    assert result.summary.jobs_added == 2
    assert result.summary.tracker_rows_updated == 1
    assert len(result.new_jobs) == 2
    assert {decision.action for decision in store.decisions} == {"prioritize", "queue_review"}
    assert any(item.kind == "job_requires_review" for item in result.needs_review)


def test_run_jobs_workflow_degrades_when_state_store_unavailable(monkeypatch) -> None:
    patch_store(monkeypatch, NullStateStore("redis unavailable"))
    monkeypatch.setattr(
        "job_agent.orchestrator.search_jobs_impl",
        lambda **_kwargs: {"implemented": True, "jobs": [], "summary": {"jobs_reviewed": 0, "duplicates_skipped": 0}, "notes": []},
    )

    result = run_jobs_workflow(PROFILE)

    assert any(item.kind == "state_store_unavailable" for item in result.needs_review)


def test_run_gmail_workflow_records_outcomes_and_immediate_review(monkeypatch) -> None:
    store = FakeStateStore()
    patch_store(monkeypatch, store)
    monkeypatch.setattr(
        "job_agent.orchestrator.search_gmail_job_updates_impl",
        lambda **_kwargs: {
            "implemented": True,
            "messages": [
                {
                    "subject": "Interview availability for Solutions Engineer role",
                    "from": "recruiting@acme.com",
                    "body": "Please share your availability so we can schedule time this week.",
                    "snippet": "Please share your availability",
                    "date": "Mon, 01 Jan 2026 09:00:00 -0500",
                }
            ],
        },
    )
    monkeypatch.setattr(
        "job_agent.orchestrator.read_tracker_sheet_impl",
        lambda _sheet_url: {
            "implemented": True,
            "rows": [
                {
                    "company": "Acme",
                    "role_title": "Solutions Engineer",
                    "status": "Applied",
                    "source": "greenhouse",
                    "duplicate_key": "acme::solutions engineer::remote",
                }
            ],
        },
    )
    monkeypatch.setattr(
        "job_agent.orchestrator.upsert_tracker_row_impl",
        lambda **_kwargs: {
            "implemented": True,
            "status": "updated",
            "row": {
                "company": "Acme",
                "role_title": "Solutions Engineer",
                "status": "Interview Requested",
                "duplicate_key": "acme::solutions engineer::remote",
            },
        },
    )

    result = run_gmail_workflow(PROFILE)

    assert result.summary.gmail_updates_processed == 1
    assert result.summary.tracker_rows_updated == 1
    assert len(store.outcomes) == 1
    assert store.outcomes[0].event_type == "interview_request"
    assert any(item.kind == "gmail_action_required" for item in result.needs_review)


def test_run_reflect_workflow_updates_strategy_weights(monkeypatch) -> None:
    store = FakeStateStore()
    store.decisions.extend(
        [
            type("Decision", (), {"role_title": "Solutions Engineer", "source": "greenhouse", "industry": "FinTech"})(),
            type("Decision", (), {"role_title": "Solutions Engineer", "source": "greenhouse", "industry": "FinTech"})(),
            type("Decision", (), {"role_title": "Integration Engineer", "source": "linkedin", "industry": "SaaS"})(),
        ]
    )
    store.outcomes.extend(
        [
            type("Outcome", (), {"role_title": "Solutions Engineer", "source": "greenhouse", "industry": "FinTech", "event_type": "positive_signal"})(),
            type("Outcome", (), {"role_title": "Solutions Engineer", "source": "greenhouse", "industry": "FinTech", "event_type": "interview_request"})(),
        ]
    )
    patch_store(monkeypatch, store)
    monkeypatch.setattr("job_agent.orchestrator.read_tracker_sheet_impl", lambda _sheet_url: {"implemented": True, "rows": []})

    result = run_reflect_workflow(PROFILE)

    assert "role solutions engineer" in (result.assistant_response or "")
    assert store.strategy_snapshot.role_weights["solutions engineer"] > 0
    assert store.strategy_snapshot.source_weights["greenhouse"] > 0


def test_run_preset_workflow_daily_merges_outputs(monkeypatch) -> None:
    store = FakeStateStore()
    patch_store(monkeypatch, store)
    monkeypatch.setattr(
        "job_agent.orchestrator.search_jobs_impl",
        lambda **_kwargs: {
            "implemented": True,
            "jobs": [
                {
                    "company": "Acme",
                    "role_title": "Solutions Engineer",
                    "location": "Remote",
                    "source": "company_sites",
                    "posting_url": "https://example.com/jobs/1",
                    "remote_or_local": "remote",
                    "posting_age_days": 1,
                    "duplicate_key": "acme::solutions engineer::remote",
                }
            ],
            "summary": {"jobs_reviewed": 1, "duplicates_skipped": 0},
            "notes": [],
        },
    )
    monkeypatch.setattr(
        "job_agent.orchestrator.score_job_fit_impl",
        lambda *_args, **_kwargs: {"fit_score": 82, "fit_band": "strong", "reason": "good fit"},
    )
    monkeypatch.setattr(
        "job_agent.orchestrator.search_gmail_job_updates_impl",
        lambda **_kwargs: {"implemented": True, "messages": []},
    )
    monkeypatch.setattr(
        "job_agent.orchestrator.read_tracker_sheet_impl",
        lambda _sheet_url: {"implemented": True, "rows": []},
    )
    monkeypatch.setattr(
        "job_agent.orchestrator.upsert_tracker_row_impl",
        lambda **_kwargs: {"implemented": True, "status": "updated", "row": {"status": "New"}},
    )

    result = run_preset_workflow("daily", PROFILE)

    assert result.summary.jobs_added == 1
    assert result.summary.gmail_updates_processed == 0
    assert result.summary.tracker_rows_updated == 1
    assert result.assistant_response is not None
