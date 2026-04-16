from __future__ import annotations

import hashlib
import inspect
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any, cast

from job_agent.agents._shared import agent_graph_spec
from job_agent.config import (
    DEFAULT_QA_SETTINGS,
    DEFAULT_WORKFLOW_INPUTS,
    PROMPTS_DIR,
    ROOT_DIR,
    SCHEMAS_DIR,
    get_model_name,
)
from job_agent.docs.models import (
    BehaviorManifest,
    BehaviorVersionRecord,
    DocumentationArtifact,
    DocumentationEvent,
    DocumentationEventType,
    DocumentationStateSnapshot,
    DocumentationUpdate,
    ExplainResponse,
    ExplanationCitation,
)
from job_agent.models import WorkflowOutput
from job_agent.runtime import DEFAULT_GMAIL_QUERIES, DEFAULT_SEARCH_SOURCES, FOLLOW_UP_DAYS, STALE_POSTING_DAYS
from job_agent.state import StateStore, isoformat, utc_now
from job_agent.tools.dedupe import normalize_text

DOCS_DIRNAME = "docs"
ARTIFACT_TITLES = {
    "architecture/overview.md": "Architecture Overview",
    "operations/guide.md": "Operations Guide",
    "developer/guide.md": "Developer Guide",
    "changelog/CHANGELOG.md": "Changelog",
    "decisions/behavior_versions.md": "Behavior Versions",
}
EVENT_TO_COMPONENT: dict[DocumentationEventType, str] = {
    "agent_graph_changed": "agent_graph",
    "workflow_changed": "workflows",
    "tool_surface_changed": "tools",
    "decision_policy_changed": "decision_policy",
    "qa_policy_changed": "qa_policy",
    "prompt_changed": "prompts",
    "schema_changed": "schemas",
    "strategy_changed": "strategy_snapshot",
}


def stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def hash_value(value: Any) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


def hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def bump_major(version: str) -> str:
    major, _minor, _patch = parse_version(version)
    return f"{major + 1}.0.0"


def bump_minor(version: str) -> str:
    major, minor, _patch = parse_version(version)
    return f"{major}.{minor + 1}.0"


def bump_patch(version: str) -> str:
    major, minor, patch = parse_version(version)
    return f"{major}.{minor}.{patch + 1}"


def parse_version(version: str) -> tuple[int, int, int]:
    parts = version.split(".")
    padded = [int(part) for part in parts[:3]]
    while len(padded) < 3:
        padded.append(0)
    return padded[0], padded[1], padded[2]


def redact_text(value: str | None) -> str | None:
    if not value:
        return value
    lowered = value.lower()
    if "oauth" in lowered or "token" in lowered or "secret" in lowered or "authorization" in lowered:
        return "[redacted]"
    if len(value) > 280:
        return f"{value[:277]}..."
    return value


def compact_diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    changed_keys = sorted(set(before) | set(after))
    return {
        key: {"before": before.get(key), "after": after.get(key)}
        for key in changed_keys
        if before.get(key) != after.get(key)
    }


