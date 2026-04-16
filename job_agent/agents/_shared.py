from __future__ import annotations

import json
from typing import Any

from job_agent.config import get_model_name, load_prompt


AGENT_SPECS: dict[str, dict[str, Any]] = {
    "CoordinatorAgent": {
        "prompt_name": "coordinator.txt",
        "handoff_description": "Routes work between job search, tracker, and Gmail specialists and returns a structured summary.",
        "handoffs": ["JobSearchAgent", "TrackerAgent", "GmailMonitorAgent"],
        "tools": [],
    },
    "JobSearchAgent": {
        "prompt_name": "job_search.txt",
        "handoff_description": "Searches job sources, normalizes results, filters them, and scores fit.",
        "handoffs": [],
        "tools": ["search_jobs", "score_job_fit"],
    },
    "TrackerAgent": {
        "prompt_name": "tracker.txt",
        "handoff_description": "Reads and upserts tracker rows while preserving history and deduplicating updates.",
        "handoffs": [],
        "tools": ["read_tracker_sheet", "upsert_tracker_row"],
    },
    "GmailMonitorAgent": {
        "prompt_name": "gmail_monitor.txt",
        "handoff_description": "Scans Gmail, classifies job-related messages, and proposes tracker updates.",
        "handoffs": [],
        "tools": [
            "search_gmail_job_updates",
            "classify_job_email",
            "match_email_to_tracker",
            "read_tracker_sheet",
            "upsert_tracker_row",
        ],
    },
    "ResumeWriterAgent": {
        "prompt_name": "resume_writer.txt",
        "handoff_description": "Drafts versioned resume artifacts from the candidate profile, reference resumes, and target job details.",
        "handoffs": [],
        "tools": [],
    },
    "CoverLetterWriterAgent": {
        "prompt_name": "cover_letter_writer.txt",
        "handoff_description": "Drafts tailored cover letter artifacts from the candidate profile, reference resumes, and target job details.",
        "handoffs": [],
        "tools": [],
    },
}


def candidate_profile_prompt_context(candidate_profile: dict[str, Any]) -> dict[str, str]:
    return {
        "candidate_profile_json": json.dumps(candidate_profile, indent=2, sort_keys=True),
    }


def build_agent(
    *,
    name: str,
    handoff_description: str,
    prompt_name: str,
    candidate_profile: dict[str, Any],
    tools: list[Any] | None = None,
    handoffs: list[Any] | None = None,
    output_type: Any | None = None,
) -> Any:
    import agents as agents_module

    agent_cls = agents_module.Agent
    return agent_cls(
        name=name,
        handoff_description=handoff_description,
        model=get_model_name(),
        instructions=load_prompt(prompt_name, **candidate_profile_prompt_context(candidate_profile)),
        tools=list(tools or []),
        handoffs=list(handoffs or []),
        output_type=output_type,
    )


def agent_graph_spec() -> dict[str, dict[str, Any]]:
    return {
        name: {
            "handoffs": list(spec["handoffs"]),
            "tools": list(spec["tools"]),
            "handoff_description": spec["handoff_description"],
        }
        for name, spec in AGENT_SPECS.items()
    }
