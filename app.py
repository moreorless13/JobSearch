from __future__ import annotations

import argparse
import json
from typing import Any

from dotenv import load_dotenv

from job_agent.agents.coordinator import build_coordinator_agent
from job_agent.config import load_candidate_profile
from job_agent.models import ReviewItem, WorkflowOutput, normalize_workflow_output
from job_agent.workflows import run_preset_workflow

MAX_AUTO_FOLLOW_UP_ROUNDS = 5


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the JobSearchAgent workflow.")
    parser.add_argument(
        "--workflow",
        choices=("daily", "jobs", "gmail", "reflect"),
        default="daily",
        help="Which workflow preset to run.",
    )
    parser.add_argument(
        "--input",
        help="Optional free-form instruction. Overrides the workflow preset prompt.",
    )
    return parser.parse_args()


def run_free_form_workflow(
    candidate_profile: dict[str, Any],
    user_input: str,
    *,
    runner_cls: Any | None = None,
    max_auto_follow_up_rounds: int = MAX_AUTO_FOLLOW_UP_ROUNDS,
) -> WorkflowOutput:
    if runner_cls is None:
        from agents import Runner as runner_cls

    coordinator_agent = build_coordinator_agent(candidate_profile)
    current_input = user_input
    previous_response_id: str | None = None
    last_payload = WorkflowOutput()

    for _ in range(max_auto_follow_up_rounds + 1):
        result = runner_cls.run_sync(
            coordinator_agent,
            current_input,
            previous_response_id=previous_response_id,
        )
        last_payload = normalize_workflow_output(result.final_output)
        if not last_payload.follow_up_questions:
            return last_payload

        previous_response_id = getattr(result, "last_response_id", None)
        current_input = "yes"

    last_payload.needs_review.append(
        ReviewItem(
            kind="follow_up_loop_limit",
            reason="Coordinator kept asking follow-up questions after automatic yes responses.",
            details=f"Stopped after {max_auto_follow_up_rounds} automatic follow-up replies.",
        )
    )
    return last_payload


def main() -> None:
    load_dotenv()
    args = parse_args()

    candidate_profile = load_candidate_profile()
    if args.input:
        payload = run_free_form_workflow(candidate_profile, args.input).model_dump()
    else:
        payload = run_preset_workflow(args.workflow, candidate_profile).model_dump()

    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
