from __future__ import annotations

from typing import Any

from agents import Agent

from job_agent.agents._shared import AGENT_SPECS, build_agent
from job_agent.tools.sheets import read_tracker_sheet, upsert_tracker_row


def build_tracker_agent(candidate_profile: dict[str, Any]) -> Agent:
    spec = AGENT_SPECS["TrackerAgent"]
    return build_agent(
        name="TrackerAgent",
        handoff_description=spec["handoff_description"],
        prompt_name=spec["prompt_name"],
        candidate_profile=candidate_profile,
        tools=[read_tracker_sheet, upsert_tracker_row],
    )
