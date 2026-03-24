from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field

from job_agent.tools.dedupe import normalize_text

try:
    import redis as redis_module
except ImportError:  # pragma: no cover - exercised via degraded-mode tests
    redis_module = None


DEFAULT_OBJECTIVE = "maximize qualified interview probability within 30 days while respecting salary, location, and role constraints"
STATE_NAMESPACE = "job_agent"
ACTIVE_GOAL_KEY = f"{STATE_NAMESPACE}:goal:active"
CURRENT_STRATEGY_KEY = f"{STATE_NAMESPACE}:strategy:current"
DECISIONS_KEY = f"{STATE_NAMESPACE}:decisions"
OUTCOMES_KEY = f"{STATE_NAMESPACE}:outcomes"
PLAN_RUNS_KEY = f"{STATE_NAMESPACE}:plan_runs"
FOLLOW_UPS_KEY = f"{STATE_NAMESPACE}:follow_ups"
MAX_HISTORY_ITEMS = 500
WEIGHT_DELTA_CAP = 0.4


def utc_now() -> datetime:
    return datetime.now(UTC)


def isoformat(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def role_slug(value: str | None) -> str:
    return normalize_text(value)


class Subgoal(BaseModel):
    subgoal_id: str
    label: str
    priority: float = 1.0
    rationale: str
    status: Literal["active", "paused", "completed"] = "active"


class GoalState(BaseModel):
    goal_id: str = "default_goal"
    objective: str = DEFAULT_OBJECTIVE
    window_days: int = 30
    started_at: str
    expires_at: str
    status: Literal["active", "archived"] = "active"
    subgoals: list[Subgoal] = Field(default_factory=list)


class PlanTask(BaseModel):
    task_id: str
    kind: str
    priority: float
    status: Literal["planned", "completed", "skipped"] = "planned"
    reason: str
    due_at: str | None = None


class PlanRun(BaseModel):
    run_id: str
    workflow: str
    created_at: str
    tasks: list[PlanTask] = Field(default_factory=list)


class DecisionRecord(BaseModel):
    decision_id: str
    timestamp: str
    workflow: str
    duplicate_key: str | None = None
    company: str | None = None
    role_title: str | None = None
    role_slug: str | None = None
    industry: str | None = None
    source: str | None = None
    action: Literal["prioritize", "track", "queue_review", "follow_up_due", "skip"]
    final_score: int
    base_fit_score: int
    freshness_bonus: int = 0
    source_bonus: int = 0
    strategy_bonus: int = 0
    effort_penalty: int = 0
    rationale: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class OutcomeEvent(BaseModel):
    event_id: str
    timestamp: str
    duplicate_key: str | None = None
    company: str | None = None
    role_title: str | None = None
    role_slug: str | None = None
    source: str | None = None
    industry: str | None = None
    event_type: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class FollowUpTask(BaseModel):
    task_id: str
    duplicate_key: str | None = None
    company: str | None = None
    role_title: str | None = None
    due_at: str
    reason: str
    status: Literal["planned", "completed"] = "planned"


class StrategySnapshot(BaseModel):
    updated_at: str
    reflection_summary: str | None = None
    role_weights: dict[str, float] = Field(default_factory=dict)
    industry_weights: dict[str, float] = Field(default_factory=dict)
    source_weights: dict[str, float] = Field(default_factory=dict)
    subgoal_priorities: dict[str, float] = Field(default_factory=dict)


class StateStoreStatus(BaseModel):
    available: bool
    degraded_reason: str | None = None


class StateStore:
    def __init__(self, *, available: bool, degraded_reason: str | None = None) -> None:
        self.status = StateStoreStatus(available=available, degraded_reason=degraded_reason)

    def ensure_goal_state(self, candidate_profile: dict[str, Any]) -> GoalState | None:
        raise NotImplementedError

    def save_goal_state(self, goal_state: GoalState) -> None:
        raise NotImplementedError

    def get_strategy_snapshot(self, candidate_profile: dict[str, Any]) -> StrategySnapshot | None:
        raise NotImplementedError

    def save_strategy_snapshot(self, snapshot: StrategySnapshot) -> None:
        raise NotImplementedError

    def save_plan_run(self, plan_run: PlanRun) -> None:
        raise NotImplementedError

    def append_decision(self, decision: DecisionRecord) -> None:
        raise NotImplementedError

    def append_outcome(self, event: OutcomeEvent) -> None:
        raise NotImplementedError

    def list_decisions(self, *, lookback_days: int | None = None) -> list[DecisionRecord]:
        raise NotImplementedError

    def list_outcomes(self, *, lookback_days: int | None = None) -> list[OutcomeEvent]:
        raise NotImplementedError

    def list_follow_up_tasks(self) -> list[FollowUpTask]:
        raise NotImplementedError

    def save_follow_up_task(self, task: FollowUpTask) -> None:
        raise NotImplementedError

    def mark_follow_up_completed(self, duplicate_key: str | None) -> None:
        raise NotImplementedError


class NullStateStore(StateStore):
    def __init__(self, reason: str) -> None:
        super().__init__(available=False, degraded_reason=reason)

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
        return None

    def append_outcome(self, event: OutcomeEvent) -> None:
        return None

    def list_decisions(self, *, lookback_days: int | None = None) -> list[DecisionRecord]:
        return []

    def list_outcomes(self, *, lookback_days: int | None = None) -> list[OutcomeEvent]:
        return []

    def list_follow_up_tasks(self) -> list[FollowUpTask]:
        return []

    def save_follow_up_task(self, task: FollowUpTask) -> None:
        return None

    def mark_follow_up_completed(self, duplicate_key: str | None) -> None:
        return None


def build_default_subgoals(candidate_profile: dict[str, Any]) -> list[Subgoal]:
    roles = [role for role in candidate_profile.get("target_roles", []) if role]
    subgoals = [
        Subgoal(
            subgoal_id=f"role:{role_slug(role)}",
            label=f"Prioritize {role}",
            rationale="Seeded from the target roles in the candidate profile.",
        )
        for role in roles[:5]
    ]
    subgoals.extend(
        [
            Subgoal(
                subgoal_id="follow_up_hygiene",
                label="Clear due follow-ups",
                rationale="Maintain timely follow-up recommendations for active opportunities.",
            ),
            Subgoal(
                subgoal_id="source_quality",
                label="Favor high-signal sources",
                rationale="Bias toward sources that generate positive recruiter or interview signals.",
            ),
        ]
    )
    return subgoals


def build_default_goal_state(candidate_profile: dict[str, Any]) -> GoalState:
    now = utc_now()
    objective = candidate_profile.get("top_level_objective") or DEFAULT_OBJECTIVE
    return GoalState(
        objective=objective,
        started_at=isoformat(now),
        expires_at=isoformat(now + timedelta(days=30)),
        subgoals=build_default_subgoals(candidate_profile),
    )


def build_default_strategy_snapshot(candidate_profile: dict[str, Any]) -> StrategySnapshot:
    return StrategySnapshot(
        updated_at=isoformat(utc_now()),
        role_weights={role_slug(role): 0.0 for role in candidate_profile.get("target_roles", []) if role},
        industry_weights={normalize_text(industry): 0.0 for industry in candidate_profile.get("target_industries", []) if industry},
        source_weights={},
        subgoal_priorities={},
        reflection_summary="Initialized strategy from candidate profile defaults.",
    )


def within_lookback(timestamp: str, lookback_days: int | None) -> bool:
    if lookback_days is None:
        return True
    try:
        observed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return True
    return observed >= utc_now() - timedelta(days=lookback_days)


class RedisStateStore(StateStore):
    def __init__(self, client: Any) -> None:
        super().__init__(available=True, degraded_reason=None)
        self.client = client

    @staticmethod
    def from_env() -> StateStore:
        redis_url = os.getenv("REDIS_URL")
        if not redis_url:
            return NullStateStore("REDIS_URL is not configured; running in degraded stateless mode.")
        if redis_module is None:
            return NullStateStore("The redis package is not installed; running in degraded stateless mode.")
        try:
            client = redis_module.Redis.from_url(redis_url, decode_responses=True)
            client.ping()
            return RedisStateStore(client)
        except Exception as exc:  # pragma: no cover - covered by degraded-mode tests through monkeypatch
            return NullStateStore(f"Redis is unavailable: {exc}")

    def _get_model(self, key: str, model_cls: type[BaseModel]) -> BaseModel | None:
        payload = self.client.get(key)
        if not payload:
            return None
        return model_cls.model_validate_json(payload)

    def _set_model(self, key: str, model: BaseModel) -> None:
        self.client.set(key, model.model_dump_json())

    def _push_model(self, key: str, model: BaseModel) -> None:
        self.client.lpush(key, model.model_dump_json())
        self.client.ltrim(key, 0, MAX_HISTORY_ITEMS - 1)

    def _load_list(self, key: str, model_cls: type[BaseModel], *, lookback_days: int | None = None) -> list[BaseModel]:
        items = []
        for payload in self.client.lrange(key, 0, MAX_HISTORY_ITEMS - 1):
            item = model_cls.model_validate_json(payload)
            timestamp = getattr(item, "timestamp", None)
            if timestamp and not within_lookback(timestamp, lookback_days):
                continue
            items.append(item)
        return list(reversed(items))

    def ensure_goal_state(self, candidate_profile: dict[str, Any]) -> GoalState | None:
        current = self._get_model(ACTIVE_GOAL_KEY, GoalState)
        if current is not None:
            return current
        goal_state = build_default_goal_state(candidate_profile)
        self._set_model(ACTIVE_GOAL_KEY, goal_state)
        return goal_state

    def save_goal_state(self, goal_state: GoalState) -> None:
        self._set_model(ACTIVE_GOAL_KEY, goal_state)

    def get_strategy_snapshot(self, candidate_profile: dict[str, Any]) -> StrategySnapshot | None:
        current = self._get_model(CURRENT_STRATEGY_KEY, StrategySnapshot)
        if current is not None:
            return current
        snapshot = build_default_strategy_snapshot(candidate_profile)
        self._set_model(CURRENT_STRATEGY_KEY, snapshot)
        return snapshot

    def save_strategy_snapshot(self, snapshot: StrategySnapshot) -> None:
        self._set_model(CURRENT_STRATEGY_KEY, snapshot)

    def save_plan_run(self, plan_run: PlanRun) -> None:
        self._push_model(PLAN_RUNS_KEY, plan_run)

    def append_decision(self, decision: DecisionRecord) -> None:
        self._push_model(DECISIONS_KEY, decision)

    def append_outcome(self, event: OutcomeEvent) -> None:
        self._push_model(OUTCOMES_KEY, event)

    def list_decisions(self, *, lookback_days: int | None = None) -> list[DecisionRecord]:
        return [item for item in self._load_list(DECISIONS_KEY, DecisionRecord, lookback_days=lookback_days)]

    def list_outcomes(self, *, lookback_days: int | None = None) -> list[OutcomeEvent]:
        return [item for item in self._load_list(OUTCOMES_KEY, OutcomeEvent, lookback_days=lookback_days)]

    def list_follow_up_tasks(self) -> list[FollowUpTask]:
        items = [item for item in self._load_list(FOLLOW_UPS_KEY, FollowUpTask)]
        return [item for item in items if item.status == "planned"]

    def save_follow_up_task(self, task: FollowUpTask) -> None:
        existing = self.list_follow_up_tasks()
        if task.duplicate_key and any(item.duplicate_key == task.duplicate_key for item in existing):
            return
        self._push_model(FOLLOW_UPS_KEY, task)

    def mark_follow_up_completed(self, duplicate_key: str | None) -> None:
        if not duplicate_key:
            return
        tasks = [
            FollowUpTask.model_validate_json(payload)
            for payload in self.client.lrange(FOLLOW_UPS_KEY, 0, MAX_HISTORY_ITEMS - 1)
        ]
        updated = False
        for task in tasks:
            if task.duplicate_key == duplicate_key and task.status == "planned":
                task.status = "completed"
                updated = True
        if not updated:
            return
        pipeline = self.client.pipeline()
        pipeline.delete(FOLLOW_UPS_KEY)
        for task in tasks:
            pipeline.rpush(FOLLOW_UPS_KEY, task.model_dump_json())
        pipeline.ltrim(FOLLOW_UPS_KEY, 0, MAX_HISTORY_ITEMS - 1)
        pipeline.execute()


def clamp_weight(value: float) -> float:
    return max(-WEIGHT_DELTA_CAP, min(WEIGHT_DELTA_CAP, value))


def build_plan_run(workflow: str, tasks: list[PlanTask]) -> PlanRun:
    return PlanRun(
        run_id=str(uuid.uuid4()),
        workflow=workflow,
        created_at=isoformat(utc_now()),
        tasks=tasks,
    )


def build_follow_up_task(
    *,
    duplicate_key: str | None,
    company: str | None,
    role_title: str | None,
    due_at: datetime,
    reason: str,
) -> FollowUpTask:
    return FollowUpTask(
        task_id=str(uuid.uuid4()),
        duplicate_key=duplicate_key,
        company=company,
        role_title=role_title,
        due_at=isoformat(due_at),
        reason=reason,
    )
