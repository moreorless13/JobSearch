from job_agent.tools.jobs import calculate_fit_score, location_matches, salary_meets_floor


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
        "description": "Own API integrations, customer implementation work, and Postman collections.",
        "salary_min": 90000,
    }

    result = calculate_fit_score(job, PROFILE)

    assert result["fit_score"] >= 70
    assert result["fit_band"] in {"good", "strong", "excellent"}