class DocumentationService:
    def __init__(
        self,
        *,
        candidate_profile: dict[str, Any],
        state_store: StateStore,
        strategy_snapshot: Any | None = None,
        root_dir: Path | None = None,
        prompts_dir: Path | None = None,
        schemas_dir: Path | None = None,
    ) -> None:
        self.candidate_profile = candidate_profile
        self.state_store = state_store
        self.strategy_snapshot = strategy_snapshot
        self.root_dir = root_dir or ROOT_DIR
        self.docs_dir = self.root_dir / DOCS_DIRNAME
        self.prompts_dir = prompts_dir or PROMPTS_DIR
        self.schemas_dir = schemas_dir or SCHEMAS_DIR

    def refresh(self, *, workflow: str, output: WorkflowOutput | None = None) -> list[DocumentationUpdate]:
        previous_state = self.state_store.get_documentation_state()
        previous_manifest = previous_state.manifest if previous_state else None
        current_manifest = self.build_manifest()
        events = self._build_events(previous_manifest, current_manifest, workflow=workflow)
        existing_versions = list(previous_state.versions if previous_state else [])
        previous_version = previous_state.behavior_version if previous_state else "1.0.0"

        if previous_manifest is None:
            version = "1.0.0"
            change_type = "initial"
        elif self._has_major_change(previous_manifest, current_manifest):
            version = bump_major(previous_version)
            change_type = "major"
        elif events:
            version = bump_minor(previous_version)
            change_type = "minor"
        else:
            version = previous_version
            change_type = None

        version_records = list(existing_versions)
        if not version_records:
            version_records.append(
                BehaviorVersionRecord(
                    version=version,
                    released_at=current_manifest.created_at,
                    change_type="initial" if previous_manifest is None else "patch",
                    summary="Initial documentation baseline generated.",
                )
            )
        elif change_type in {"major", "minor"} and version != previous_version:
            version_records.append(
                BehaviorVersionRecord(
                    version=version,
                    released_at=current_manifest.created_at,
                    change_type=change_type,
                    summary=self._version_summary(events, fallback="Behavior changed and documentation was refreshed."),
                    event_ids=[event.event_id for event in events],
                )
            )

        history_events = self.state_store.list_documentation_events()
        existing_changelog_content = self._read_existing_artifact("changelog/CHANGELOG.md")
        rendered = self._render_artifacts(
            manifest=current_manifest,
            behavior_version=version,
            version_records=version_records,
            history_events=[*history_events, *events],
            current_events=events,
            existing_changelog_content=existing_changelog_content,
        )

        if previous_manifest is not None and not events and self._has_doc_only_changes(rendered, previous_state):
            version = bump_patch(previous_version)
            version_records.append(
                BehaviorVersionRecord(
                    version=version,
                    released_at=current_manifest.created_at,
                    change_type="patch",
                    summary="Documentation content changed without a behavior change.",
                )
            )
            rendered = self._render_artifacts(
                manifest=current_manifest,
                behavior_version=version,
                version_records=version_records,
                history_events=history_events,
                current_events=[],
                existing_changelog_content=existing_changelog_content,
            )

        for event in events:
            event.behavior_version = version
            if not event.summary:
                event.summary = self._humanize_event(event)

        artifact_models: list[DocumentationArtifact] = []
        updates: list[DocumentationUpdate] = []
        now = isoformat(utc_now())
        for relative_path, content in rendered.items():
            artifact_path = self.docs_dir / relative_path
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            new_hash = hash_text(content)
            existed = artifact_path.exists()
            previous_content = artifact_path.read_text(encoding="utf-8") if existed else None
            previous_hash = hash_text(previous_content) if previous_content is not None else None
            if previous_hash == new_hash:
                update_type = "unchanged"
            else:
                artifact_path.write_text(content, encoding="utf-8")
                update_type = "created" if not existed else "updated"

            title = ARTIFACT_TITLES[relative_path]
            artifact_models.append(
                DocumentationArtifact(
                    artifact_path=f"{DOCS_DIRNAME}/{relative_path}",
                    title=title,
                    source_event_ids=[event.event_id for event in events],
                    rendered_at=now,
                    content_hash=new_hash,
                    behavior_version=version,
                )
            )
            updates.append(
                DocumentationUpdate(
                    artifact_path=f"{DOCS_DIRNAME}/{relative_path}",
                    title=title,
                    update_type=update_type,
                    behavior_version=version,
                    summary=self._artifact_summary(update_type, events, version_records[-1].summary if version_records else None),
                )
            )

        if output is not None:
            output.documentation_updates.extend(updates)

        snapshot = DocumentationStateSnapshot(
            updated_at=now,
            behavior_version=version,
            manifest=current_manifest,
            artifacts=artifact_models,
            versions=version_records,
        )
        for event in events:
            self.state_store.append_documentation_event(event)
        self.state_store.save_documentation_state(snapshot)
        return updates

    def build_manifest(self) -> BehaviorManifest:
        workflows = {
            name: {
                "prompt": prompt,
                "supports_tracker": name in {"jobs", "gmail", "daily"},
                "supports_reflection": name in {"reflect", "daily"},
            }
            for name, prompt in DEFAULT_WORKFLOW_INPUTS.items()
        }
        agent_graph = self._build_agent_graph()
        tools = self._build_tool_surface(agent_graph)
        prompts = self._build_file_manifest(self.prompts_dir, "*.txt")
        schemas = self._build_file_manifest(self.schemas_dir, "*.json")
        decision_policy = {
            "thresholds": dict(self.candidate_profile.get("decision_thresholds") or {}),
            "search_sources": list(DEFAULT_SEARCH_SOURCES),
            "gmail_queries": list(DEFAULT_GMAIL_QUERIES),
            "stale_posting_days": STALE_POSTING_DAYS,
            "follow_up_days": FOLLOW_UP_DAYS,
            "salary_floor": self.candidate_profile.get("salary_floor"),
        }
        qa_policy = {
            **DEFAULT_QA_SETTINGS,
            **(self.candidate_profile.get("qa") or {}),
            "llm_override_env": os.getenv("JOB_AGENT_QA_LLM_JUDGE_ENABLED"),
        }
        strategy_snapshot = {}
        if self.strategy_snapshot is not None:
            if hasattr(self.strategy_snapshot, "model_dump"):
                strategy_snapshot = self.strategy_snapshot.model_dump()
            else:
                strategy_snapshot = dict(self.strategy_snapshot)
        public_interfaces = {
            "available_workflows": sorted(DEFAULT_WORKFLOW_INPUTS),
            "workflow_output_schema": WorkflowOutput.model_json_schema(),
        }
        components = {
            "public_interfaces": public_interfaces,
            "workflows": workflows,
            "agent_graph": agent_graph,
            "tools": tools,
            "prompts": prompts,
            "schemas": schemas,
            "decision_policy": decision_policy,
            "qa_policy": qa_policy,
            "strategy_snapshot": strategy_snapshot,
        }
        component_hashes = {name: hash_value(value) for name, value in components.items()}
        manifest_hash = hash_value(component_hashes)
        return BehaviorManifest(
            created_at=isoformat(utc_now()),
            manifest_hash=manifest_hash,
            component_hashes=component_hashes,
            public_interfaces=public_interfaces,
            workflows=workflows,
            agent_graph=agent_graph,
            tools=tools,
            prompts=prompts,
            schemas=schemas,
            decision_policy=decision_policy,
            qa_policy=qa_policy,
            strategy_snapshot=strategy_snapshot,
        )

    def _build_events(self, previous: BehaviorManifest | None, current: BehaviorManifest, *, workflow: str) -> list[DocumentationEvent]:
        if previous is None:
            return []
        events: list[DocumentationEvent] = []
        for event_type, component in EVENT_TO_COMPONENT.items():
            if previous.component_hashes.get(component) == current.component_hashes.get(component):
                continue
            before = getattr(previous, component)
            after = getattr(current, component)
            event = DocumentationEvent(
                event_id=str(uuid.uuid4()),
                timestamp=current.created_at,
                event_type=event_type,
                summary="",
                reason=self._event_reason(event_type, before=before, after=after),
                impact=self._event_impact(event_type),
                before=before if isinstance(before, dict) else {"value": before},
                after=after if isinstance(after, dict) else {"value": after},
                workflow=workflow,
            )
            event.summary = self._humanize_event(event)
            events.append(event)
        return events

    def _build_agent_graph(self) -> dict[str, Any]:
        return agent_graph_spec()

    def _build_tool_surface(self, agent_graph: dict[str, Any]) -> dict[str, Any]:
        tool_names = sorted({tool for spec in agent_graph.values() for tool in spec.get("tools", [])})
        tool_objects = {
            "search_jobs": __import__("job_agent.tools.jobs", fromlist=["search_jobs"]).search_jobs,
            "score_job_fit": __import__("job_agent.tools.jobs", fromlist=["score_job_fit"]).score_job_fit,
            "search_gmail_job_updates": __import__("job_agent.tools.gmail", fromlist=["search_gmail_job_updates"]).search_gmail_job_updates,
            "classify_job_email": __import__("job_agent.tools.gmail", fromlist=["classify_job_email"]).classify_job_email,
            "match_email_to_tracker": __import__("job_agent.tools.gmail", fromlist=["match_email_to_tracker"]).match_email_to_tracker,
            "read_tracker_sheet": __import__("job_agent.tools.sheets", fromlist=["read_tracker_sheet"]).read_tracker_sheet,
            "upsert_tracker_row": __import__("job_agent.tools.sheets", fromlist=["upsert_tracker_row"]).upsert_tracker_row,
        }
        surface = {}
        for name in tool_names:
            tool = tool_objects.get(name)
            if tool is None:
                continue
            surface[name] = {
                "module": getattr(tool, "__module__", ""),
                "signature": self._tool_signature(tool),
                "doc": inspect.getdoc(tool),
            }
        return surface

    def _build_file_manifest(self, directory: Path, pattern: str) -> dict[str, Any]:
        manifest = {}
        for path in sorted(directory.glob(pattern)):
            content = path.read_text(encoding="utf-8")
            manifest[path.name] = {
                "hash": hash_text(content),
                "lines": len(content.splitlines()),
            }
        return manifest

    def _read_existing_artifact(self, relative_path: str) -> str | None:
        artifact_path = self.docs_dir / relative_path
        if not artifact_path.exists():
            return None
        return artifact_path.read_text(encoding="utf-8")

    def _tool_name(self, tool: Any) -> str:
        return getattr(tool, "name", getattr(tool, "__name__", tool.__class__.__name__))

    def _tool_signature(self, tool: Any) -> str:
        try:
            return str(inspect.signature(tool))
        except (TypeError, ValueError):
            schema = getattr(tool, "params_json_schema", None) or {}
            properties = schema.get("properties", {})
            if properties:
                return f"({', '.join(sorted(properties))})"
            return "()"

    def _has_major_change(self, previous: BehaviorManifest, current: BehaviorManifest) -> bool:
        previous_workflows = set(previous.workflows)
        current_workflows = set(current.workflows)
        if previous_workflows - current_workflows:
            return True
        previous_schema_hash = previous.component_hashes.get("public_interfaces")
        current_schema_hash = current.component_hashes.get("public_interfaces")
        return previous_schema_hash != current_schema_hash

    def _has_doc_only_changes(self, rendered: dict[str, str], previous_state: DocumentationStateSnapshot | None) -> bool:
        if previous_state is None:
            return False
        previous_hashes = {artifact.artifact_path.removeprefix(f"{DOCS_DIRNAME}/"): artifact.content_hash for artifact in previous_state.artifacts}
        for relative_path, content in rendered.items():
            if previous_hashes.get(relative_path) != hash_text(content):
                return True
        return False

    def _event_reason(self, event_type: str, *, before: dict[str, Any], after: dict[str, Any]) -> str:
        diff = compact_diff(before, after)
        if not diff:
            return "The component hash changed, but no structured field diff was extracted."
        changed_keys = ", ".join(sorted(diff)[:5])
        return f"Changed fields: {changed_keys}."

    def _event_impact(self, event_type: str) -> str:
        impacts = {
            "agent_graph_changed": "Coordinator routing and specialist boundaries changed.",
            "workflow_changed": "Available workflow behavior or prompts changed.",
            "tool_surface_changed": "Specialist tools or tool contracts changed.",
            "decision_policy_changed": "Job scoring, filtering, or follow-up policy changed.",
            "qa_policy_changed": "Approval, flagging, or rejection rules changed.",
            "prompt_changed": "Agent instructions changed and may alter behavior.",
            "schema_changed": "Structured inputs or outputs changed.",
            "strategy_changed": "Adaptive weighting changed from reflection data.",
        }
        return impacts[event_type]

    def _humanize_event(self, event: DocumentationEvent) -> str:
        if os.getenv("JOB_AGENT_DOCS_LLM_ENABLED", "").lower() in {"1", "true", "yes"}:
            try:
                import openai as openai_module

                client = cast(Any, openai_module).OpenAI()
                prompt = (
                    "Write one short sentence explaining this system change in plain English.\n"
                    f"Event type: {event.event_type}\n"
                    f"Impact: {event.impact}\n"
                    f"Before: {event.before}\n"
                    f"After: {event.after}\n"
                    "Do not mention secrets or raw tokens."
                )
                response = client.responses.create(model=get_model_name(), input=prompt, max_output_tokens=80)
                if getattr(response, "output_text", None):
                    return response.output_text.strip()
            except Exception:
                pass

        diff = compact_diff(event.before, event.after)
        if event.event_type == "decision_policy_changed":
            changed = ", ".join(sorted(diff)[:4]) or "decision thresholds"
            return f"Updated job decision policy for {changed}."
        if event.event_type == "qa_policy_changed":
            changed = ", ".join(sorted(diff)[:4]) or "QA thresholds"
            return f"Adjusted QA policy for {changed}."
        if event.event_type == "strategy_changed":
            return "Reflection updated the adaptive strategy weights used to rank opportunities."
        if event.event_type == "prompt_changed":
            changed = ", ".join(sorted(diff)[:4]) or "agent prompts"
            return f"Changed prompt files affecting {changed}."
        if event.event_type == "schema_changed":
            changed = ", ".join(sorted(diff)[:4]) or "schemas"
            return f"Updated structured schema files for {changed}."
        if event.event_type == "tool_surface_changed":
            changed = ", ".join(sorted(diff)[:4]) or "tool contracts"
            return f"Changed the specialist tool surface for {changed}."
        if event.event_type == "agent_graph_changed":
            return "Updated agent handoffs or tool ownership in the orchestration graph."
        return "Updated workflow definitions or output contracts."

    def _artifact_summary(self, update_type: str, events: list[DocumentationEvent], version_summary: str | None) -> str:
        if update_type == "unchanged":
            return "No documentation changes detected."
        if events:
            return self._version_summary(events, fallback=version_summary or "Documentation was refreshed.")
        return version_summary or "Documentation was refreshed."

    def _version_summary(self, events: list[DocumentationEvent], fallback: str) -> str:
        if not events:
            return fallback
        summaries = [event.summary for event in events[:2] if event.summary]
        return " ".join(summaries) if summaries else fallback

    def _render_artifacts(
        self,
        *,
        manifest: BehaviorManifest,
        behavior_version: str,
        version_records: list[BehaviorVersionRecord],
        history_events: list[DocumentationEvent],
        current_events: list[DocumentationEvent],
        existing_changelog_content: str | None = None,
    ) -> dict[str, str]:
        return {
            "architecture/overview.md": self._render_architecture(manifest, behavior_version),
            "operations/guide.md": self._render_operations(manifest, behavior_version),
            "developer/guide.md": self._render_developer(manifest, behavior_version),
            "changelog/CHANGELOG.md": self._render_changelog(
                current_events if existing_changelog_content else history_events,
                behavior_version,
                existing_content=existing_changelog_content,
            ),
            "decisions/behavior_versions.md": self._render_behavior_versions(version_records, behavior_version, current_events),
        }

    def _render_architecture(self, manifest: BehaviorManifest, behavior_version: str) -> str:
        workflow_lines = "\n".join(
            f"- `{name}`: {details['prompt']}"
            for name, details in sorted(manifest.workflows.items())
        )
        agent_lines = "\n".join(
            f"- `{name}` hands off to {', '.join(details.get('handoffs') or ['no specialists'])} and uses tools: {', '.join(details.get('tools') or ['none'])}."
            for name, details in sorted(manifest.agent_graph.items())
        )
        tool_lines = "\n".join(
            f"- `{name}` {details.get('signature', '')}: {details.get('doc') or 'No docstring provided.'}"
            for name, details in sorted(manifest.tools.items())
        )
        schema_lines = "\n".join(f"- `{name}`" for name in sorted(manifest.schemas))
        return (
            "# Architecture Overview\n\n"
            f"Current behavior version: `{behavior_version}`\n\n"
            "The system finds jobs, scores them, optionally drafts versioned tailored resumes, scans Gmail for updates, syncs the tracker, and reflects on outcomes to adjust strategy.\n\n"
            "## Workflows\n\n"
            f"{workflow_lines}\n\n"
            "## Agent Graph\n\n"
            f"{agent_lines}\n\n"
            "## Tool Surface\n\n"
            f"{tool_lines}\n\n"
            "## Schemas\n\n"
            f"{schema_lines}\n"
        )

    def _render_operations(self, manifest: BehaviorManifest, behavior_version: str) -> str:
        decision_policy = manifest.decision_policy
        qa_policy = manifest.qa_policy
        has_resume_references = bool(self.candidate_profile.get("resume_reference_documents"))
        return (
            "# Operations Guide\n\n"
            f"Current behavior version: `{behavior_version}`\n\n"
            "Run the preset workflows through `python app.py --workflow <daily|jobs|gmail|reflect>`.\n\n"
            "## Decision Rules\n\n"
            f"- Salary floor: `{decision_policy.get('salary_floor')}`\n"
            f"- Thresholds: `{decision_policy.get('thresholds')}`\n"
            f"- Follow-up delay: `{decision_policy.get('follow_up_days')}` business days\n"
            f"- Search sources: `{', '.join(decision_policy.get('search_sources', []))}`\n\n"
            "## QA Gates\n\n"
            f"- Approve threshold: `{qa_policy.get('approve_threshold')}`\n"
            f"- Flag threshold: `{qa_policy.get('flag_threshold')}`\n"
            f"- LLM judge enabled: `{qa_policy.get('llm_judge_enabled')}`\n"
            f"- Duplicate company cooldown: `{qa_policy.get('duplicate_company_cooldown_days')}` days\n\n"
            "## Resume Tailoring\n\n"
            "- Jobs marked `tailor_resume = yes` can generate versioned resume drafts during the `jobs` workflow.\n"
            "- Drafts are written under `output/doc/resumes/` and the generated `resume_version` is stored on the tracker row.\n"
            f"- Resume reference documents configured: `{has_resume_references}`\n"
            "- Resume generation failures are surfaced in `needs_review` as `resume_generation_unavailable` instead of silently skipping the issue.\n\n"
            "## Documentation Refresh\n\n"
            "- Preset workflows refresh documentation after completion.\n"
            "- Docs are rewritten only when content changes.\n"
            "- Explain queries read generated docs plus recent decisions, outcomes, and QA records.\n"
        )

    def _render_developer(self, manifest: BehaviorManifest, behavior_version: str) -> str:
        prompt_lines = "\n".join(f"- `{name}`" for name in sorted(manifest.prompts))
        schema_lines = "\n".join(f"- `{name}`" for name in sorted(manifest.schemas))
        return (
            "# Developer Guide\n\n"
            f"Current behavior version: `{behavior_version}`\n\n"
            "Add behavior changes in code, then let the documentation service capture the new manifest and render guides.\n\n"
            "## Change Rules\n\n"
            "- Workflow, tool, prompt, schema, decision, QA, and strategy changes are tracked through manifest diffs.\n"
            "- Major versions are reserved for workflow removals or output contract changes.\n"
            "- Minor versions cover new or changed behavior.\n"
            "- Patch versions cover documentation-only refreshes.\n\n"
            "## Working Surface\n\n"
            f"- Prompt files: \n{prompt_lines}\n"
            f"- Schemas live under `schemas/`:\n{schema_lines}\n"
            "- `WorkflowOutput` is a public interface. Changes such as `resume_artifacts` should be treated as contract changes.\n"
            "- Resume drafting behavior is split between `job_agent/resume.py`, `job_agent/agents/resume_writer.py`, and tracker sync in the orchestrator.\n"
            "- The explain path is available through `python app.py --explain \"<question>\"`.\n"
        )

    def _render_changelog(
        self,
        events: list[DocumentationEvent],
        behavior_version: str,
        *,
        existing_content: str | None = None,
    ) -> str:
        appended_body = self._render_changelog_events(events)
        if existing_content:
            content = self._update_changelog_version(existing_content, behavior_version)
            if not events:
                return content if content.endswith("\n") else f"{content}\n"
            if "- No documented behavior changes yet." in content and content.count("## ") == 0:
                return f"# Changelog\n\nCurrent behavior version: `{behavior_version}`\n\n{appended_body}\n"
            return f"{content.rstrip()}\n\n{appended_body}\n"

        if not events:
            appended_body = "- No documented behavior changes yet."
        return f"# Changelog\n\nCurrent behavior version: `{behavior_version}`\n\n{appended_body}\n"

    def _render_changelog_events(self, events: list[DocumentationEvent]) -> str:
        if not events:
            return ""
        grouped: dict[str, list[DocumentationEvent]] = {}
        for event in events:
            grouped.setdefault(event.timestamp[:10], []).append(event)
        sections = []
        for day in sorted(grouped):
            lines = "\n".join(
                f"- {event.summary} (`{event.event_type}`)"
                for event in grouped[day]
            )
            sections.append(f"## {day}\n\n{lines}")
        return "\n\n".join(sections)

    def _update_changelog_version(self, content: str, behavior_version: str) -> str:
        pattern = r"Current behavior version: `[^`]+`"
        replacement = f"Current behavior version: `{behavior_version}`"
        if re.search(pattern, content):
            return re.sub(pattern, replacement, content, count=1)
        return f"# Changelog\n\n{replacement}\n\n{content.lstrip()}"

    def _render_behavior_versions(
        self,
        version_records: list[BehaviorVersionRecord],
        behavior_version: str,
        current_events: list[DocumentationEvent],
    ) -> str:
        records = list(version_records)
        if not records:
            records.append(
                BehaviorVersionRecord(
                    version=behavior_version,
                    released_at=isoformat(utc_now()),
                    change_type="initial",
                    summary="Initial documentation baseline generated.",
                )
            )
        lines = "\n".join(
            f"- `{record.version}` ({record.change_type}) on {record.released_at[:10]}: {record.summary}"
            for record in records
        )
        current_change_lines = "\n".join(f"- {event.summary}" for event in current_events) or "- No new behavior changes in this refresh."
        return (
            "# Behavior Versions\n\n"
            f"Current behavior version: `{behavior_version}`\n\n"
            "## Version History\n\n"
            f"{lines}\n\n"
            "## Current Refresh\n\n"
            f"{current_change_lines}\n"
        )


