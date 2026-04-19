from __future__ import annotations

import json
import re
from typing import Any, Literal, cast

import pydantic as pydantic_module

from job_agent.docs.models import DocumentationUpdate
from job_agent.state import QAVerdict

BaseModel = cast(Any, pydantic_module).BaseModel
Field = cast(Any, pydantic_module).Field
field_validator = cast(Any, pydantic_module).field_validator
ValidationError = cast(Any, pydantic_module).ValidationError


def _blank_string_to_none(value: Any) -> Any:
    if isinstance(value, str) and not value.strip():
        return None
    return value


class SummaryCounts(BaseModel):
    jobs_reviewed: int = 0
    jobs_added: int = 0
    duplicates_skipped: int = 0
    availability_checks_processed: int = 0
    gmail_updates_processed: int = 0
    tracker_rows_updated: int = 0
    qa_evaluations: int = 0
    qa_approved: int = 0
    qa_flagged: int = 0
    qa_rejected: int = 0


class JobRecord(BaseModel):
    company: str | None = None
    role_title: str | None = None
    location: str | None = None
    source: str | None = None
    posting_url: str | None = None
    careers_url: str | None = None
    salary: str | None = None
    remote_or_local: Literal["remote", "local", "hybrid", "unknown"] = "unknown"
    fit_score: int | None = None
    match_summary: str | None = None
    required_experience_years: float | None = None
    candidate_experience_years: float | None = None
    experience_gap_years: float | None = None
    checked_url: str | None = None
    link_check_status: str | None = None
    link_checked_at: str | None = None
    availability_status: str | None = None
    availability_checked_at: str | None = None
    availability_next_check_at: str | None = None
    availability_notes: str | None = None
    duplicate_key: str | None = None
    reason: str | None = None

    @field_validator(
        "fit_score",
        "required_experience_years",
        "candidate_experience_years",
        "experience_gap_years",
        mode="before",
    )
    @classmethod
    def blank_optional_numeric_fields_to_none(cls, value: Any) -> Any:
        return _blank_string_to_none(value)


class TrackerUpdate(BaseModel):
    company: str | None = None
    role_title: str | None = None
    status: str | None = None
    duplicate_key: str | None = None
    update_type: str | None = None
    notes: str | None = None


class GmailUpdate(BaseModel):
    classification: str
    company: str | None = None
    role_title: str | None = None
    deadline: str | None = None
    action: str | None = None
    matched_duplicate_key: str | None = None
    confidence: float | None = None


class ResumeArtifact(BaseModel):
    company: str | None = None
    role_title: str | None = None
    version: str
    output_path: str
    format: str = "markdown"
    docx_path: str | None = None
    google_doc_id: str | None = None
    google_doc_url: str | None = None
    google_doc_error: str | None = None
    source_labels: list[str] = Field(default_factory=list)


class CoverLetterArtifact(BaseModel):
    company: str | None = None
    role_title: str | None = None
    version: str
    output_path: str
    format: str = "markdown"
    docx_path: str | None = None
    google_doc_id: str | None = None
    google_doc_url: str | None = None
    google_doc_error: str | None = None
    source_labels: list[str] = Field(default_factory=list)


class ReviewItem(BaseModel):
    kind: str
    company: str | None = None
    role_title: str | None = None
    reason: str
    details: str | None = None


class QAResult(BaseModel):
    event_type: str
    stage: str
    entity_key: str | None = None
    verdict: QAVerdict
    score: float
    approve_threshold: float
    flag_threshold: float
    blocked_action: str | None = None
    recommended_action: str | None = None
    reasons: list[str] = Field(default_factory=list)
    score_breakdown: dict[str, float] = Field(default_factory=dict)


class FollowUpQuestion(BaseModel):
    question: str
    context: str | None = None
    required: bool = True


