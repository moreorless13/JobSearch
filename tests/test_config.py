from __future__ import annotations

import json

from job_agent import config


def test_load_candidate_profile_defaults_resume_reference_documents(monkeypatch, tmp_path) -> None:
    profile_path = tmp_path / "candidate_profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "candidate_name": "James",
                "location_rules": {"allow_remote": True, "radius_miles": 25, "origin": "Cedar Knolls, NJ"},
                "salary_floor": 65000,
                "target_roles": ["Solutions Engineer"],
                "target_industries": ["FinTech"],
                "keywords": ["API"],
                "sheet_url": "https://example.com/sheet",
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(config, "SCHEMAS_DIR", tmp_path)

    loaded = config.load_candidate_profile()

    assert loaded["resume_reference_documents"] == []


def test_load_candidate_profile_versions_resume_reference_labels(monkeypatch, tmp_path) -> None:
    profile_path = tmp_path / "candidate_profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "candidate_name": "James",
                "location_rules": {"allow_remote": True, "radius_miles": 25, "origin": "Cedar Knolls, NJ"},
                "salary_floor": 65000,
                "target_roles": ["Solutions Engineer"],
                "target_industries": ["FinTech"],
                "keywords": ["API"],
                "sheet_url": "https://example.com/sheet",
                "resume_reference_documents": [
                    {
                        "label": "Solutions Engineer Resume",
                        "path": "/tmp/solutions.docx",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(config, "SCHEMAS_DIR", tmp_path)

    loaded = config.load_candidate_profile()

    assert loaded["resume_reference_documents"][0]["version"] == "v1.0"
    assert loaded["resume_reference_documents"][0]["label"] == "Solutions Engineer Resume (v1.0)"
