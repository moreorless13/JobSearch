from __future__ import annotations

from typing import Any

from agents import Agent

from job_agent.agents._shared import AGENT_SPECS, build_agent
from job_agent.tools.jobs import score_job_fit, search_jobs, verify_job_availability


def build_job_search_agent(candidate_profile: dict[str, Any]) -> Agent:
    spec = AGENT_SPECS["JobSearchAgent"]
    return build_agent(
        name="JobSearchAgent",
        handoff_description=spec["handoff_description"],
        prompt_name=spec["prompt_name"],
        candidate_profile=candidate_profile,
        tools=[search_jobs, score_job_fit, verify_job_availability],
    )
