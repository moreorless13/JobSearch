from __future__ import annotations

from job_agent.models import WorkflowOutput
from job_agent.orchestrator import JobSearchOrchestrator


def run_jobs_workflow(candidate_profile: dict) -> WorkflowOutput:
    return JobSearchOrchestrator(candidate_profile).run_jobs()


def run_gmail_workflow(candidate_profile: dict) -> WorkflowOutput:
    return JobSearchOrchestrator(candidate_profile).run_gmail()


def run_reflect_workflow(candidate_profile: dict) -> WorkflowOutput:
    return JobSearchOrchestrator(candidate_profile).run_reflect()


def run_preset_workflow(workflow: str, candidate_profile: dict) -> WorkflowOutput:
    orchestrator = JobSearchOrchestrator(candidate_profile)
    if workflow == "jobs":
        return orchestrator.run_jobs()
    if workflow == "gmail":
        return orchestrator.run_gmail()
    if workflow == "reflect":
        return orchestrator.run_reflect()
    if workflow == "daily":
        return orchestrator.run_daily()
    raise ValueError(f"Unsupported workflow preset: {workflow}")
