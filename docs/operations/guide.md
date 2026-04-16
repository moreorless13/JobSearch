# Operations Guide

Current behavior version: `1.0.0`

Run the preset workflows through `python app.py --workflow <daily|jobs|gmail|reflect|backfill-materials>`.

## Decision Rules

- Salary floor: `65000`
- Thresholds: `{'prioritize': 70, 'track': 50, 'queue_review': 60, 'stale_days': 21}`
- Follow-up delay: `3` business days
- Search sources: `linkedin, indeed, ziprecruiter, greenhouse, lever, workday, ashby, smartrecruiters, google_jobs, company_sites`

## QA Gates

- Approve threshold: `0.8`
- Flag threshold: `0.6`
- LLM judge enabled: `False`
- Duplicate company cooldown: `7` days

## Application Materials

- Jobs added to the tracker generate versioned resume and cover letter drafts during the `jobs` workflow.
- Existing tracker rows can be backfilled with fresh materials through `python app.py --workflow backfill-materials`.
- Use `backfill-resumes` or `backfill-cover-letters` when only one artifact type needs the one-off pass.
- Resume drafts are written under `output/doc/resumes/` and the generated `resume_version` is stored on the tracker row.
- Cover letters are written under `output/doc/cover_letters/` as Markdown and DOCX, and the generated `cover_letter_version` is stored on the tracker row.
- If a resume DOCX template is configured, the generator writes a formatted `.docx` resume using that template.
- If a cover letter DOCX template is configured, the generator uses it as both the cover letter format source and writing-style reference.
- If a Drive folder ID or URL is configured, resume and cover letter DOCX files are uploaded and converted into Google Docs in that folder.
- Drive publishing can use Workspace delegation or direct service-account upload when the target folder is shared with the service account.
- Resume reference documents configured: `True`
- Resume and cover letter generation failures are surfaced in `needs_review` instead of silently skipping the issue.

## Documentation Refresh

- Preset workflows refresh documentation after completion.
- Docs are rewritten only when content changes.
- Explain queries read generated docs plus recent decisions, outcomes, and QA records.
