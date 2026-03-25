from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

DocumentationEventType = Literal[
    "agent_graph_changed",
    "workflow_changed",
    "tool_surface_changed",
    "decision_policy_changed",
    "qa_policy_changed",
    "prompt_changed",
    "schema_changed",
    "strategy_changed",
]


class DocumentationEvent(BaseModel):
    event_id: str
    timestamp: str
    event_type: DocumentationEventType
    summary: str
    reason: str | None = None
    impact: str | None = None
    before: dict[str, Any] = Field(default_factory=dict)
    after: dict[str, Any] = Field(default_factory=dict)
    workflow: str | None = None
    behavior_version: str | None = None


class DocumentationArtifact(BaseModel):
    artifact_path: str
    title: str
    source_event_ids: list[str] = Field(default_factory=list)
    rendered_at: str
    content_hash: str
    behavior_version: str


class DocumentationUpdate(BaseModel):
    artifact_path: str
    title: str
    update_type: Literal["created", "updated", "unchanged"]
    behavior_version: str
    summary: str


class BehaviorManifest(BaseModel):
    created_at: str
    manifest_hash: str
    component_hashes: dict[str, str] = Field(default_factory=dict)
    public_interfaces: dict[str, Any] = Field(default_factory=dict)
    workflows: dict[str, Any] = Field(default_factory=dict)
    agent_graph: dict[str, Any] = Field(default_factory=dict)
    tools: dict[str, Any] = Field(default_factory=dict)
    prompts: dict[str, Any] = Field(default_factory=dict)
    schemas: dict[str, Any] = Field(default_factory=dict)
    decision_policy: dict[str, Any] = Field(default_factory=dict)
    qa_policy: dict[str, Any] = Field(default_factory=dict)
    strategy_snapshot: dict[str, Any] = Field(default_factory=dict)


class BehaviorVersionRecord(BaseModel):
    version: str
    released_at: str
    change_type: Literal["major", "minor", "patch", "initial"]
    summary: str
    event_ids: list[str] = Field(default_factory=list)


class DocumentationStateSnapshot(BaseModel):
    updated_at: str
    behavior_version: str
    manifest: BehaviorManifest | None = None
    artifacts: list[DocumentationArtifact] = Field(default_factory=list)
    versions: list[BehaviorVersionRecord] = Field(default_factory=list)


class ExplanationCitation(BaseModel):
    source_type: Literal["document", "documentation_event", "decision", "outcome", "qa_evaluation"]
    reference: str
    detail: str | None = None


class ExplainResponse(BaseModel):
    question: str
    answer: str
    citations: list[ExplanationCitation] = Field(default_factory=list)
    generated_at: str
