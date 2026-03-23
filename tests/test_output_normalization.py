from job_agent.models import WorkflowOutput, normalize_workflow_output


def test_normalize_workflow_output_accepts_dict() -> None:
    result = normalize_workflow_output(
        {
            "summary": {
                "jobs_reviewed": 3,
                "jobs_added": 1,
                "duplicates_skipped": 1,
                "gmail_updates_processed": 0,
                "tracker_rows_updated": 1,
            },
            "new_jobs": [],
            "gmail_updates": [],
            "tracker_updates": [],
            "needs_review": [],
            "follow_up_questions": [],
            "assistant_response": "Added 1 job to the tracker.",
        }
    )

    assert isinstance(result, WorkflowOutput)
    assert result.summary.jobs_added == 1
    assert result.assistant_response == "Added 1 job to the tracker."


def test_normalize_workflow_output_accepts_json_string() -> None:
    result = normalize_workflow_output(
        '{"summary":{"jobs_reviewed":0,"jobs_added":0,"duplicates_skipped":0,"gmail_updates_processed":0,"tracker_rows_updated":0},"new_jobs":[],"gmail_updates":[],"tracker_updates":[],"needs_review":[],"follow_up_questions":[],"assistant_response":"No changes this run."}'
    )

    assert isinstance(result, WorkflowOutput)
    assert result.needs_review == []
    assert result.assistant_response == "No changes this run."


def test_normalize_workflow_output_wraps_plain_text() -> None:
    result = normalize_workflow_output("search_jobs is stubbed")

    assert isinstance(result, WorkflowOutput)
    assert result.summary.jobs_added == 0
    assert result.assistant_response == "search_jobs is stubbed"
    assert len(result.needs_review) == 1
    assert result.needs_review[0].kind == "unstructured_output"


def test_normalize_workflow_output_accepts_follow_up_questions() -> None:
    result = normalize_workflow_output(
        {
            "summary": {
                "jobs_reviewed": 0,
                "jobs_added": 0,
                "duplicates_skipped": 0,
                "gmail_updates_processed": 0,
                "tracker_rows_updated": 0,
            },
            "new_jobs": [],
            "gmail_updates": [],
            "tracker_updates": [],
            "needs_review": [],
            "follow_up_questions": [
                {
                    "question": "Should I prioritize remote roles over local roles this run?",
                    "context": "The request asked for a narrowed search but did not specify a preferred location mode.",
                    "required": True,
                }
            ],
        }
    )

    assert len(result.follow_up_questions) == 1
    assert result.follow_up_questions[0].required is True


def test_normalize_workflow_output_preserves_text_follow_up_question() -> None:
    result = normalize_workflow_output("Which role titles should I prioritize first?")

    assert result.assistant_response == "Which role titles should I prioritize first?"
    assert len(result.follow_up_questions) == 1
    assert result.follow_up_questions[0].question == "Which role titles should I prioritize first?"
    assert result.needs_review == []


def test_normalize_workflow_output_preserves_text_data_entry_response() -> None:
    result = normalize_workflow_output("Updated the tracker status to Interviewing and added a follow-up note.")

    assert result.assistant_response == "Updated the tracker status to Interviewing and added a follow-up note."
    assert result.follow_up_questions == []
    assert result.needs_review == []


def test_normalize_workflow_output_infers_follow_up_from_assistant_response() -> None:
    result = normalize_workflow_output(
        {
            "summary": {
                "jobs_reviewed": 0,
                "jobs_added": 0,
                "duplicates_skipped": 0,
                "gmail_updates_processed": 0,
                "tracker_rows_updated": 0,
            },
            "new_jobs": [],
            "gmail_updates": [],
            "tracker_updates": [],
            "needs_review": [],
            "follow_up_questions": [],
            "assistant_response": "What roles should I prioritize first?",
        }
    )

    assert len(result.follow_up_questions) == 1
    assert result.follow_up_questions[0].question == "What roles should I prioritize first?"
