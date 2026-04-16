from __future__ import annotations

from typing import Callable

from job_agent.models import WorkflowOutput
from job_agent.orchestrator import JobSearchOrchestrator


WorkflowRunner = Callable[[JobSearchOrchestrator], WorkflowOutput]


def run_jobs_workflow(candidate_profile: dict) -> WorkflowOutput:
    return JobSearchOrchestrator(candidate_profile).run_jobs()


def run_gmail_workflow(candidate_profile: dict) -> WorkflowOutput:
    return JobSearchOrchestrator(candidate_profile).run_gmail()


def run_reflect_workflow(candidate_profile: dict) -> WorkflowOutput:
    return JobSearchOrchestrator(candidate_profile).run_reflect()


def run_backfill_resumes_workflow(candidate_profile: dict, *, overwrite_existing: bool = True) -> WorkflowOutput:
    return JobSearchOrchestrator(candidate_profile).backfill_tracker_resumes(overwrite_existing=overwrite_existing)


def run_backfill_cover_letters_workflow(candidate_profile: dict, *, overwrite_existing: bool = True) -> WorkflowOutput:
    return JobSearchOrchestrator(candidate_profile).backfill_tracker_cover_letters(overwrite_existing=overwrite_existing)


def run_backfill_materials_workflow(candidate_profile: dict, *, overwrite_existing: bool = True) -> WorkflowOutput:
    return JobSearchOrchestrator(candidate_profile).backfill_tracker_application_materials(overwrite_existing=overwrite_existing)


WORKFLOW_RUNNERS: dict[str, WorkflowRunner] = {
    "jobs": lambda orchestrator: orchestrator.run_jobs(),
    "gmail": lambda orchestrator: orchestrator.run_gmail(),
    "reflect": lambda orchestrator: orchestrator.run_reflect(),
    "daily": lambda orchestrator: orchestrator.run_daily(),
    "backfill-resumes": lambda orchestrator: orchestrator.backfill_tracker_resumes(),
    "backfill-cover-letters": lambda orchestrator: orchestrator.backfill_tracker_cover_letters(),
    "backfill-materials": lambda orchestrator: orchestrator.backfill_tracker_application_materials(),
}


def run_preset_workflow(workflow: str, candidate_profile: dict) -> WorkflowOutput:
    orchestrator = JobSearchOrchestrator(candidate_profile)
    runner = WORKFLOW_RUNNERS.get(workflow)
    if runner is None:
        raise ValueError(f"Unsupported workflow preset: {workflow}")
    return runner(orchestrator)
