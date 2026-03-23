# JobSearch

Job-search workflow built on the OpenAI Agents SDK with live OpenAI-powered search and Google Sheets tracker sync.

## Why the package layout looks slightly different from the draft

The OpenAI Agents SDK is imported as `agents`. A top-level local `agents/` directory would shadow the installed SDK package and break imports. This starter keeps app code under `job_agent/agents/` so `from agents import Agent` still resolves to the SDK.

## What is included

- `app.py`: runnable entrypoint for the coordinator workflow
- `job_agent/agents/`: coordinator and specialist agent builders
- `job_agent/tools/`: job search, Sheets integration, Gmail stubs, and helper logic
- `schemas/`: candidate profile and JSON schemas
- `prompts/`: prompt files for each agent
- `tests/`: unit tests for local logic that does not require external services
- `.env.example`: environment template
- `requirements.txt`: baseline dependencies

## Current status

Implemented today:

- Job search is live through OpenAI web search
- Google Sheets works with service-account credentials
- Preset workflows `daily`, `jobs`, and `gmail` return structured JSON
- Free-form coordinator runs support `assistant_response` and follow-up questions
- Free-form runs automatically answer coordinator follow-up questions with `yes` until the coordinator proceeds or a safety limit is reached
- Search retries once when the first filtered pass returns no jobs
- Tracker sync skips weak `ignore`-band matches instead of writing obvious low-fit rows
- Local helper logic for dedupe, location filtering, fit scoring, and email classification is implemented and tested

Still incomplete:

- Gmail is still stubbed
- No auto-apply behavior exists
- No outbound email behavior exists
- Search quality depends on model/web-search variability, so results can differ between runs
- Job-search notes still land in `needs_review` in preset workflows

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Required `.env` values:

- `OPENAI_API_KEY`
- `JOB_TRACKER_SHEET_URL`
- one of `GOOGLE_SERVICE_ACCOUNT_FILE` or `GOOGLE_SERVICE_ACCOUNT_JSON`

Optional `.env` values:

- `OPENAI_MODEL` defaults to `gpt-4.1-mini`
- `GMAIL_SEARCH_MAX_RESULTS` defaults to `25`

For Sheets access, share the tracker spreadsheet with the service account email as an editor.

The candidate profile is loaded from `schemas/candidate_profile.json`. `JOB_TRACKER_SHEET_URL` from `.env` overrides the sheet URL in that file at runtime.

## Run

```bash
python app.py
```

Examples:

```bash
python app.py --workflow jobs
python app.py --workflow daily
python app.py --workflow gmail
python app.py --input "Search for new matching jobs and update my tracker."
```

Free-form `--input` runs use the coordinator agent. If the coordinator asks a follow-up question, the app automatically replies `yes` and continues until no follow-up questions remain or the loop limit is hit.

All runs print a JSON payload with this top-level shape:

```json
{
  "summary": {},
  "new_jobs": [],
  "gmail_updates": [],
  "tracker_updates": [],
  "needs_review": [],
  "follow_up_questions": [],
  "assistant_response": null
}
```

`jobs` and `daily` workflows will write to Google Sheets when qualifying jobs are found.

## Test

```bash
venv/bin/python -m pytest -q
```

## Known behavior

- A run that writes nothing to Sheets usually means job search returned zero qualifying jobs after local filtering, not that Sheets is broken.
- The web-search layer is nondeterministic, so repeated searches can return different jobs.
- Tracker rows are matched primarily by `duplicate_key`, then by posting URL and company/title/location.
- Existing tracker notes are preserved and new notes are appended.

## Next steps

1. Replace Gmail stubs with real Gmail API integration.
2. Improve search consistency and note handling in preset workflows.
3. Add trace/export hooks and run persistence.
