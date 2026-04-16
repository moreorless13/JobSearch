from __future__ import annotations

from datetime import UTC, datetime

from job_agent.models import JobRecord
from job_agent.docs.models import BehaviorManifest, DocumentationEvent, DocumentationStateSnapshot
from job_agent.orchestrator import (
    build_tracker_row_from_job,
    decide_job_action,
    due_follow_up_datetime,
    reflect_strategy,
    should_tailor_resume,
    tracker_due_follow_ups,
)
from job_agent.state import (
    ACTIVE_GOAL_KEY,
    CURRENT_STRATEGY_KEY,
    DecisionRecord,
    DOCUMENTATION_EVENTS_KEY,
    DOCUMENTATION_STATE_KEY,
    GoalState,
    OutcomeEvent,
    QAEvaluationRecord,
    QA_EVALUATIONS_KEY,
    RedisStateStore,
    StrategySnapshot,
    build_default_goal_state,
    build_default_strategy_snapshot,
    isoformat,
)


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


class FakePipeline:
    def __init__(self, client):
        self.client = client
        self.commands = []

    def delete(self, key):
        self.commands.append(("delete", key))
        return self

    def rpush(self, key, value):
        self.commands.append(("rpush", key, value))
        return self

    def ltrim(self, key, start, stop):
        self.commands.append(("ltrim", key, start, stop))
        return self

    def execute(self):
        for command in self.commands:
            if command[0] == "delete":
                self.client.delete(command[1])
            elif command[0] == "rpush":
                self.client.rpush(command[1], command[2])
            elif command[0] == "ltrim":
                self.client.ltrim(command[1], command[2], command[3])


class FakeRedisClient:
    def __init__(self) -> None:
        self.values = {}
        self.lists = {}

    def ping(self):
        return True

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value):
        self.values[key] = value

    def lpush(self, key, value):
        self.lists.setdefault(key, []).insert(0, value)

    def ltrim(self, key, start, stop):
        self.lists[key] = self.lists.get(key, [])[start : stop + 1]

    def lrange(self, key, start, stop):
        items = self.lists.get(key, [])
        if stop == -1:
            return items[start:]
        return items[start : stop + 1]

    def delete(self, key):
        self.lists[key] = []

    def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)

    def pipeline(self):
        return FakePipeline(self)


def test_redis_state_store_seeds_goal_and_strategy() -> None:
    client = FakeRedisClient()
    store = RedisStateStore(client)

    goal_state = store.ensure_goal_state(PROFILE)
    strategy = store.get_strategy_snapshot(PROFILE)

    assert goal_state is not None
    assert strategy is not None
    assert ACTIVE_GOAL_KEY in client.values
    assert CURRENT_STRATEGY_KEY in client.values
    assert goal_state.objective == PROFILE["top_level_objective"]


def test_redis_state_store_persists_qa_evaluations() -> None:
    client = FakeRedisClient()
    store = RedisStateStore(client)

    store.append_qa_evaluation(
        QAEvaluationRecord(
            evaluation_id="qa_1",
            timestamp=isoformat(datetime(2026, 1, 10, tzinfo=UTC)),
            workflow="jobs",
            event_type="JOB_FOUND",
            stage="pre_action",
            entity_key="acme::solutions engineer::remote",
            verdict="flag",
            score=0.72,
            approve_threshold=0.8,
            flag_threshold=0.6,
            blocked_action="tracker_sync",
            recommended_action="manual_review",
            reasons=["duplicate cooldown"],
            score_breakdown={"role_match": 0.3},
        )
    )

    stored = store.list_qa_evaluations()

    assert QA_EVALUATIONS_KEY in client.lists
    assert len(stored) == 1
    assert stored[0].verdict == "flag"


def test_redis_state_store_persists_documentation_state_and_events() -> None:
    client = FakeRedisClient()
    store = RedisStateStore(client)

    event = DocumentationEvent(
        event_id="doc_evt_1",
        timestamp=isoformat(datetime(2026, 1, 12, tzinfo=UTC)),
        event_type="decision_policy_changed",
        summary="Updated decision thresholds.",
        reason="Changed fields: thresholds.",
        impact="Job scoring changed.",
    )
    snapshot = DocumentationStateSnapshot(
        updated_at=isoformat(datetime(2026, 1, 12, tzinfo=UTC)),
        behavior_version="1.1.0",
        manifest=BehaviorManifest(
            created_at=isoformat(datetime(2026, 1, 12, tzinfo=UTC)),
            manifest_hash="manifest_hash",
            component_hashes={"decision_policy": "abc"},
        ),
        artifacts=[],
        versions=[],
    )

    store.append_documentation_event(event)
    store.save_documentation_state(snapshot)

    stored_events = store.list_documentation_events()
    stored_snapshot = store.get_documentation_state()

    assert DOCUMENTATION_EVENTS_KEY in client.lists
    assert DOCUMENTATION_STATE_KEY in client.values
    assert len(stored_events) == 1
    assert stored_events[0].event_type == "decision_policy_changed"
    assert stored_snapshot is not None
    assert stored_snapshot.behavior_version == "1.1.0"


