from datetime import date

from job_agent.tools.jobs import (
    calculate_fit_score,
    derive_candidate_experience_years,
    location_matches,
    parse_required_experience_years,
    salary_meets_floor,
)


PROFILE = {
    "location_rules": {
        "allow_remote": True,
        "radius_miles": 25,
        "origin": "Cedar Knolls, NJ",
    },
    "salary_floor": 65000,
    "target_roles": ["Solutions Engineer"],
    "target_industries": ["FinTech"],
    "keywords": ["API", "integrations", "Postman"],
    "work_history": [
        {
            "company": "Acme",
            "title": "Solutions Engineer",
            "start_date": "2020-01-01",
            "end_date": "2022-01-01",
            "counts_toward_relevant_experience": True,
        },
        {
            "company": "Beta",
            "title": "Integration Engineer",
            "start_date": "2021-07-01",
            "end_date": "2023-01-01",
            "counts_toward_relevant_experience": True,
        },
    ],
}


def test_location_matches_remote_role() -> None:
    job = {"location": "Remote - US", "remote_or_local": "remote"}
    assert location_matches(job, PROFILE) is True


def test_salary_floor_rejects_listed_low_salary() -> None:
    job = {"salary_min": 60000}
    assert salary_meets_floor(job, PROFILE["salary_floor"]) is False


def test_calculate_fit_score_rewards_alignment() -> None:
    job = {
        "company": "Acme",
        "role_title": "Senior Solutions Engineer",
        "location": "Remote",
        "remote_or_local": "remote",
        "industry": "FinTech",
        "description": "Need 3+ years of experience owning API integrations, customer implementation work, and Postman collections.",
        "salary_min": 90000,
    }

    result = calculate_fit_score(job, PROFILE)

    assert result["fit_score"] >= 70
    assert result["fit_band"] in {"good", "strong", "excellent"}
    assert result["required_experience_years"] == 3.0
    assert result["candidate_experience_years"] == 3.0
    assert result["experience_gap_years"] == 0.0


def test_derive_candidate_experience_years_merges_overlapping_relevant_roles() -> None:
    years = derive_candidate_experience_years(PROFILE, current_date=date(2026, 1, 1))
    assert years == 3.0


def test_derive_candidate_experience_years_falls_back_to_total_tenure() -> None:
    profile = {
        "work_history": [
            {
                "company": "Acme",
                "title": "Support",
                "start_date": "2020-01-01",
                "end_date": "2021-01-01",
                "counts_toward_relevant_experience": False,
            },
            {
                "company": "Beta",
                "title": "Customer Success",
                "start_date": "2021-01-01",
                "end_date": "2023-01-01",
                "counts_toward_relevant_experience": False,
            },
        ]
    }

    years = derive_candidate_experience_years(profile, current_date=date(2026, 1, 1))
    assert years == 3.0


def test_derive_candidate_experience_years_uses_current_date_for_active_role() -> None:
    profile = {
        "work_history": [
            {
                "company": "Acme",
                "title": "Solutions Engineer",
                "start_date": "2025-01-01",
                "end_date": None,
                "counts_toward_relevant_experience": True,
            }
        ]
    }

    years = derive_candidate_experience_years(profile, current_date=date(2026, 1, 1))
    assert years == 1.0


def test_parse_required_experience_years_handles_common_formats() -> None:
    assert parse_required_experience_years("Need 5+ years of experience in SaaS.") == 5.0
    assert parse_required_experience_years("Requires 3-5 years of experience with APIs.") == 3.0
    assert parse_required_experience_years("Requires 3 to 5 years of experience with APIs.") == 3.0
    assert parse_required_experience_years("Minimum of 4 years of experience required.") == 4.0
    assert parse_required_experience_years("No explicit tenure requirement listed.") is None


def test_calculate_fit_score_penalizes_material_experience_shortfall() -> None:
    profile = {
        **PROFILE,
        "work_history": [
            {
                "company": "Acme",
                "title": "Solutions Engineer",
                "start_date": "2023-01-01",
                "end_date": "2025-01-01",
                "counts_toward_relevant_experience": True,
            }
        ],
    }
    job = {
        "company": "Acme",
        "role_title": "Senior Solutions Engineer",
        "location": "Remote",
        "remote_or_local": "remote",
        "industry": "FinTech",
        "description": "Need 5+ years of experience owning API integrations and Postman collections.",
        "salary_min": 90000,
    }

    result = calculate_fit_score(job, profile)

    assert result["fit_score"] == 59
    assert "experience is materially below the requirement" in result["reason"]