class WorkflowOutput(BaseModel):
    summary: SummaryCounts = Field(default_factory=SummaryCounts)
    new_jobs: list[JobRecord] = Field(default_factory=list)
    gmail_updates: list[GmailUpdate] = Field(default_factory=list)
    resume_artifacts: list[ResumeArtifact] = Field(default_factory=list)
    cover_letter_artifacts: list[CoverLetterArtifact] = Field(default_factory=list)
    tracker_updates: list[TrackerUpdate] = Field(default_factory=list)
    qa_results: list[QAResult] = Field(default_factory=list)
    documentation_updates: list[DocumentationUpdate] = Field(default_factory=list)
    needs_review: list[ReviewItem] = Field(default_factory=list)
    follow_up_questions: list[FollowUpQuestion] = Field(default_factory=list)
    assistant_response: str | None = None


def finalize_workflow_output(output: WorkflowOutput) -> WorkflowOutput:
    if not output.follow_up_questions and output.assistant_response:
        output.follow_up_questions.extend(extract_follow_up_questions(output.assistant_response))
    return output


def extract_follow_up_questions(text: str) -> list[FollowUpQuestion]:
    normalized_text = text.strip()
    if not normalized_text:
        return []

    questions: list[FollowUpQuestion] = []
    chunks = [chunk.strip(" -*\t") for chunk in normalized_text.splitlines() if chunk.strip()]
    for chunk in chunks:
        for match in re.findall(r"[^?]*\?", chunk):
            question = match.strip()
            if not question:
                continue
            questions.append(FollowUpQuestion(question=question, required=True))
            if len(questions) >= 3:
                return questions

    return questions


def looks_like_data_entry_response(text: str) -> bool:
    normalized = text.strip().lower()
    confirmation_markers = (
        "updated ",
        "added ",
        "recorded ",
        "logged ",
        "saved ",
        "marked ",
        "set ",
        "created ",
    )
    tracker_markers = ("tracker", "sheet", "status", "interview", "follow-up", "follow up", "note", "row")
    return any(marker in normalized for marker in confirmation_markers) and any(
        marker in normalized for marker in tracker_markers
    )


def review_output(kind: str, reason: str, details: str) -> WorkflowOutput:
    return finalize_workflow_output(WorkflowOutput(
        needs_review=[
            ReviewItem(
                kind=kind,
                reason=reason,
                details=details,
            )
        ]
    ))


def try_validate_workflow_output(raw_output: Any) -> WorkflowOutput | None:
    if hasattr(raw_output, "model_dump"):
        raw_output = raw_output.model_dump()
    if not isinstance(raw_output, dict):
        return None
    try:
        return finalize_workflow_output(WorkflowOutput.model_validate(raw_output))
    except ValidationError:
        return None


def workflow_output_from_text(text: str) -> WorkflowOutput:
    normalized_text = text.strip()
    if not normalized_text:
        return finalize_workflow_output(WorkflowOutput())

    try:
        parsed_output = json.loads(normalized_text)
    except json.JSONDecodeError:
        parsed_output = None

    validated = try_validate_workflow_output(parsed_output)
    if validated is not None:
        return validated

    follow_up_questions = extract_follow_up_questions(normalized_text)
    if follow_up_questions:
        return finalize_workflow_output(WorkflowOutput(
            follow_up_questions=follow_up_questions,
            assistant_response=normalized_text,
        ))

    if looks_like_data_entry_response(normalized_text):
        return finalize_workflow_output(WorkflowOutput(assistant_response=normalized_text))

    return finalize_workflow_output(WorkflowOutput(
        assistant_response=normalized_text,
        needs_review=[
            ReviewItem(
                kind="unstructured_output",
                reason="Coordinator returned text instead of the required workflow JSON contract.",
                details=normalized_text,
            )
        ]
    ))


def normalize_workflow_output(raw_output: Any) -> WorkflowOutput:
    if isinstance(raw_output, WorkflowOutput):
        return finalize_workflow_output(raw_output)

    validated = try_validate_workflow_output(raw_output)
    if validated is not None:
        return validated

    if isinstance(raw_output, str):
        return workflow_output_from_text(raw_output)

    return review_output(
        "unexpected_output_type",
        "Coordinator returned a value that could not be normalized into the workflow contract.",
        repr(raw_output),
    )
