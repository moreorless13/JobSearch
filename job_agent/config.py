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
    "reflect": "Review recent outcomes, update strategy weights, and summarize the changes.",
}

DEFAULT_TOP_LEVEL_OBJECTIVE = "maximize qualified interview probability within 30 days while respecting salary, location, and role constraints"
DEFAULT_DECISION_THRESHOLDS = {
    "prioritize": 85,
    "track": 70,
    "queue_review": 60,
    "stale_days": 21,
}


def load_candidate_profile() -> dict[str, Any]:
    with (SCHEMAS_DIR / "candidate_profile.json").open("r", encoding="utf-8") as handle:
        profile = json.load(handle)

    override_sheet_url = os.getenv("JOB_TRACKER_SHEET_URL")
    if override_sheet_url:
        profile["sheet_url"] = override_sheet_url

    profile.setdefault("top_level_objective", DEFAULT_TOP_LEVEL_OBJECTIVE)
    profile.setdefault("company_priorities", {})
    profile["decision_thresholds"] = {
        **DEFAULT_DECISION_THRESHOLDS,
        **(profile.get("decision_thresholds") or {}),
    }

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


def get_redis_url() -> str | None:
    return os.getenv("REDIS_URL")
