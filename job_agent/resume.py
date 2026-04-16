from __future__ import annotations

import json
import re
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any, cast

import pydantic as pydantic_module

from job_agent.models import ResumeArtifact
from job_agent.tools.dedupe import normalize_text

BaseModel = cast(Any, pydantic_module).BaseModel
Field = cast(Any, pydantic_module).Field

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_REFERENCE_VERSION = "v1.0"
GENERATED_VERSION_PATTERN = re.compile(r"__v(?P<major>\d+)\.(?P<minor>\d+)$")


class ResumeExperienceEntry(BaseModel):
    company: str
    title: str
    dates: str
    bullets: list[str] = Field(default_factory=list)


class ResumeDraft(BaseModel):
    full_name: str
    target_role: str
    headline: str
    professional_summary: list[str] = Field(default_factory=list)
    core_skills: list[str] = Field(default_factory=list)
    experience: list[ResumeExperienceEntry] = Field(default_factory=list)
    education: list[str] = Field(default_factory=list)


def versioned_label(label: str, version: str) -> str:
    if normalize_text(version) in normalize_text(label):
        return label
    return f"{label} ({version})"


def normalize_resume_reference_documents(documents: Any) -> list[dict[str, Any]]:
    if not isinstance(documents, list):
        return []

    normalized_documents: list[dict[str, Any]] = []
    for index, raw_document in enumerate(documents, start=1):
        if not isinstance(raw_document, dict):
            continue

        path = str(raw_document.get("path") or "").strip()
        label = str(raw_document.get("label") or Path(path).stem or f"Resume Reference {index}").strip()
        version = str(raw_document.get("version") or DEFAULT_REFERENCE_VERSION).strip() or DEFAULT_REFERENCE_VERSION
        notes = raw_document.get("notes") if isinstance(raw_document.get("notes"), list) else []
        normalized_documents.append(
            {
                **raw_document,
                "label": versioned_label(label, version),
                "version": version,
                "path": path,
                "notes": [str(note).strip() for note in notes if str(note).strip()],
            }
        )

    return normalized_documents


def slugify(value: str | None) -> str:
    normalized = normalize_text(value).replace(" ", "-")
    collapsed = re.sub(r"[^a-z0-9-]+", "", normalized)
    collapsed = re.sub(r"-{2,}", "-", collapsed).strip("-")
    return collapsed or "resume"


def extract_reference_document_text(path: str) -> str | None:
    document_path = Path(path)
    if not path or not document_path.exists():
        return None

    suffix = document_path.suffix.lower()
    if suffix in {".txt", ".md"}:
        return document_path.read_text(encoding="utf-8")

    if suffix == ".docx":
        try:
            with zipfile.ZipFile(document_path) as archive:
                document_xml = archive.read("word/document.xml")
            root = ET.fromstring(document_xml)
            paragraphs: list[str] = []
            namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            for paragraph in root.findall(".//w:p", namespace):
                runs = [node.text or "" for node in paragraph.findall(".//w:t", namespace)]
                text = "".join(runs).strip()
                if text:
                    paragraphs.append(text)
            if paragraphs:
                return "\n".join(paragraphs)
        except (KeyError, ET.ParseError, zipfile.BadZipFile):
            pass
        try:
            result = subprocess.run(
                ["textutil", "-convert", "txt", "-stdout", str(document_path)],
                capture_output=True,
                text=True,
                check=False,
            )
        except (FileNotFoundError, PermissionError):
            return None
        if result.returncode == 0:
            return result.stdout
        return None

    if suffix == ".pdf":
        try:
            result = subprocess.run(
                ["pdftotext", str(document_path), "-"],
                capture_output=True,
                text=True,
                check=False,
            )
        except (FileNotFoundError, PermissionError):
            return None
        if result.returncode == 0:
            return result.stdout
        return None

    return None


def build_resume_reference_packets(candidate_profile: dict[str, Any]) -> list[dict[str, Any]]:
    packets: list[dict[str, Any]] = []
    for document in normalize_resume_reference_documents(candidate_profile.get("resume_reference_documents")):
        extracted_text = extract_reference_document_text(document["path"])
        packets.append(
            {
                "label": document["label"],
                "version": document["version"],
                "kind": document.get("kind"),
                "target_focus": document.get("target_focus"),
                "path": document["path"],
                "notes": document.get("notes") or [],
                "content_available": extracted_text is not None,
                "content": (extracted_text or "").strip()[:12000] or None,
            }
        )
    return packets


def build_resume_artifact_slug(*, company: str | None, role_title: str | None) -> str:
    return f"{slugify(company)}__{slugify(role_title)}"


