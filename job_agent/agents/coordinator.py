from __future__ import annotations

from typing import Any

from agents import Agent

from job_agent.agents._shared import AGENT_SPECS, build_agent
from job_agent.agents.gmail_monitor import build_gmail_monitor_agent
from job_agent.agents.job_search import build_job_search_agent
from job_agent.agents.tracker import build_tracker_agent
from job_agent.models import WorkflowOutput


def build_coordinator_agent(candidate_profile: dict[str, Any]) -> Agent:
    spec = AGENT_SPECS["CoordinatorAgent"]
    job_search_agent = build_job_search_agent(candidate_profile)
    tracker_agent = build_tracker_agent(candidate_profile)
    gmail_monitor_agent = build_gmail_monitor_agent(candidate_profile)

    return build_agent(
        name="CoordinatorAgent",
        handoff_description=spec["handoff_description"],
        prompt_name=spec["prompt_name"],
        candidate_profile=candidate_profile,
        handoffs=[job_search_agent, tracker_agent, gmail_monitor_agent],
        output_type=WorkflowOutput,
    )