class ExplainService:
    def __init__(
        self,
        *,
        candidate_profile: dict[str, Any],
        state_store: StateStore,
        strategy_snapshot: Any | None = None,
        root_dir: Path | None = None,
    ) -> None:
        self.candidate_profile = candidate_profile
        self.state_store = state_store
        self.strategy_snapshot = strategy_snapshot
        self.root_dir = root_dir or ROOT_DIR
        self.documentation_service = DocumentationService(
            candidate_profile=candidate_profile,
            state_store=state_store,
            strategy_snapshot=strategy_snapshot,
            root_dir=self.root_dir,
        )

    def explain(self, question: str) -> ExplainResponse:
        normalized = normalize_text(question)
        generated_at = isoformat(utc_now())
        if "what changed" in normalized or "this week" in normalized:
            return self._explain_recent_changes(question, generated_at)
        if "why" in normalized and ("reject" in normalized or "rejected" in normalized or "skip" in normalized):
            return self._explain_rejection(question, generated_at)
        return self._explain_system(question, generated_at)

    def _explain_recent_changes(self, question: str, generated_at: str) -> ExplainResponse:
        events = self.state_store.list_documentation_events(lookback_days=7)
        if not events:
            return ExplainResponse(
                question=question,
                answer="No documented behavior changes were recorded in the last 7 days.",
                citations=[ExplanationCitation(source_type="document", reference="docs/changelog/CHANGELOG.md")],
                generated_at=generated_at,
            )
        answer = "Recent behavior changes: " + " ".join(event.summary for event in events[:3])
        citations = [
            ExplanationCitation(
                source_type="documentation_event",
                reference=event.event_id,
                detail=event.summary,
            )
            for event in events[:3]
        ]
        citations.append(ExplanationCitation(source_type="document", reference="docs/changelog/CHANGELOG.md"))
        return ExplainResponse(question=question, answer=answer, citations=citations, generated_at=generated_at)

    def _explain_rejection(self, question: str, generated_at: str) -> ExplainResponse:
        decisions = self.state_store.list_decisions(lookback_days=30)
        recent_skips = [decision for decision in reversed(decisions) if decision.action == "skip"]
        if not recent_skips:
            return ExplainResponse(
                question=question,
                answer="I could not find a recent rejected or skipped job decision in state history.",
                citations=[],
                generated_at=generated_at,
            )
        decision = recent_skips[0]
        answer = (
            f"The most recent skipped job was `{decision.role_title}` at `{decision.company}`. "
            f"It was skipped with score {decision.final_score} because {decision.rationale}"
        )
        return ExplainResponse(
            question=question,
            answer=answer,
            citations=[
                ExplanationCitation(
                    source_type="decision",
                    reference=decision.decision_id,
                    detail=f"{decision.company} | {decision.role_title}",
                )
            ],
            generated_at=generated_at,
        )

    def _explain_system(self, question: str, generated_at: str) -> ExplainResponse:
        manifest = self.documentation_service.build_manifest()
        workflows = ", ".join(sorted(manifest.workflows))
        answer = (
            "The system runs four preset workflows. "
            f"`{workflows}` cover job intake, Gmail monitoring, tracker sync, and reflection. "
            "Jobs are scored against the candidate profile, QA can block unsafe actions, and documentation refresh runs after preset workflows."
        )
        return ExplainResponse(
            question=question,
            answer=answer,
            citations=[
                ExplanationCitation(source_type="document", reference="docs/architecture/overview.md"),
                ExplanationCitation(source_type="document", reference="docs/operations/guide.md"),
            ],
            generated_at=generated_at,
        )