def next_generated_resume_version(output_dir: Path, *, company: str | None, role_title: str | None) -> str:
    artifact_slug = build_resume_artifact_slug(company=company, role_title=role_title)
    highest_version: tuple[int, int] | None = None

    for artifact_path in output_dir.glob(f"{artifact_slug}__v*.md"):
        match = GENERATED_VERSION_PATTERN.search(artifact_path.stem)
        if match is None:
            continue
        candidate = (int(match.group("major")), int(match.group("minor")))
        if highest_version is None or candidate > highest_version:
            highest_version = candidate

    if highest_version is None:
        return "v1.0"
    major, minor = highest_version
    return f"v{major}.{minor + 1}"


def render_resume_markdown(
    draft: ResumeDraft,
    *,
    version: str,
    company: str | None,
    source_labels: list[str],
) -> str:
    lines = [
        f"# {draft.full_name}",
        f"Target Role: {draft.target_role}",
        f"Version: {version}",
    ]
    if company:
        lines.append(f"Tailored For: {company}")
    if source_labels:
        lines.append(f"Reference Set: {', '.join(source_labels)}")

    lines.extend(
        [
            "",
            f"## {draft.headline}",
            "",
            "## Professional Summary",
        ]
    )
    lines.extend(f"- {bullet}" for bullet in draft.professional_summary if bullet.strip())
    lines.extend(["", "## Core Skills"])
    lines.extend(f"- {skill}" for skill in draft.core_skills if skill.strip())
    lines.extend(["", "## Experience"])
    for entry in draft.experience:
        lines.extend(
            [
                f"### {entry.company}",
                f"{entry.title} | {entry.dates}",
            ]
        )
        lines.extend(f"- {bullet}" for bullet in entry.bullets if bullet.strip())
        lines.append("")

    if draft.education:
        lines.append("## Education")
        lines.extend(f"- {item}" for item in draft.education if item.strip())
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def write_resume_artifact(
    *,
    draft: ResumeDraft,
    company: str | None,
    role_title: str | None,
    source_labels: list[str],
    root_dir: Path | None = None,
) -> ResumeArtifact:
    output_dir = (root_dir or ROOT_DIR) / "output" / "doc" / "resumes"
    output_dir.mkdir(parents=True, exist_ok=True)
    version = next_generated_resume_version(output_dir, company=company, role_title=role_title)
    artifact_slug = build_resume_artifact_slug(company=company, role_title=role_title)
    artifact_path = output_dir / f"{artifact_slug}__{version}.md"
    artifact_path.write_text(
        render_resume_markdown(
            draft,
            version=version,
            company=company,
            source_labels=source_labels,
        ),
        encoding="utf-8",
    )
    return ResumeArtifact(
        company=company,
        role_title=role_title,
        version=version,
        output_path=str(artifact_path),
        format="markdown",
        source_labels=source_labels,
    )


def validate_resume_draft(raw_output: Any) -> ResumeDraft:
    if isinstance(raw_output, ResumeDraft):
        return raw_output
    if hasattr(raw_output, "model_dump"):
        raw_output = raw_output.model_dump()
    if isinstance(raw_output, dict):
        return ResumeDraft.model_validate(raw_output)
    if isinstance(raw_output, str):
        return ResumeDraft.model_validate(json.loads(raw_output))
    raise TypeError("Resume writer returned an unexpected payload.")


def generate_resume_artifact_impl(
    *,
    candidate_profile: dict[str, Any],
    job: dict[str, Any],
    runner_cls: Any | None = None,
    root_dir: Path | None = None,
) -> dict[str, Any]:
    reference_documents = build_resume_reference_packets(candidate_profile)
    if not reference_documents:
        return {
            "implemented": False,
            "reason": "No resume reference documents are configured for resume drafting.",
        }

    available_documents = [document for document in reference_documents if document.get("content_available")]
    if not available_documents:
        return {
            "implemented": False,
            "reason": "Reference resume files are configured, but their contents could not be extracted.",
        }

    if runner_cls is None:
        import agents as agents_module

        runner_cls = cast(Any, agents_module).Runner

    from job_agent.agents.resume_writer import build_resume_writer_agent

    agent = build_resume_writer_agent(
        candidate_profile,
        job=job,
        reference_documents=reference_documents,
    )
    result = runner_cls.run_sync(
        agent,
        "Draft a versioned tailored resume for this opportunity using the reference documents and job details.",
    )
    draft = validate_resume_draft(result.final_output)
    artifact = write_resume_artifact(
        draft=draft,
        company=job.get("company"),
        role_title=job.get("role_title"),
        source_labels=[document["label"] for document in reference_documents],
        root_dir=root_dir,
    )
    return {
        "implemented": True,
        "artifact": artifact.model_dump(),
    }
