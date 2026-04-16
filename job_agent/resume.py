from __future__ import annotations

import json
import re
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from copy import deepcopy
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
DOCX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
XML_NS = "http://www.w3.org/XML/1998/namespace"
WPML_NAMESPACES = {
    "wpc": "http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas",
    "cx": "http://schemas.microsoft.com/office/drawing/2014/chartex",
    "cx1": "http://schemas.microsoft.com/office/drawing/2015/9/8/chartex",
    "cx2": "http://schemas.microsoft.com/office/drawing/2015/10/21/chartex",
    "cx3": "http://schemas.microsoft.com/office/drawing/2016/5/9/chartex",
    "cx4": "http://schemas.microsoft.com/office/drawing/2016/5/10/chartex",
    "cx5": "http://schemas.microsoft.com/office/drawing/2016/5/11/chartex",
    "cx6": "http://schemas.microsoft.com/office/drawing/2016/5/12/chartex",
    "cx7": "http://schemas.microsoft.com/office/drawing/2016/5/13/chartex",
    "cx8": "http://schemas.microsoft.com/office/drawing/2016/5/14/chartex",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "aink": "http://schemas.microsoft.com/office/drawing/2016/ink",
    "am3d": "http://schemas.microsoft.com/office/drawing/2017/model3d",
    "o": "urn:schemas-microsoft-com:office:office",
    "oel": "http://schemas.microsoft.com/office/2019/extlst",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "v": "urn:schemas-microsoft-com:vml",
    "wp14": "http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "w10": "urn:schemas-microsoft-com:office:word",
    "w": W_NS,
    "w14": "http://schemas.microsoft.com/office/word/2010/wordml",
    "w15": "http://schemas.microsoft.com/office/word/2012/wordml",
    "w16cex": "http://schemas.microsoft.com/office/word/2018/wordml/cex",
    "w16cid": "http://schemas.microsoft.com/office/word/2016/wordml/cid",
    "w16": "http://schemas.microsoft.com/office/word/2018/wordml",
    "w16du": "http://schemas.microsoft.com/office/word/2023/wordml/word16du",
    "w16sdtdh": "http://schemas.microsoft.com/office/word/2020/wordml/sdtdatahash",
    "w16se": "http://schemas.microsoft.com/office/word/2015/wordml/symex",
    "wpg": "http://schemas.microsoft.com/office/word/2010/wordprocessingGroup",
    "wpi": "http://schemas.microsoft.com/office/word/2010/wordprocessingInk",
    "wne": "http://schemas.microsoft.com/office/word/2006/wordml",
    "wps": "http://schemas.microsoft.com/office/word/2010/wordprocessingShape",
}

for prefix, uri in WPML_NAMESPACES.items():
    ET.register_namespace(prefix, uri)


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

    for artifact_path in output_dir.glob(f"{artifact_slug}__v*"):
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


def w_tag(name: str) -> str:
    return f"{{{W_NS}}}{name}"


def _w_attr(name: str) -> str:
    return f"{{{W_NS}}}{name}"


def _paragraph_text(paragraph: ET.Element) -> str:
    return "".join(node.text or "" for node in paragraph.findall(f".//{w_tag('t')}")).strip()


def _paragraph_style(paragraph: ET.Element) -> str | None:
    style = paragraph.find(f"./{w_tag('pPr')}/{w_tag('pStyle')}")
    return style.get(_w_attr("val")) if style is not None else None


def _paragraph_has_numbering(paragraph: ET.Element) -> bool:
    return paragraph.find(f"./{w_tag('pPr')}/{w_tag('numPr')}") is not None


def _first_run_properties(paragraph: ET.Element) -> ET.Element | None:
    run_properties = paragraph.find(f"./{w_tag('r')}/{w_tag('rPr')}")
    return deepcopy(run_properties) if run_properties is not None else None


def _paragraph_properties(paragraph: ET.Element) -> ET.Element | None:
    properties = paragraph.find(f"./{w_tag('pPr')}")
    return deepcopy(properties) if properties is not None else None


