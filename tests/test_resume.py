from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from job_agent.resume import (
    ResumeDraft,
    build_resume_reference_packets,
    generate_resume_artifact_impl,
    next_generated_resume_version,
    normalize_resume_reference_documents,
)


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
