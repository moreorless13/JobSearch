from __future__ import annotations

import json
from typing import Any

from agents import Agent

from job_agent.agents.gmail_monitor import build_gmail_monitor_agent
from job_agent.agents.job_search import build_job_search_agent
from job_agent.agents.tracker import build_tracker_agent
from job_agent.config import get_model_name, load_prompt
from job_agent.models import WorkflowOutput


def build_coordinator_agent(candidate_profile: dict[str, Any]) -> Agent:
    job_search_agent = build_job_search_agent(candidate_profile)
    tracker_agent = build_tracker_agent(candidate_profile)
    gmail_monitor_agent = build_gmail_monitor_agent(candidate_profile)

    return Agent(
        name="CoordinatorAgent",
        handoff_description="Routes work between job search, tracker, and Gmail specialists and returns a structured summary.",
        model=get_model_name(),
        instructions=load_prompt(
            "coordinator.txt",
            candidate_profile_json=json.dumps(candidate_profile, indent=2, sort_keys=True),
        ),
        handoffs=[job_search_agent, tracker_agent, gmail_monitor_agent],
        output_type=WorkflowOutput,
    )
