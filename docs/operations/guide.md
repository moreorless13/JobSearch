# Operations Guide

Current behavior version: `1.1.1`

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

## Documentation Refresh

- Preset workflows refresh documentation after completion.
- Docs are rewritten only when content changes.
- Explain queries read generated docs plus recent decisions, outcomes, and QA records.