def test_decide_job_action_applies_thresholds_and_stale_skip() -> None:
    snapshot = build_default_strategy_snapshot(PROFILE)
    fit = {"fit_score": 82}
    fresh_job = {
        "company": "Acme",
        "role_title": "Solutions Engineer",
        "source": "company_sites",
        "posting_url": "https://example.com/jobs/1",
        "posting_age_days": 2,
    }
    stale_job = {
        "company": "StaleCo",
        "role_title": "Solutions Engineer",
        "source": "linkedin",
        "posting_url": "https://example.com/jobs/2",
        "posting_age_days": 30,
    }

    fresh_action = decide_job_action(fresh_job, fit, PROFILE, snapshot)
    stale_action = decide_job_action(stale_job, fit, PROFILE, snapshot)

    assert fresh_action[0] == "prioritize"
    assert stale_action[0] == "skip"


def test_should_tailor_resume_returns_yes_for_fit_score_or_next_steps() -> None:
    assert should_tailor_resume(fit_score=71, next_steps="Track and monitor for updates.") == "yes"
    assert should_tailor_resume(
        fit_score=60,
        next_steps="Review quickly and decide whether to tailor the resume.",
    ) == "yes"
    assert should_tailor_resume(fit_score=60, next_steps="Track and monitor for updates.") == "no"


def test_build_tracker_row_from_job_sets_tailor_resume_flag() -> None:
    row = build_tracker_row_from_job(
        job=JobRecord(
            company="Acme",
            role_title="Solutions Engineer",
            location="Remote",
            source="company_sites",
            posting_url="https://example.com/jobs/1",
            careers_url="https://example.com/careers/1",
            salary="$100,000",
            remote_or_local="remote",
            fit_score=68,
            match_summary="maybe",
            required_experience_years=5.0,
            candidate_experience_years=6.2,
            experience_gap_years=1.2,
            duplicate_key="acme::solutions engineer::remote",
            reason="Worth a look",
        ),
        next_steps="Review quickly and decide whether to tailor the resume.",
        resume_version="v1.0",
    )

    assert row["tailor_resume"] == "yes"
    assert row["resume_version"] == "v1.0"
    assert row["required_experience_years"] == 5.0
    assert row["candidate_experience_years"] == 6.2
    assert row["experience_gap_years"] == 1.2


def test_tracker_due_follow_ups_waits_three_business_days() -> None:
    rows = [
        {
            "company": "Acme",
            "role_title": "Solutions Engineer",
            "status": "Applied",
            "applied_date": "2026-01-05",
            "duplicate_key": "acme::solutions engineer::remote",
        }
    ]

    tasks = tracker_due_follow_ups(rows)

    assert len(tasks) == 1
    assert tasks[0].duplicate_key == "acme::solutions engineer::remote"


def test_reflect_strategy_reweights_recent_positive_signals() -> None:
    snapshot = build_default_strategy_snapshot(PROFILE)
    goal_state = build_default_goal_state(PROFILE)
    decisions = [
        DecisionRecord(
            decision_id="1",
            timestamp=isoformat(datetime(2026, 1, 10, tzinfo=UTC)),
            workflow="jobs",
            company="Acme",
            role_title="Solutions Engineer",
            role_slug="solutions engineer",
            industry="FinTech",
            source="greenhouse",
            action="prioritize",
            final_score=90,
            base_fit_score=82,
            rationale="test",
        )
    ]
    outcomes = [
        OutcomeEvent(
            event_id="1",
            timestamp=isoformat(datetime(2026, 1, 11, tzinfo=UTC)),
            company="Acme",
            role_title="Solutions Engineer",
            role_slug="solutions engineer",
            industry="FinTech",
            source="greenhouse",
            event_type="interview_request",
        )
    ]

    updated_snapshot, updated_goal_state = reflect_strategy(
        candidate_profile=PROFILE,
        snapshot=snapshot,
        goal_state=goal_state,
        decisions=decisions,
        outcomes=outcomes,
        due_follow_ups=[],
    )

    assert updated_snapshot.role_weights["solutions engineer"] > 0
    assert updated_snapshot.industry_weights["fintech"] > 0
    assert updated_snapshot.source_weights["greenhouse"] > 0
    assert updated_goal_state is not None
    assert any(subgoal.priority > 1.0 for subgoal in updated_goal_state.subgoals if subgoal.subgoal_id == "role:solutions engineer")
