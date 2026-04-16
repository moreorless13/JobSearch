from __future__ import annotations

import json
from typing import Any

from agents import Agent

from job_agent.agents._shared import AGENT_SPECS
from job_agent.config import get_model_name, load_prompt
from job_agent.resume import CoverLetterDraft


def build_cover_letter_writer_agent(
    candidate_profile: dict[str, Any],
    *,
    job: dict[str, Any],
    reference_documents: list[dict[str, Any]],
) -> Agent:
    spec = AGENT_SPECS["CoverLetterWriterAgent"]
    return Agent(
        name="CoverLetterWriterAgent",
        handoff_description=spec["handoff_description"],
        model=get_model_name(),
        instructions=load_prompt(
            spec["prompt_name"],
            candidate_profile_json=json.dumps(candidate_profile, indent=2, sort_keys=True),
            job_json=json.dumps(job, indent=2, sort_keys=True),
            reference_documents_json=json.dumps(reference_documents, indent=2, sort_keys=True),
        ),
        tools=[],
        handoffs=[],
        output_type=CoverLetterDraft,
    )
