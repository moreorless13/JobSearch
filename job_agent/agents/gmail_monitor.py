from __future__ import annotations

from typing import Any

from agents import Agent

from job_agent.agents._shared import AGENT_SPECS, build_agent
from job_agent.tools.gmail import (
    classify_job_email,
    match_email_to_tracker,
    search_gmail_job_updates,
)
from job_agent.tools.sheets import read_tracker_sheet, upsert_tracker_row


def build_gmail_monitor_agent(candidate_profile: dict[str, Any]) -> Agent:
    spec = AGENT_SPECS["GmailMonitorAgent"]
    return build_agent(
        name="GmailMonitorAgent",
        handoff_description=spec["handoff_description"],
        prompt_name=spec["prompt_name"],
        candidate_profile=candidate_profile,
        tools=[
            search_gmail_job_updates,
            classify_job_email,
            match_email_to_tracker,
            read_tracker_sheet,
            upsert_tracker_row,
        ],
    )
