# JobSearchAgent

Job-search workflow built on the OpenAI Agents SDK with live OpenAI-powered search, Redis-backed orchestration state, Gmail monitoring, and Google Sheets tracker sync.

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
- Gmail search is implemented with Gmail API auth support
- Redis-backed goal state, plan runs, decisions, outcomes, follow-up tasks, and reflection snapshots are supported when `REDIS_URL` is configured
- Preset workflows `daily`, `jobs`, `gmail`, and `reflect` return structured JSON
- Preset workflows now run through a shared deterministic orchestrator with supervisor-mode guardrails
- Job intake uses deterministic decision scoring for `prioritize`, `track`, `queue_review`, and `skip`
- Daily and reflect runs adjust role/source/industry strategy weights from recent outcomes
- Follow-up review tasks are scheduled from Gmail signals and stale applied tracker rows
- Free-form coordinator runs support `assistant_response` and follow-up questions
- Free-form runs automatically answer coordinator follow-up questions with `yes` until the coordinator proceeds or a safety limit is reached
- Search retries once when the first filtered pass returns no jobs
- Tracker sync only writes jobs that clear deterministic decision thresholds
- Local helper logic for dedupe, location filtering, fit scoring, and email classification is implemented and tested

Still incomplete:

- No auto-apply behavior exists
- No outbound email behavior exists
- If Redis is unavailable, the app falls back to stateless mode and reports degraded orchestration in `needs_review`
- Search quality depends on model/web-search variability, so results can differ between runs
- Semantic memory and embeddings are not implemented yet
- Gmail still requires you to configure either Workspace delegation or a local OAuth token flow before it can read mail

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
- `REDIS_URL` enables Redis-backed orchestration state. Example: `redis://localhost:6379/0`
- `GMAIL_SEARCH_MAX_RESULTS` defaults to `25`
- `GMAIL_DELEGATED_USER` for Workspace domain-wide delegation
- `GOOGLE_OAUTH_CLIENT_SECRET_FILE` or `GOOGLE_OAUTH_CLIENT_SECRET_JSON` for standard Gmail OAuth
- `GMAIL_TOKEN_FILE` or `GMAIL_TOKEN_JSON` for a previously authorized Gmail token
- `GMAIL_OAUTH_USE_CONSOLE=true` to use a console OAuth flow instead of a local browser callback

For Sheets access, share the tracker spreadsheet with the service account email as an editor.
For Gmail access, choose one of these modes:

- Google Workspace: use `GOOGLE_SERVICE_ACCOUNT_*` plus `GMAIL_DELEGATED_USER`
- Personal Gmail or standard OAuth: use `GOOGLE_OAUTH_CLIENT_SECRET_*`, then let the app create `GMAIL_TOKEN_FILE` on first successful login

The candidate profile is loaded from `schemas/candidate_profile.json`. `JOB_TRACKER_SHEET_URL` from `.env` overrides the sheet URL in that file at runtime. The candidate profile now also supports `top_level_objective`, `company_priorities`, and `decision_thresholds`.

## Run

```bash
python app.py
```

Examples:

```bash
python app.py --workflow jobs
python app.py --workflow daily
python app.py --workflow gmail
python app.py --workflow reflect
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

`jobs` and `daily` workflows will write to Google Sheets when qualifying jobs are found. `reflect` updates Redis strategy state only and preserves the existing top-level output contract.

## Test

```bash
.venv/bin/python -m pytest -q
```

## Type Check

```bash
npx pyright job_agent/tools/gmail.py job_agent/tools/jobs.py job_agent/orchestrator.py job_agent/state.py
```

`pyrightconfig.json` points Pyright at `.venv`, so editor and CLI diagnostics use the project virtualenv.

## Known behavior

- A run that writes nothing to Sheets usually means job search returned zero qualifying jobs after local filtering, not that Sheets is broken.
- The web-search layer is nondeterministic, so repeated searches can return different jobs.
- Tracker rows are matched primarily by `duplicate_key`, then by posting URL and company/title/location.
- Existing tracker notes are preserved and new notes are appended.
- When Redis is configured, the orchestrator stores decisions, outcomes, reflection summaries, and follow-up tasks there. Google Sheets remains the human-readable mirror, not the strategy source of truth.

## Next steps

1. Refit the free-form coordinator to call the shared orchestrator core instead of bypassing it.
2. Add semantic memory and embeddings on top of the structured Redis history.
3. Introduce supervised resume-tailoring and application-prep flows without removing current guardrails.
