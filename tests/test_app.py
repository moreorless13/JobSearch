from types import SimpleNamespace

import pytest

import app as app_module
from app import run_free_form_workflow
from job_agent.redis_preflight import RedisPreflightError


PROFILE = {
    "candidate_name": "James",
    "location_rules": {
        "allow_remote": True,
        "radius_miles": 25,
        "origin": "Cedar Knolls, NJ",
    },
    "salary_floor": 65000,
    "target_roles": ["Solutions Engineer"],
    "target_industries": ["FinTech"],
    "keywords": ["API"],
    "sheet_url": "https://example.com/sheet",
}


class FakeRunner:
    responses: list[SimpleNamespace] = []
    calls: list[tuple[str, str | None]] = []

    @classmethod
    def run_sync(cls, _agent, input, *, previous_response_id=None):
        cls.calls.append((input, previous_response_id))
        return cls.responses.pop(0)


def test_run_free_form_workflow_auto_replies_yes_until_follow_ups_clear() -> None:
    FakeRunner.calls = []
    FakeRunner.responses = [
        SimpleNamespace(
            final_output={
                "summary": {
                    "jobs_reviewed": 0,
                    "jobs_added": 0,
                    "duplicates_skipped": 0,
                    "gmail_updates_processed": 0,
                    "tracker_rows_updated": 0,
                },
                "new_jobs": [],
                "gmail_updates": [],
                "tracker_updates": [],
                "needs_review": [],
                "follow_up_questions": [{"question": "Proceed with the default search?", "required": True}],
                "assistant_response": "Proceed with the default search?",
            },
            last_response_id="resp_1",
        ),
        SimpleNamespace(
            final_output={
                "summary": {
                    "jobs_reviewed": 2,
                    "jobs_added": 1,
                    "duplicates_skipped": 0,
                    "gmail_updates_processed": 0,
                    "tracker_rows_updated": 1,
                },
                "new_jobs": [],
                "gmail_updates": [],
                "tracker_updates": [],
                "needs_review": [],
                "follow_up_questions": [],
                "assistant_response": "Added 1 job to the tracker.",
            },
            last_response_id="resp_2",
        ),
    ]

    result = run_free_form_workflow(PROFILE, "Help me with my job search.", runner_cls=FakeRunner)

    assert FakeRunner.calls == [
        ("Help me with my job search.", None),
        ("yes", "resp_1"),
    ]
    assert result.summary.jobs_added == 1
    assert result.follow_up_questions == []


def test_run_free_form_workflow_adds_review_after_follow_up_loop_limit() -> None:
    FakeRunner.calls = []
    FakeRunner.responses = [
        SimpleNamespace(
            final_output={
                "summary": {
                    "jobs_reviewed": 0,
                    "jobs_added": 0,
                    "duplicates_skipped": 0,
                    "gmail_updates_processed": 0,
                    "tracker_rows_updated": 0,
                },
                "new_jobs": [],
                "gmail_updates": [],
                "tracker_updates": [],
                "needs_review": [],
                "follow_up_questions": [{"question": "Still proceed?", "required": True}],
                "assistant_response": "Still proceed?",
            },
            last_response_id="resp_1",
        ),
        SimpleNamespace(
            final_output={
                "summary": {
                    "jobs_reviewed": 0,
                    "jobs_added": 0,
                    "duplicates_skipped": 0,
                    "gmail_updates_processed": 0,
                    "tracker_rows_updated": 0,
                },
                "new_jobs": [],
                "gmail_updates": [],
                "tracker_updates": [],
                "needs_review": [],
                "follow_up_questions": [{"question": "Still proceed now?", "required": True}],
                "assistant_response": "Still proceed now?",
            },
            last_response_id="resp_2",
        ),
    ]

    result = run_free_form_workflow(
        PROFILE,
        "Help me with my job search.",
        runner_cls=FakeRunner,
        max_auto_follow_up_rounds=1,
    )

    assert len(result.needs_review) == 1
    assert result.needs_review[0].kind == "follow_up_loop_limit"


def test_main_exits_before_workflow_when_redis_preflight_fails(monkeypatch, capsys) -> None:
    def fail_preflight():
        raise RedisPreflightError(
            "Redis preflight failed: could not connect to redis://redis:6379/0: connection refused",
            start_command="docker compose up -d redis",
        )

    monkeypatch.setattr(app_module.dotenv_module, "load_dotenv", lambda: None)
    monkeypatch.setattr(app_module, "parse_args", lambda: SimpleNamespace(workflow="daily", input=None, explain=None))
    monkeypatch.setattr(app_module, "run_redis_preflight", fail_preflight)
    monkeypatch.setattr(
        app_module,
        "load_candidate_profile",
        lambda: pytest.fail("candidate profile should not load after Redis preflight failure"),
    )

    with pytest.raises(SystemExit) as exc_info:
        app_module.main()

    captured = capsys.readouterr()
    assert exc_info.value.code == 1
    assert "Redis preflight failed" in captured.err
    assert "Start Redis with: docker compose up -d redis" in captured.err
