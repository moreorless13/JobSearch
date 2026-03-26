from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from job_agent.docs.models import DocumentationEvent, DocumentationStateSnapshot
from job_agent.state import (
    DecisionRecord,
    FollowUpTask,
    GoalState,
    OutcomeEvent,
    PlanRun,
    QAEvaluationRecord,
    StateStore,
    StrategySnapshot,
    build_default_strategy_snapshot,
    isoformat,
)


PROFILE: dict[str, Any] = {
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


class FakeStateStore(StateStore):
    def __init__(self) -> None:
        super().__init__(available=True, degraded_reason=None)
        self.documentation_events: list[DocumentationEvent] = []
        self.documentation_state: DocumentationStateSnapshot | None = None
        self.decisions: list[DecisionRecord] = []
        self.outcomes: list[OutcomeEvent] = []
        self.qa_evaluations: list[QAEvaluationRecord] = []

    def append_documentation_event(self, event: DocumentationEvent) -> None:
        self.documentation_events.append(event)

    def list_documentation_events(self, *, lookback_days: int | None = None) -> list[DocumentationEvent]:
        return list(self.documentation_events)

    def get_documentation_state(self) -> DocumentationStateSnapshot | None:
        return self.documentation_state

    def save_documentation_state(self, snapshot: DocumentationStateSnapshot) -> None:
        self.documentation_state = snapshot

    def list_decisions(self, *, lookback_days: int | None = None) -> list[DecisionRecord]:
        return list(self.decisions)

    def list_outcomes(self, *, lookback_days: int | None = None) -> list[OutcomeEvent]:
        return list(self.outcomes)

    def list_qa_evaluations(self, *, lookback_days: int | None = None) -> list[QAEvaluationRecord]:
        return list(self.qa_evaluations)

    def ensure_goal_state(self, candidate_profile: dict[str, Any]) -> GoalState | None:
        return None

    def save_goal_state(self, goal_state: GoalState) -> None:
        return None

    def get_strategy_snapshot(self, candidate_profile: dict[str, Any]) -> StrategySnapshot | None:
        return None

    def save_strategy_snapshot(self, snapshot: StrategySnapshot) -> None:
        return None

    def save_plan_run(self, plan_run: PlanRun) -> None:
        return None

    def append_decision(self, decision: DecisionRecord) -> None:
        self.decisions.append(decision)

    def append_outcome(self, event: OutcomeEvent) -> None:
        self.outcomes.append(event)

    def list_follow_up_tasks(self) -> list[FollowUpTask]:
        return []

    def save_follow_up_task(self, task: FollowUpTask) -> None:
        return None

    def mark_follow_up_completed(self, duplicate_key: str | None) -> None:
        return None

    def append_qa_evaluation(self, evaluation: QAEvaluationRecord) -> None:
        self.qa_evaluations.append(evaluation)


def write_surface_files(prompts_dir: Path, schemas_dir: Path, *, prompt_body: str, schema_body: str) -> None:
    prompts_dir.mkdir(parents=True, exist_ok=True)
    schemas_dir.mkdir(parents=True, exist_ok=True)
    (prompts_dir / "coordinator.txt").write_text(prompt_body, encoding="utf-8")
    (schemas_dir / "normalized_job.json").write_text(schema_body, encoding="utf-8")


def load_docs_service() -> Any:
    import job_agent.docs.service as docs_service

    return cast(Any, docs_service)


def test_documentation_service_emits_manifest_change_events_for_policy_prompt_and_schema_updates(tmp_path: Path) -> None:
    docs_service = load_docs_service()
    store = FakeStateStore()
    prompts_dir = tmp_path / "prompts"
    schemas_dir = tmp_path / "schemas"
    write_surface_files(prompts_dir, schemas_dir, prompt_body="baseline prompt", schema_body='{"type":"object"}')

    service = docs_service.DocumentationService(
        candidate_profile=PROFILE,
        state_store=store,
        strategy_snapshot=build_default_strategy_snapshot(PROFILE),
        root_dir=tmp_path,
        prompts_dir=prompts_dir,
        schemas_dir=schemas_dir,
    )
    initial_updates = service.refresh(workflow="jobs")

    assert len(initial_updates) == 5
    assert store.documentation_state is not None
    assert store.documentation_events == []

    updated_profile = {
        **PROFILE,
        "decision_thresholds": {**PROFILE["decision_thresholds"], "prioritize": 90},
        "qa": {**PROFILE["qa"], "approve_threshold": 0.85},
    }
    write_surface_files(prompts_dir, schemas_dir, prompt_body="updated prompt", schema_body='{"type":"object","title":"Job"}')
    service = docs_service.DocumentationService(
        candidate_profile=updated_profile,
        state_store=store,
        strategy_snapshot=build_default_strategy_snapshot(updated_profile),
        root_dir=tmp_path,
        prompts_dir=prompts_dir,
        schemas_dir=schemas_dir,
    )
    service.refresh(workflow="reflect")

    event_types = {event.event_type for event in store.documentation_events}
    assert "decision_policy_changed" in event_types
    assert "qa_policy_changed" in event_types
    assert "prompt_changed" in event_types
    assert "schema_changed" in event_types


def test_documentation_service_is_idempotent_when_nothing_changes(tmp_path: Path) -> None:
    docs_service = load_docs_service()
    store = FakeStateStore()
    prompts_dir = tmp_path / "prompts"
    schemas_dir = tmp_path / "schemas"
    write_surface_files(prompts_dir, schemas_dir, prompt_body="baseline prompt", schema_body='{"type":"object"}')

    service = docs_service.DocumentationService(
        candidate_profile=PROFILE,
        state_store=store,
        strategy_snapshot=build_default_strategy_snapshot(PROFILE),
        root_dir=tmp_path,
        prompts_dir=prompts_dir,
        schemas_dir=schemas_dir,
    )
    first = service.refresh(workflow="jobs")
    second = service.refresh(workflow="jobs")

    assert any(update.update_type == "created" for update in first)
    assert all(update.update_type == "unchanged" for update in second)
    assert len(store.documentation_events) == 0


def test_explain_service_answers_change_and_rejection_questions(tmp_path: Path) -> None:
    docs_service = load_docs_service()
    store = FakeStateStore()
    prompts_dir = tmp_path / "prompts"
    schemas_dir = tmp_path / "schemas"
    write_surface_files(prompts_dir, schemas_dir, prompt_body="baseline prompt", schema_body='{"type":"object"}')

    service = docs_service.DocumentationService(
        candidate_profile=PROFILE,
        state_store=store,
        strategy_snapshot=build_default_strategy_snapshot(PROFILE),
        root_dir=tmp_path,
        prompts_dir=prompts_dir,
        schemas_dir=schemas_dir,
    )
    service.refresh(workflow="jobs")

    updated_profile = {
        **PROFILE,
        "decision_thresholds": {**PROFILE["decision_thresholds"], "track": 75},
    }
    service = docs_service.DocumentationService(
        candidate_profile=updated_profile,
        state_store=store,
        strategy_snapshot=build_default_strategy_snapshot(updated_profile),
        root_dir=tmp_path,
        prompts_dir=prompts_dir,
        schemas_dir=schemas_dir,
    )
    service.refresh(workflow="reflect")

    store.decisions.append(
        DecisionRecord(
            decision_id="decision_1",
            timestamp=isoformat(datetime(2026, 1, 12, tzinfo=UTC)),
            workflow="jobs",
            company="Acme",
            role_title="Solutions Engineer",
            role_slug="solutions engineer",
            source="linkedin",
            action="skip",
            final_score=58,
            base_fit_score=58,
            rationale="Below the tracking threshold after deterministic scoring.",
        )
    )

    explain = docs_service.ExplainService(
        candidate_profile=updated_profile,
        state_store=store,
        strategy_snapshot=build_default_strategy_snapshot(updated_profile),
        root_dir=tmp_path,
    )
    recent_changes = explain.explain("What changed this week?")
    system_answer = explain.explain("How does job application work?")
    rejection_answer = explain.explain("Why was this job rejected?")

    assert "Recent behavior changes" in recent_changes.answer
    assert recent_changes.citations
    assert "The system runs four preset workflows" in system_answer.answer
    assert "most recent skipped job" in rejection_answer.answer
    assert rejection_answer.citations[0].reference == "decision_1"