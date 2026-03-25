from __future__ import annotations

from datetime import UTC, datetime

from job_agent.docs.models import DocumentationStateSnapshot
from job_agent.docs.service import DocumentationService
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
    "qa": {
        "approve_threshold": 0.8,
        "flag_threshold": 0.6,
        "llm_judge_enabled": False,
        "duplicate_company_cooldown_days": 7,
    },
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
        self.qa_evaluations = []
        self.documentation_events = []
        self.documentation_state = None

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

    def append_qa_evaluation(self, evaluation):
        self.qa_evaluations.append(evaluation)

    def list_qa_evaluations(self, *, lookback_days=None):
        return list(self.qa_evaluations)

    def append_documentation_event(self, event):
        self.documentation_events.append(event)

    def list_documentation_events(self, *, lookback_days=None):
        return list(self.documentation_events)

    def get_documentation_state(self):
        return self.documentation_state

    def save_documentation_state(self, snapshot):
        self.documentation_state = snapshot


def patch_store(monkeypatch, store) -> None:
    monkeypatch.setattr("job_agent.orchestrator.RedisStateStore.from_env", lambda: store)


def patch_docs_service(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "job_agent.orchestrator.DocumentationService",
        lambda **kwargs: DocumentationService(**kwargs, root_dir=tmp_path),
    )


def test_run_jobs_workflow_uses_decision_engine_and_tracker_sync(monkeypatch, tmp_path) -> None:
    store = FakeStateStore()
    patch_store(monkeypatch, store)
    patch_docs_service(monkeypatch, tmp_path)
    monkeypatch.setattr("job_agent.orchestrator.read_tracker_sheet_impl", lambda _sheet_url: {"implemented": True, "rows": []})

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
    assert result.summary.qa_evaluations == 2
    assert len(result.new_jobs) == 2
    assert len(result.documentation_updates) == 5
    assert {decision.action for decision in store.decisions} == {"prioritize", "queue_review"}
    assert any(item.kind == "job_requires_review" for item in result.needs_review)
    assert isinstance(store.documentation_state, DocumentationStateSnapshot)

def test_run_jobs_workflow_degrades_when_state_store_unavailable(monkeypatch, tmp_path) -> None:
    patch_store(monkeypatch, NullStateStore("redis unavailable"))
    patch_docs_service(monkeypatch, tmp_path)
    monkeypatch.setattr("job_agent.orchestrator.read_tracker_sheet_impl", lambda _sheet_url: {"implemented": False, "reason": "tracker unavailable"})
    monkeypatch.setattr(
        "job_agent.orchestrator.search_jobs_impl",
        lambda **_kwargs: {"implemented": True, "jobs": [], "summary": {"jobs_reviewed": 0, "duplicates_skipped": 0}, "notes": []},
    )

    result = run_jobs_workflow(PROFILE)

    assert any(item.kind == "state_store_unavailable" for item in result.needs_review)
    assert len(result.documentation_updates) == 5

def test_run_gmail_workflow_records_outcomes_and_immediate_review(monkeypatch, tmp_path) -> None:
    store = FakeStateStore()
    patch_store(monkeypatch, store)
    patch_docs_service(monkeypatch, tmp_path)
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
    assert result.summary.qa_evaluations == 1
    assert len(store.outcomes) == 1
    assert len(result.documentation_updates) == 5
    assert store.outcomes[0].event_type == "interview_request"
    assert any(item.kind == "gmail_action_required" for item in result.needs_review)

def test_run_reflect_workflow_updates_strategy_weights(monkeypatch, tmp_path) -> None:
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
    patch_docs_service(monkeypatch, tmp_path)
    monkeypatch.setattr("job_agent.orchestrator.read_tracker_sheet_impl", lambda _sheet_url: {"implemented": True, "rows": []})

    result = run_reflect_workflow(PROFILE)

    assert result.summary.qa_evaluations == 1
    assert len(result.documentation_updates) == 5
    assert "role solutions engineer" in (result.assistant_response or "")
    assert store.strategy_snapshot.role_weights["solutions engineer"] > 0
    assert store.strategy_snapshot.source_weights["greenhouse"] > 0