def _extract_docx_template_parts(root: ET.Element) -> dict[str, ET.Element | None]:
    paragraphs = [paragraph for paragraph in root.findall(f".//{w_tag('p')}") if _paragraph_text(paragraph)]
    parts: dict[str, ET.Element | None] = {
        "heading_p_pr": None,
        "heading_r_pr": None,
        "section_p_pr": None,
        "section_r_pr": None,
        "company_p_pr": None,
        "company_r_pr": None,
        "role_p_pr": None,
        "role_r_pr": None,
        "body_p_pr": None,
        "body_r_pr": None,
        "bullet_p_pr": None,
        "bullet_r_pr": None,
    }

    for paragraph in paragraphs:
        text = _paragraph_text(paragraph)
        style = _paragraph_style(paragraph)
        numbered = _paragraph_has_numbering(paragraph)
        if style == "Heading3" and parts["heading_p_pr"] is None:
            parts["heading_p_pr"] = _paragraph_properties(paragraph)
            parts["heading_r_pr"] = _first_run_properties(paragraph)
            continue
        if text.lower() == "experience" and parts["section_p_pr"] is None:
            parts["section_p_pr"] = _paragraph_properties(paragraph)
            parts["section_r_pr"] = _first_run_properties(paragraph)
            continue
        if numbered and parts["bullet_p_pr"] is None:
            parts["bullet_p_pr"] = _paragraph_properties(paragraph)
            parts["bullet_r_pr"] = _first_run_properties(paragraph)
            continue
        if not numbered and style is None and parts["body_p_pr"] is None and len(text.split()) > 6:
            parts["body_p_pr"] = _paragraph_properties(paragraph)
            parts["body_r_pr"] = _first_run_properties(paragraph)

    for index, paragraph in enumerate(paragraphs):
        if _paragraph_text(paragraph).lower() != "experience":
            continue
        company = next((candidate for candidate in paragraphs[index + 1 :] if not _paragraph_has_numbering(candidate)), None)
        role = next(
            (
                candidate
                for candidate in paragraphs[index + 2 :]
                if not _paragraph_has_numbering(candidate) and "|" in _paragraph_text(candidate)
            ),
            None,
        )
        if company is not None:
            parts["company_p_pr"] = _paragraph_properties(company)
            parts["company_r_pr"] = _first_run_properties(company)
        if role is not None:
            parts["role_p_pr"] = _paragraph_properties(role)
            parts["role_r_pr"] = _first_run_properties(role)
        break

    return parts


def _style_paragraph_properties(style_id: str) -> ET.Element:
    properties = ET.Element(w_tag("pPr"))
    style = ET.SubElement(properties, w_tag("pStyle"))
    style.set(_w_attr("val"), style_id)
    return properties


def _bold_run_properties() -> ET.Element:
    properties = ET.Element(w_tag("rPr"))
    ET.SubElement(properties, w_tag("b")).set(_w_attr("val"), "1")
    ET.SubElement(properties, w_tag("bCs")).set(_w_attr("val"), "1")
    return properties


def _clone_or_fallback(candidate: ET.Element | None, fallback: ET.Element | None = None) -> ET.Element | None:
    if candidate is not None:
        return deepcopy(candidate)
    return deepcopy(fallback) if fallback is not None else None


def _make_run(text: str, run_properties: ET.Element | None = None) -> ET.Element:
    run = ET.Element(w_tag("r"))
    if run_properties is not None:
        run.append(deepcopy(run_properties))
    text_node = ET.SubElement(run, w_tag("t"))
    if text.startswith(" ") or text.endswith(" "):
        text_node.set(f"{{{XML_NS}}}space", "preserve")
    text_node.text = text
    return run


def _make_paragraph(
    text: str | list[tuple[str, ET.Element | None]],
    *,
    paragraph_properties: ET.Element | None = None,
    run_properties: ET.Element | None = None,
) -> ET.Element:
    paragraph = ET.Element(w_tag("p"))
    if paragraph_properties is not None:
        paragraph.append(deepcopy(paragraph_properties))

    segments = [(text, run_properties)] if isinstance(text, str) else text
    for segment_text, segment_run_properties in segments:
        if segment_text:
            paragraph.append(_make_run(segment_text, segment_run_properties))
    return paragraph


