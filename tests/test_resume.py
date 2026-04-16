from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from types import SimpleNamespace

from job_agent.resume import (
    ResumeDraft,
    build_resume_reference_packets,
    generate_resume_artifact_impl,
    next_generated_resume_version,
    normalize_resume_reference_documents,
)

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def write_minimal_resume_template(path: Path) -> None:
    document_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="{W_NS}">
  <w:body>
    <w:p><w:pPr><w:pStyle w:val="Heading3"/></w:pPr><w:r><w:rPr><w:b w:val="1"/></w:rPr><w:t>Professional Summary</w:t></w:r></w:p>
    <w:p><w:pPr><w:spacing w:after="120"/></w:pPr><w:r><w:t>Summary text</w:t></w:r></w:p>
    <w:p><w:pPr><w:spacing w:after="240"/></w:pPr><w:r><w:rPr><w:b w:val="1"/></w:rPr><w:t>Experience</w:t></w:r></w:p>
    <w:p><w:pPr><w:spacing w:after="0"/></w:pPr><w:r><w:rPr><w:b w:val="1"/></w:rPr><w:t>Dash Solutions</w:t></w:r></w:p>
    <w:p><w:pPr><w:spacing w:after="0"/></w:pPr><w:r><w:rPr><w:b w:val="1"/></w:rPr><w:t>Solutions Engineer | Jan 2024 - Jun 2024</w:t></w:r></w:p>
    <w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr><w:ind w:left="720" w:hanging="360"/></w:pPr><w:r><w:t>Built implementation guides.</w:t></w:r></w:p>
    <w:sectPr/>
  </w:body>
</w:document>
"""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", document_xml)


def test_normalize_resume_reference_documents_adds_versioned_labels() -> None:
    documents = normalize_resume_reference_documents(
        [
            {
                "label": "Solutions Engineer Resume",
                "path": "/tmp/solutions.docx",
            }
        ]
    )

    assert documents[0]["version"] == "v1.0"
    assert documents[0]["label"] == "Solutions Engineer Resume (v1.0)"


def test_next_generated_resume_version_increments_minor(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "output" / "doc" / "resumes"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "acme__solutions-engineer__v1.0.md").write_text("v1.0", encoding="utf-8")
    (artifact_dir / "acme__solutions-engineer__v1.1.md").write_text("v1.1", encoding="utf-8")

    version = next_generated_resume_version(
        artifact_dir,
        company="Acme",
        role_title="Solutions Engineer",
    )

    assert version == "v1.2"


def test_build_resume_reference_packets_marks_missing_pdf_content(tmp_path: Path) -> None:
    pdf_path = tmp_path / "Profile.pdf"
    pdf_path.write_text("not a real pdf", encoding="utf-8")
    profile = {
        "resume_reference_documents": [
            {
                "label": "Profile PDF",
                "version": "v1.0",
                "path": str(pdf_path),
                "kind": "profile",
            }
        ]
    }

    packets = build_resume_reference_packets(profile)

    assert packets[0]["label"] == "Profile PDF (v1.0)"
    assert packets[0]["content_available"] is False


class FakeRunner:
    @staticmethod
    def run_sync(_agent, _input):
        return SimpleNamespace(
            final_output=ResumeDraft(
                full_name="James Strande",
                target_role="Solutions Engineer",
                headline="Solutions Engineer",
                professional_summary=[
                    "Client-facing API integration leader.",
                    "Experienced in implementation guides and QA.",
                ],
                core_skills=["API Integrations", "Postman", "JIRA"],
                experience=[],
                education=["Georgia Institute of Technology"],
            )
        )


class FakeRunnerWithExperience:
    @staticmethod
    def run_sync(_agent, _input):
        return SimpleNamespace(
            final_output=ResumeDraft(
                full_name="James Strande",
                target_role="Solutions Engineer",
                headline="Solutions Engineer",
                professional_summary=["Client-facing API integration leader."],
                core_skills=["API Integrations", "Postman"],
                experience=[
                    {
                        "company": "Acme",
                        "title": "Solutions Engineer",
                        "dates": "2024 - Present",
                        "bullets": ["Built tailored integration plans for fintech clients."],
                    }
                ],
                education=["Georgia Institute of Technology"],
            )
        )


def test_generate_resume_artifact_impl_writes_versioned_markdown(tmp_path: Path) -> None:
    reference_path = tmp_path / "reference.md"
    reference_path.write_text("# Resume Reference\nAPI integrations\n", encoding="utf-8")
    profile = {
        "candidate_name": "James Strande",
        "resume_reference_documents": [
            {
                "label": "Master Resume",
                "version": "v1.0",
                "path": str(reference_path),
                "kind": "resume",
            }
        ],
    }

    result = generate_resume_artifact_impl(
        candidate_profile=profile,
        job={"company": "Acme", "role_title": "Solutions Engineer"},
        runner_cls=FakeRunner,
        root_dir=tmp_path,
    )

    assert result["implemented"] is True
    assert result["artifact"]["version"] == "v1.0"
    assert Path(result["artifact"]["output_path"]).exists()


def test_generate_resume_artifact_impl_writes_docx_and_publishes_google_doc(monkeypatch, tmp_path: Path) -> None:
    reference_path = tmp_path / "reference.md"
    reference_path.write_text("# Resume Reference\nAPI integrations\n", encoding="utf-8")
    template_path = tmp_path / "template.docx"
    write_minimal_resume_template(template_path)
    uploads: list[dict[str, object]] = []

    def fake_upload_docx_as_google_doc_impl(**kwargs):
        uploads.append(kwargs)
        assert Path(str(kwargs["docx_path"])).exists()
        return {
            "implemented": True,
            "google_doc_id": "doc123",
            "google_doc_url": "https://docs.google.com/document/d/doc123/edit",
        }

    monkeypatch.setattr("job_agent.tools.drive.upload_docx_as_google_doc_impl", fake_upload_docx_as_google_doc_impl)
    profile = {
        "candidate_name": "James Strande",
        "resume_template_document_path": str(template_path),
        "resume_google_drive_folder_id": "folder123",
        "resume_reference_documents": [
            {
                "label": "Master Resume",
                "version": "v1.0",
                "path": str(reference_path),
                "kind": "resume",
            }
        ],
    }

    result = generate_resume_artifact_impl(
        candidate_profile=profile,
        job={"company": "Acme", "role_title": "Solutions Engineer"},
        runner_cls=FakeRunnerWithExperience,
        root_dir=tmp_path,
    )

    artifact = result["artifact"]
    assert result["implemented"] is True
    assert artifact["format"] == "markdown+docx"
    assert artifact["google_doc_url"] == "https://docs.google.com/document/d/doc123/edit"
    assert uploads[0]["folder_id"] == "folder123"
    docx_path = Path(artifact["docx_path"])
    assert docx_path.exists()

    with zipfile.ZipFile(docx_path) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    text = "\n".join(node.text or "" for node in root.findall(f".//{{{W_NS}}}t"))
    assert "Acme" in text
    assert "Built tailored integration plans for fintech clients." in text
