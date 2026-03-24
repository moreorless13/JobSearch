from job_agent.tools.gmail import classify_email_payload, match_email_to_tracker_row_payload


def test_classify_interview_request() -> None:
    result = classify_email_payload(
        email_subject="Interview availability for Solutions Engineer role",
        email_from="recruiting@acme.com",
        email_body="Please share your availability so we can schedule time this week.",
    )

    assert result["classification"] == "Interview Request"
    assert result["company"] == "Acme"
    assert result["action"] == "Respond promptly"


def test_match_email_to_tracker_row() -> None:
    classified_email = {
        "company": "Acme",
        "role_title": "Solutions Engineer",
    }
    tracker_rows = [
        {"company": "Acme", "role_title": "Solutions Engineer", "duplicate_key": "acme::solutions engineer::remote"},
        {"company": "Other", "role_title": "Sales Engineer", "duplicate_key": "other::sales engineer::remote"},
    ]

    result = match_email_to_tracker_row_payload(classified_email, tracker_rows)

    assert result["matched"] is True
    assert result["row"]["company"] == "Acme"


def test_match_email_to_tracker_row_returns_no_row_when_unmatched() -> None:
    classified_email = {
        "company": None,
        "role_title": "Completely Different Role",
    }
    tracker_rows = [
        {"company": "Acme", "role_title": "Solutions Engineer", "duplicate_key": "acme::solutions engineer::remote"},
    ]

    result = match_email_to_tracker_row_payload(classified_email, tracker_rows)

    assert result["matched"] is False
    assert result["row"] is None
