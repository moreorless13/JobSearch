from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from job_agent.resume import normalize_resume_reference_documents

ROOT_DIR = Path(__file__).resolve().parent.parent
SCHEMAS_DIR = ROOT_DIR / "schemas"
PROMPTS_DIR = ROOT_DIR / "prompts"

DEFAULT_WORKFLOW_INPUTS = {
    "daily": "Run today's workflow: search jobs, update the tracker, scan Gmail, and summarize changes.",
    "jobs": "Search for new matching jobs, update the tracker, and summarize changes.",
    "availability": "Recheck tracked job posting links and availability for rows due for verification.",
    "gmail": "Scan Gmail for job-related updates, sync the tracker, and summarize changes.",
    "reflect": "Review recent outcomes, update strategy weights, and summarize the changes.",
    "backfill-resumes": "Generate tailored resumes for existing tracker rows and write resume versions back to the tracker.",
    "backfill-cover-letters": "Generate cover letters for existing tracker rows and write cover letter versions back to the tracker.",
    "backfill-materials": "Generate tailored resumes and cover letters for existing tracker rows and write versions back to the tracker.",
}

DEFAULT_TOP_LEVEL_OBJECTIVE = "maximize qualified interview probability within 30 days while respecting salary, location, and role constraints"
DEFAULT_DECISION_THRESHOLDS = {
    "prioritize": 85,
    "track": 70,
    "queue_review": 60,
    "stale_days": 21,
}
DEFAULT_QA_SETTINGS = {
    "approve_threshold": 0.8,
    "flag_threshold": 0.6,
    "llm_judge_enabled": False,
    "duplicate_company_cooldown_days": 7,
}


def load_candidate_profile() -> dict[str, Any]:
    with (SCHEMAS_DIR / "candidate_profile.json").open("r", encoding="utf-8") as handle:
        profile = json.load(handle)

    override_sheet_url = os.getenv("JOB_TRACKER_SHEET_URL")
    if override_sheet_url:
        profile["sheet_url"] = override_sheet_url

    resume_template_path = os.getenv("RESUME_TEMPLATE_DOCX_PATH")
    if resume_template_path:
        profile["resume_template_document_path"] = resume_template_path

    cover_letter_template_path = os.getenv("COVER_LETTER_TEMPLATE_DOCX_PATH")
    if cover_letter_template_path:
        profile["cover_letter_template_document_path"] = cover_letter_template_path

    resume_drive_folder_id = os.getenv("RESUME_GOOGLE_DRIVE_FOLDER_ID")
    if resume_drive_folder_id:
        profile["resume_google_drive_folder_id"] = resume_drive_folder_id

    resume_drive_folder_url = os.getenv("RESUME_GOOGLE_DRIVE_FOLDER_URL")
    if resume_drive_folder_url:
        profile["resume_google_drive_folder_url"] = resume_drive_folder_url

    profile.setdefault("top_level_objective", DEFAULT_TOP_LEVEL_OBJECTIVE)
    profile.setdefault("company_priorities", {})
    profile.setdefault("resume_reference_documents", [])
    profile["resume_reference_documents"] = normalize_resume_reference_documents(profile.get("resume_reference_documents"))
    profile["decision_thresholds"] = {
        **DEFAULT_DECISION_THRESHOLDS,
        **(profile.get("decision_thresholds") or {}),
    }
    profile["qa"] = {
        **DEFAULT_QA_SETTINGS,
        **(profile.get("qa") or {}),
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
