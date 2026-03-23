from __future__ import annotations

import json
from typing import Any

from agents import Agent

from job_agent.config import get_model_name, load_prompt
from job_agent.tools.sheets import read_tracker_sheet, upsert_tracker_row


def build_tracker_agent(candidate_profile: dict[str, Any]) -> Agent:
    return Agent(
        name="TrackerAgent",
        handoff_description="Reads and upserts tracker rows while preserving history and deduplicating updates.",
        model=get_model_name(),
        instructions=load_prompt(
            "tracker.txt",
            candidate_profile_json=json.dumps(candidate_profile, indent=2, sort_keys=True),
        ),
        tools=[read_tracker_sheet, upsert_tracker_row],
    )
