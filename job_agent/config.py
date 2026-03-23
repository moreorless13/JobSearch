from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
SCHEMAS_DIR = ROOT_DIR / "schemas"
PROMPTS_DIR = ROOT_DIR / "prompts"

DEFAULT_WORKFLOW_INPUTS = {
    "daily": "Run today's workflow: search jobs, update the tracker, scan Gmail, and summarize changes.",
    "jobs": "Search for new matching jobs, update the tracker, and summarize changes.",
    "gmail": "Scan Gmail for job-related updates, sync the tracker, and summarize changes.",
}


def load_candidate_profile() -> dict[str, Any]:
    with (SCHEMAS_DIR / "candidate_profile.json").open("r", encoding="utf-8") as handle:
        profile = json.load(handle)

    override_sheet_url = os.getenv("JOB_TRACKER_SHEET_URL")
    if override_sheet_url:
        profile["sheet_url"] = override_sheet_url

    return profile


def load_prompt(name: str, **replacements: str) -> str:
    content = (PROMPTS_DIR / name).read_text(encoding="utf-8")
    if replacements:
        content = content.format(**replacements)
    return content


def build_run_input(workflow: str) -> str:
    return DEFAULT_WORKFLOW_INPUTS[workflow]


def get_model_name() -> str:
    return os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
