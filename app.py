from __future__ import annotations

import argparse
import json
from typing import Any, cast

import dotenv as dotenv_module

from job_agent.config import load_candidate_profile
from job_agent.docs.service import ExplainService
from job_agent.models import ReviewItem, WorkflowOutput, normalize_workflow_output
from job_agent.state import RedisStateStore
from job_agent.workflows import run_preset_workflow

MAX_AUTO_FOLLOW_UP_ROUNDS = 5


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the JobSearchAgent workflow.")
    parser.add_argument(
        "--workflow",
        choices=("daily", "jobs", "gmail", "reflect", "backfill-resumes", "backfill-cover-letters", "backfill-materials"),
        default="daily",
        help="Which workflow preset to run.",
    )
    parser.add_argument(
        "--input",
        help="Optional free-form instruction. Overrides the workflow preset prompt.",
    )
    parser.add_argument(
        "--explain",
        help="Answer a question from generated docs plus recent workflow state.",
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
        import agents as agents_module

        runner_cls = cast(Any, agents_module).Runner
    runner = cast(Any, runner_cls)

    from job_agent.agents.coordinator import build_coordinator_agent

    coordinator_agent = build_coordinator_agent(candidate_profile)
    current_input = user_input
    previous_response_id: str | None = None
    last_payload = WorkflowOutput()

    for _ in range(max_auto_follow_up_rounds + 1):
        result = runner.run_sync(
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


def build_cli_payload(args: argparse.Namespace, candidate_profile: dict[str, Any]) -> dict[str, Any]:
    if args.explain:
        state_store = RedisStateStore.from_env()
        strategy_snapshot = state_store.get_strategy_snapshot(candidate_profile)
        return ExplainService(
            candidate_profile=candidate_profile,
            state_store=state_store,
            strategy_snapshot=strategy_snapshot,
        ).explain(args.explain).model_dump()
    if args.input:
        return run_free_form_workflow(candidate_profile, args.input).model_dump()
    return run_preset_workflow(args.workflow, candidate_profile).model_dump()


def main() -> None:
    cast(Any, dotenv_module).load_dotenv()
    args = parse_args()
    candidate_profile = load_candidate_profile()
    payload = build_cli_payload(args, candidate_profile)
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