def render_resume_docx(
    *,
    draft: ResumeDraft,
    version: str,
    company: str | None,
    source_labels: list[str],
    template_path: Path,
    output_path: Path,
) -> None:
    with zipfile.ZipFile(template_path) as source_archive:
        document_xml = source_archive.read("word/document.xml")
        root = ET.fromstring(document_xml)
        template_parts = _extract_docx_template_parts(root)
        body = root.find(w_tag("body"))
        if body is None:
            raise ValueError("Resume DOCX template does not contain a Word document body.")
        section_properties = body.find(w_tag("sectPr"))
        for child in list(body):
            body.remove(child)

        heading_p_pr = _clone_or_fallback(template_parts["heading_p_pr"], _style_paragraph_properties("Heading3"))
        heading_r_pr = _clone_or_fallback(template_parts["heading_r_pr"], _bold_run_properties())
        section_p_pr = _clone_or_fallback(template_parts["section_p_pr"], heading_p_pr)
        section_r_pr = _clone_or_fallback(template_parts["section_r_pr"], heading_r_pr)
        company_p_pr = _clone_or_fallback(template_parts["company_p_pr"], template_parts["body_p_pr"])
        company_r_pr = _clone_or_fallback(template_parts["company_r_pr"], _bold_run_properties())
        role_p_pr = _clone_or_fallback(template_parts["role_p_pr"], template_parts["body_p_pr"])
        role_r_pr = _clone_or_fallback(template_parts["role_r_pr"], _bold_run_properties())
        body_p_pr = _clone_or_fallback(template_parts["body_p_pr"])
        body_r_pr = _clone_or_fallback(template_parts["body_r_pr"])
        bullet_p_pr = _clone_or_fallback(template_parts["bullet_p_pr"], body_p_pr)
        bullet_r_pr = _clone_or_fallback(template_parts["bullet_r_pr"], body_r_pr)

        metadata = [f"Target Role: {draft.target_role}", f"Version: {version}"]
        if company:
            metadata.append(f"Tailored For: {company}")
        if source_labels:
            metadata.append(f"Reference Set: {', '.join(source_labels)}")

        paragraphs = [
            _make_paragraph(draft.full_name, paragraph_properties=_style_paragraph_properties("Title"), run_properties=heading_r_pr),
            _make_paragraph(" | ".join(metadata), paragraph_properties=body_p_pr, run_properties=body_r_pr),
            _make_paragraph("Professional Summary", paragraph_properties=heading_p_pr, run_properties=heading_r_pr),
        ]
        paragraphs.extend(
            _make_paragraph(summary, paragraph_properties=body_p_pr, run_properties=body_r_pr)
            for summary in draft.professional_summary
            if summary.strip()
        )
        paragraphs.append(_make_paragraph("Experience", paragraph_properties=section_p_pr, run_properties=section_r_pr))

        for entry in draft.experience:
            if entry.company.strip():
                paragraphs.append(_make_paragraph(entry.company, paragraph_properties=company_p_pr, run_properties=company_r_pr))
            role_line = " | ".join(part for part in (entry.title, entry.dates) if part.strip())
            if role_line:
                paragraphs.append(_make_paragraph(role_line, paragraph_properties=role_p_pr, run_properties=role_r_pr))
            paragraphs.extend(
                _make_paragraph(bullet, paragraph_properties=bullet_p_pr, run_properties=bullet_r_pr)
                for bullet in entry.bullets
                if bullet.strip()
            )

        if draft.core_skills:
            paragraphs.append(_make_paragraph("Core Skills", paragraph_properties=heading_p_pr, run_properties=heading_r_pr))
            paragraphs.extend(
                _make_paragraph(skill, paragraph_properties=bullet_p_pr, run_properties=bullet_r_pr)
                for skill in draft.core_skills
                if skill.strip()
            )

        if draft.education:
            paragraphs.append(_make_paragraph("Education", paragraph_properties=heading_p_pr, run_properties=heading_r_pr))
            paragraphs.extend(
                _make_paragraph(item, paragraph_properties=bullet_p_pr, run_properties=bullet_r_pr)
                for item in draft.education
                if item.strip()
            )

        for paragraph in paragraphs:
            body.append(paragraph)
        if section_properties is not None:
            body.append(section_properties)

        rendered_document_xml = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as output_archive:
            for item in source_archive.infolist():
                if item.filename == "word/document.xml":
                    output_archive.writestr(item, rendered_document_xml)
                    continue
                output_archive.writestr(item, source_archive.read(item.filename))


