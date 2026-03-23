from __future__ import annotations

import json
from typing import Any

from agents import Agent

from job_agent.config import get_model_name, load_prompt
from job_agent.tools.gmail import (
    classify_job_email,
    match_email_to_tracker,
    search_gmail_job_updates,
)
from job_agent.tools.sheets import read_tracker_sheet, upsert_tracker_row


def build_gmail_monitor_agent(candidate_profile: dict[str, Any]) -> Agent:
    return Agent(
        name="GmailMonitorAgent",
        handoff_description="Scans Gmail, classifies job-related messages, and proposes tracker updates.",
        model=get_model_name(),
        instructions=load_prompt(
            "gmail_monitor.txt",
            candidate_profile_json=json.dumps(candidate_profile, indent=2, sort_keys=True),
        ),
        tools=[
            search_gmail_job_updates,
            classify_job_email,
            match_email_to_tracker,
            read_tracker_sheet,
            upsert_tracker_row,
        ],
    )
