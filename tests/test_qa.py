from __future__ import annotations

from job_agent.events import WorkflowEvent
from job_agent.qa import QAEventDispatcher
from job_agent.state import NullStateStore, build_default_strategy_snapshot


PROFILE = {
    "candidate_name": "James",
    "location_rules": {
        "allow_remote": True,
        "radius_miles": 25,
        "origin": "Cedar Knolls, NJ",
    },
    "salary_floor": 65000,
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


def test_job_found_duplicate_is_hard_reject() -> None:
    dispatcher = QAEventDispatcher(PROFILE, NullStateStore("no redis"))

    result = dispatcher.evaluate(
        workflow="jobs",
        event_type=WorkflowEvent.JOB_FOUND,
        stage="pre_action",
        entity_key="acme::solutions engineer::remote us",
        payload={
            "job": {
                "company": "Acme",
                "role_title": "Solutions Engineer",
                "location": "Remote - US",
                "source": "company_sites",
                "posting_url": "https://example.com/jobs/1",
                "remote_or_local": "remote",
                "posting_age_days": 1,
                "duplicate_key": "acme::solutions engineer::remote us",
            },
            "fit": {"fit_score": 90},
            "decision": {},
        },
        context={
            "tracker_rows": [
                {
                    "company": "Acme",
                    "role_title": "Solutions Engineer",
                    "location": "Remote - US",
                    "duplicate_key": "acme::solutions engineer::remote us",
                }
            ]
        },
    )

    assert result.verdict == "reject"
    assert result.blocked_action == "job_intake"


def test_llm_adjustment_does_not_override_hard_failures(monkeypatch) -> None:
    profile = {**PROFILE, "qa": {**PROFILE["qa"], "llm_judge_enabled": True}}
    dispatcher = QAEventDispatcher(profile, NullStateStore("no redis"))
    monkeypatch.setattr(
        dispatcher,
        "_call_llm_judge",
        lambda **_kwargs: type("Adjustment", (), {"score_adjustment": 0.1, "reasons": ["model liked it"]})(),
    )

    result = dispatcher.evaluate(
        workflow="jobs",
        event_type=WorkflowEvent.JOB_FOUND,
        stage="pre_action",
        entity_key="dup",
        payload={
            "job": {
                "company": "Acme",
                "role_title": "Solutions Engineer",
                "location": "Remote - US",
                "source": "company_sites",
                "posting_url": "https://example.com/jobs/1",
                "remote_or_local": "remote",
                "posting_age_days": 1,
                "duplicate_key": "dup",
            },
            "fit": {"fit_score": 95},
            "decision": {},
        },
        context={"tracker_rows": [{"company": "Acme", "role_title": "Solutions Engineer", "location": "Remote - US", "duplicate_key": "dup"}]},
    )

    assert result.verdict == "reject"
    assert "model liked it" not in result.reasons


def test_strategy_reflection_flags_when_summary_is_not_explainable() -> None:
    dispatcher = QAEventDispatcher(PROFILE, NullStateStore("no redis"))
    previous_snapshot = build_default_strategy_snapshot(PROFILE)
    updated_snapshot = previous_snapshot.model_copy(
        update={
            "role_weights": {"solutions engineer": 0.1},
            "reflection_summary": "Trust me, bro.",
        },
        deep=True,
    )

    result = dispatcher.evaluate(
        workflow="reflect",
        event_type=WorkflowEvent.STRATEGY_REFLECTED,
        stage="pre_action",
        entity_key="default_goal",
        payload={
            "previous_snapshot": previous_snapshot,
            "updated_snapshot": updated_snapshot,
            "decisions": [object(), object(), object()],
            "outcomes": [object()],
            "due_follow_ups": [],
        },
    )

    assert result.verdict == "flag"
    assert result.blocked_action == "strategy_persist"