def resolve_resume_template_path(candidate_profile: dict[str, Any]) -> Path | None:
    configured_template = str(candidate_profile.get("resume_template_document_path") or "").strip()
    if configured_template:
        return Path(configured_template)

    for document in normalize_resume_reference_documents(candidate_profile.get("resume_reference_documents")):
        path = Path(document.get("path") or "")
        if path.suffix.lower() == ".docx" and document.get("kind") == "resume":
            return path

    return None


def publish_resume_google_doc(
    *,
    artifact: ResumeArtifact,
    candidate_profile: dict[str, Any],
    google_doc_name: str,
) -> ResumeArtifact:
    if not artifact.docx_path:
        return artifact
    folder_id = str(candidate_profile.get("resume_google_drive_folder_id") or "").strip() or None
    folder_url = str(candidate_profile.get("resume_google_drive_folder_url") or "").strip() or None
    if not folder_id and not folder_url:
        artifact.google_doc_error = "Resume Google Doc publishing is not configured with a Drive folder ID or URL."
        return artifact

    from job_agent.tools.drive import upload_docx_as_google_doc_impl

    result = upload_docx_as_google_doc_impl(
        docx_path=artifact.docx_path,
        name=google_doc_name,
        folder_id=folder_id,
        folder_url=folder_url,
    )
    if result.get("implemented"):
        artifact.google_doc_id = result.get("google_doc_id")
        artifact.google_doc_url = result.get("google_doc_url")
    else:
        artifact.google_doc_error = str(result.get("reason") or "Google Docs publishing failed.")
    return artifact


def write_resume_artifact(
    *,
    draft: ResumeDraft,
    company: str | None,
    role_title: str | None,
    source_labels: list[str],
    template_path: Path | None = None,
    root_dir: Path | None = None,
) -> ResumeArtifact:
    output_dir = (root_dir or ROOT_DIR) / "output" / "doc" / "resumes"
    output_dir.mkdir(parents=True, exist_ok=True)
    version = next_generated_resume_version(output_dir, company=company, role_title=role_title)
    artifact_slug = build_resume_artifact_slug(company=company, role_title=role_title)
    artifact_path = output_dir / f"{artifact_slug}__{version}.md"
    docx_path = output_dir / f"{artifact_slug}__{version}.docx"
    artifact_path.write_text(
        render_resume_markdown(
            draft,
            version=version,
            company=company,
            source_labels=source_labels,
        ),
        encoding="utf-8",
    )

    rendered_docx_path: Path | None = None
    docx_error: str | None = None
    if template_path:
        if template_path.exists():
            render_resume_docx(
                draft=draft,
                version=version,
                company=company,
                source_labels=source_labels,
                template_path=template_path,
                output_path=docx_path,
            )
            rendered_docx_path = docx_path
        else:
            docx_error = f"Resume DOCX template does not exist: {template_path}"

    return ResumeArtifact(
        company=company,
        role_title=role_title,
        version=version,
        output_path=str(artifact_path),
        format="markdown+docx" if rendered_docx_path else "markdown",
        docx_path=str(rendered_docx_path) if rendered_docx_path else None,
        google_doc_error=docx_error,
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
    template_path = resolve_resume_template_path(candidate_profile)
    artifact = write_resume_artifact(
        draft=draft,
        company=job.get("company"),
        role_title=job.get("role_title"),
        source_labels=[document["label"] for document in reference_documents],
        template_path=template_path,
        root_dir=root_dir,
    )
    google_doc_name = " - ".join(
        str(part)
        for part in (
            job.get("company"),
            job.get("role_title"),
            f"Resume {artifact.version}",
        )
        if part
    )
    artifact = publish_resume_google_doc(
        artifact=artifact,
        candidate_profile=candidate_profile,
        google_doc_name=google_doc_name,
    )
    return {
        "implemented": True,
        "artifact": artifact.model_dump(),
    }
