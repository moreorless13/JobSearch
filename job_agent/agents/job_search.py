from __future__ import annotations

import json
from typing import Any

from agents import Agent

from job_agent.config import get_model_name, load_prompt
from job_agent.tools.jobs import score_job_fit, search_jobs


def build_job_search_agent(candidate_profile: dict[str, Any]) -> Agent:
    return Agent(
        name="JobSearchAgent",
        handoff_description="Searches job sources, normalizes results, filters them, and scores fit.",
        model=get_model_name(),
        instructions=load_prompt(
            "job_search.txt",
            candidate_profile_json=json.dumps(candidate_profile, indent=2, sort_keys=True),
        ),
        tools=[search_jobs, score_job_fit],
    )
