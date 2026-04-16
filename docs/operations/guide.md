# Operations Guide

Current behavior version: `1.0.0`

Run the preset workflows through `python app.py --workflow <daily|jobs|gmail|reflect>`.

## Decision Rules

- Salary floor: `65000`
- Thresholds: `{'prioritize': 85, 'track': 70, 'queue_review': 60, 'stale_days': 21}`
- Follow-up delay: `3` business days
- Search sources: `linkedin, indeed, ziprecruiter, greenhouse, lever, workday, ashby, smartrecruiters, google_jobs, company_sites`

## QA Gates

- Approve threshold: `0.8`
- Flag threshold: `0.6`
- LLM judge enabled: `False`
- Duplicate company cooldown: `7` days

## Resume Tailoring

- Jobs marked `tailor_resume = yes` can generate versioned resume drafts during the `jobs` workflow.
- Drafts are written under `output/doc/resumes/` and the generated `resume_version` is stored on the tracker row.
- If a DOCX template is configured, the generator writes a formatted `.docx` resume using that template.
- If a Drive folder ID or URL is configured, the `.docx` resume is uploaded and converted into a Google Doc in that folder.
- Drive publishing can use Workspace delegation or direct service-account upload when the target folder is shared with the service account.
- Resume reference documents configured: `True`
- Resume generation failures are surfaced in `needs_review` as `resume_generation_unavailable` instead of silently skipping the issue.

## Documentation Refresh

- Preset workflows refresh documentation after completion.
- Docs are rewritten only when content changes.
- Explain queries read generated docs plus recent decisions, outcomes, and QA records.
