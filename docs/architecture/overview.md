# Architecture Overview

Current behavior version: `1.0.0`

The system finds jobs, scores them, drafts versioned tailored resumes and cover letters for tracked jobs, publishes formatted resume and cover letter Google Docs when Drive is configured, scans Gmail for updates, syncs the tracker, and reflects on outcomes to adjust strategy.

## Workflows

- `availability`: Recheck tracked job posting links and availability for rows due for verification.
- `backfill-cover-letters`: Generate cover letters for existing tracker rows and write cover letter versions back to the tracker.
- `backfill-materials`: Generate tailored resumes and cover letters for existing tracker rows and write versions back to the tracker.
- `backfill-resumes`: Generate tailored resumes for existing tracker rows and write resume versions back to the tracker.
- `daily`: Run today's workflow: search jobs, update the tracker, scan Gmail, and summarize changes.
- `gmail`: Scan Gmail for job-related updates, sync the tracker, and summarize changes.
- `jobs`: Search for new matching jobs, update the tracker, and summarize changes.
- `reflect`: Review recent outcomes, update strategy weights, and summarize the changes.

## Agent Graph

- `CoordinatorAgent` hands off to JobSearchAgent, TrackerAgent, GmailMonitorAgent and uses tools: none.
- `CoverLetterWriterAgent` hands off to no specialists and uses tools: none.
- `GmailMonitorAgent` hands off to no specialists and uses tools: search_gmail_job_updates, classify_job_email, match_email_to_tracker, read_tracker_sheet, upsert_tracker_row.
- `JobSearchAgent` hands off to no specialists and uses tools: search_jobs, score_job_fit.
- `ResumeWriterAgent` hands off to no specialists and uses tools: none.
- `TrackerAgent` hands off to no specialists and uses tools: read_tracker_sheet, upsert_tracker_row.

## Tool Surface

- `classify_job_email` (email_body, email_from, email_subject): A tool that wraps a function. In most cases, you should use  the `function_tool` helpers to
create a FunctionTool, as they let you easily wrap a Python function.
- `match_email_to_tracker` (classified_email, tracker_rows): A tool that wraps a function. In most cases, you should use  the `function_tool` helpers to
create a FunctionTool, as they let you easily wrap a Python function.
- `read_tracker_sheet` (sheet_url): A tool that wraps a function. In most cases, you should use  the `function_tool` helpers to
create a FunctionTool, as they let you easily wrap a Python function.
- `score_job_fit` (candidate_profile, job): A tool that wraps a function. In most cases, you should use  the `function_tool` helpers to
create a FunctionTool, as they let you easily wrap a Python function.
- `search_gmail_job_updates` (max_results, queries): A tool that wraps a function. In most cases, you should use  the `function_tool` helpers to
create a FunctionTool, as they let you easily wrap a Python function.
- `search_jobs` (keywords, location_mode, origin, radius_miles, salary_floor, sources): A tool that wraps a function. In most cases, you should use  the `function_tool` helpers to
create a FunctionTool, as they let you easily wrap a Python function.
- `upsert_tracker_row` (duplicate_key, match_strategy, row, sheet_url): A tool that wraps a function. In most cases, you should use  the `function_tool` helpers to
create a FunctionTool, as they let you easily wrap a Python function.

## Schemas

- `candidate_profile.example.json`
- `candidate_profile.json`
- `normalized_job.json`
- `tracker_row.json`