def test_run_preset_workflow_daily_merges_outputs(monkeypatch, tmp_path) -> None:
    store = FakeStateStore()
    patch_store(monkeypatch, store)
    patch_docs_service(monkeypatch, tmp_path)
    monkeypatch.setattr("job_agent.orchestrator.read_tracker_sheet_impl", lambda _sheet_url: {"implemented": True, "rows": []})
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

    assert result.summary.qa_evaluations == 2
    assert len(result.qa_results) == 2
    assert len(result.documentation_updates) == 5
    assert len(store.documentation_events) == 0


def test_run_jobs_workflow_rejects_duplicate_tracker_row_before_sheet_write(monkeypatch, tmp_path) -> None:
    store = FakeStateStore()
    patch_store(monkeypatch, store)
    patch_docs_service(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "job_agent.orchestrator.read_tracker_sheet_impl",
        lambda _sheet_url: {
            "implemented": True,
            "rows": [
                {
                    "company": "Acme",
                    "role_title": "Solutions Engineer",
                    "location": "Remote - US",
                    "duplicate_key": "acme::solutions engineer::remote us",
                    "date_added": "2026-01-01",
                }
            ],
        },
    )
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
                    "remote_or_local": "remote",
                    "posting_age_days": 1,
                    "duplicate_key": "acme::solutions engineer::remote us",
                }
            ],
            "summary": {"jobs_reviewed": 1, "duplicates_skipped": 0},
            "notes": [],
        },
    )
    monkeypatch.setattr(
        "job_agent.orchestrator.score_job_fit_impl",
        lambda *_args, **_kwargs: {"fit_score": 90, "fit_band": "excellent", "reason": "great fit"},
    )

    writes: list[dict] = []
    monkeypatch.setattr(
        "job_agent.orchestrator.upsert_tracker_row_impl",
        lambda **kwargs: writes.append(kwargs) or {"implemented": True, "status": "updated", "row": {"status": "New"}},
    )

    result = run_jobs_workflow(PROFILE)

    assert result.summary.jobs_added == 0
    assert result.summary.tracker_rows_updated == 0
    assert writes == []
    assert result.summary.qa_rejected == 1
    assert any(item.kind == "qa_reject" for item in result.needs_review)


def test_run_gmail_workflow_blocks_unclear_email_mutations(monkeypatch, tmp_path) -> None:
    store = FakeStateStore()
    patch_store(monkeypatch, store)
    patch_docs_service(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "job_agent.orchestrator.search_gmail_job_updates_impl",
        lambda **_kwargs: {
            "implemented": True,
            "messages": [
                {
                    "id": "msg_1",
                    "subject": "Quick question",
                    "from": "unknown@example.com",
                    "body": "Checking in.",
                    "snippet": "Checking in.",
                    "date": "Mon, 01 Jan 2026 09:00:00 -0500",
                }
            ],
        },
    )
    monkeypatch.setattr("job_agent.orchestrator.read_tracker_sheet_impl", lambda _sheet_url: {"implemented": True, "rows": []})

    writes: list[dict] = []
    monkeypatch.setattr(
        "job_agent.orchestrator.upsert_tracker_row_impl",
        lambda **kwargs: writes.append(kwargs) or {"implemented": True, "status": "updated", "row": kwargs["row"]},
    )

    result = run_gmail_workflow(PROFILE)

    assert result.summary.gmail_updates_processed == 1
    assert result.summary.tracker_rows_updated == 0
    assert writes == []
    assert store.outcomes == []
    assert result.summary.qa_flagged == 1
    assert any(item.kind == "qa_flag" for item in result.needs_review)


def test_run_reflect_workflow_blocks_persistence_without_evidence(monkeypatch, tmp_path) -> None:
    store = FakeStateStore()
    original_snapshot = store.strategy_snapshot.model_copy(deep=True)
    patch_store(monkeypatch, store)
    patch_docs_service(monkeypatch, tmp_path)
    monkeypatch.setattr("job_agent.orchestrator.read_tracker_sheet_impl", lambda _sheet_url: {"implemented": True, "rows": []})

    result = run_reflect_workflow(PROFILE)

    assert result.summary.qa_flagged == 1
    assert store.strategy_snapshot == original_snapshot
    assert any(item.kind == "qa_flag" for item in result.needs_review)
