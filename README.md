# JobSearchAgent

Job-search workflow built on the OpenAI Agents SDK with live OpenAI-powered search, Redis-backed orchestration state, Gmail monitoring, and Google Sheets tracker sync.

## Why the package layout looks slightly different from the draft

The OpenAI Agents SDK is imported as `agents`. A top-level local `agents/` directory would shadow the installed SDK package and break imports. This starter keeps app code under `job_agent/agents/` so `from agents import Agent` still resolves to the SDK.

## What is included

- `app.py`: runnable entrypoint for the coordinator workflow
- `job_agent/agents/`: coordinator and specialist agent builders
- `job_agent/tools/`: job search, Sheets integration, Gmail integration, and helper logic
- `schemas/`: candidate profile and JSON schemas
- `prompts/`: prompt files for each agent
- `tests/`: unit tests for local logic that does not require external services
- `.env.example`: environment template
- `requirements.txt`: baseline dependencies

## Current status

Implemented today:

- Job search is live through OpenAI web search
- Google Sheets works with Application Default Credentials or service-account credentials and supports Workspace domain-wide delegation
- Gmail search is implemented through the Gmail API with Workspace domain-wide delegation, using ADC or service-account credentials
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
- Gmail requires Google Workspace domain-wide delegation before it can read mail

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
- Google credentials through one of:
  - `GOOGLE_APPLICATION_CREDENTIALS`
  - `GOOGLE_SERVICE_ACCOUNT_FILE`
  - `GOOGLE_SERVICE_ACCOUNT_JSON`

Optional `.env` values:

- `OPENAI_MODEL` defaults to `gpt-4.1-mini`
- `REDIS_URL` enables Redis-backed orchestration state. Example: `redis://localhost:6379/0`
- `GMAIL_SEARCH_MAX_RESULTS` defaults to `25`
- `GOOGLE_SERVICE_ACCOUNT_EMAIL` for keyless delegated auth when the service account email cannot be detected from ADC automatically
- `GOOGLE_DELEGATED_USER` for Workspace domain-wide delegation across Gmail and Sheets
- `GMAIL_DELEGATED_USER` remains supported as a Gmail-specific fallback

`GOOGLE_APPLICATION_CREDENTIALS` and `GOOGLE_SERVICE_ACCOUNT_FILE` should point to the same kind of file: a Google service-account JSON key. If you already use the standard Google auth env var, you do not need to duplicate it with `GOOGLE_SERVICE_ACCOUNT_FILE`.

If no key file or inline JSON is provided, the app now falls back to **Application Default Credentials**. That is the preferred Cloud Run path.

For Gmail access, this repo requires Google Workspace domain-wide delegation and does not use local OAuth token flows:

- use Application Default Credentials or `GOOGLE_APPLICATION_CREDENTIALS`, `GOOGLE_SERVICE_ACCOUNT_FILE`, or `GOOGLE_SERVICE_ACCOUNT_JSON`
- set `GOOGLE_DELEGATED_USER` or `GMAIL_DELEGATED_USER`
- set `GOOGLE_SERVICE_ACCOUNT_EMAIL` if ADC cannot infer the service account email for delegated impersonation

For Sheets access, either:

- use domain-wide delegation with `GOOGLE_DELEGATED_USER` or `GMAIL_DELEGATED_USER`, or
- share the tracker spreadsheet with the service account email as an editor

Delegation is the preferred production path for Cloud Run Jobs.

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

Free-form `--input` runs use the coordinator agent. Today, if the coordinator asks a follow-up question, the app automatically replies `yes` and continues until no follow-up questions remain or the loop limit is hit. That behavior is intentional in the current CLI flow, but it is still blunt enough to deserve supervision instead of blind trust.

## Deploy

This repository is currently a CLI workload, so Google Cloud deployment should target **Cloud Run Jobs** rather than a request-serving Cloud Run service.

Recommended deployment order:

1. Enable the required Google Cloud APIs: Cloud Run, Cloud Build, Artifact Registry, Secret Manager, Gmail API, Sheets API, and Redis API if you want Memorystore.
2. Confirm Google Workspace domain-wide delegation is authorized for the service account client ID with at least `https://www.googleapis.com/auth/gmail.readonly` and `https://www.googleapis.com/auth/spreadsheets`.
3. Create an Artifact Registry Docker repository.
4. Create a Secret Manager secret for `OPENAI_API_KEY`. A service-account JSON key is optional now and only needed if you are not using ADC.
5. Build and push the container image with `gcloud builds submit`.
6. Provision Memorystore for Redis if you want Redis-backed orchestration state. Otherwise omit `REDIS_URL` and the app will run in degraded stateless mode.
7. Attach the correct service account to the Cloud Run Job and grant it `Service Account Token Creator` on the delegated target account if required for your impersonation setup.
8. Create a Cloud Run Job that sets `JOB_TRACKER_SHEET_URL`, `GOOGLE_DELEGATED_USER`, and any optional `REDIS_URL`. Set `GOOGLE_SERVICE_ACCOUNT_EMAIL` if the runtime cannot infer it automatically.
9. Execute the job and inspect the first execution logs before scheduling it.

Typical production env for the Cloud Run Job:

```env
JOB_TRACKER_SHEET_URL=https://docs.google.com/spreadsheets/d/YOUR_SHEET_ID/edit
GOOGLE_DELEGATED_USER=user@yourdomain.com
GOOGLE_SERVICE_ACCOUNT_EMAIL=your-cloud-run-sa@PROJECT_ID.iam.gserviceaccount.com
OPENAI_MODEL=gpt-4.1-mini
REDIS_URL=redis://REDIS_HOST:6379/0
```

If you still need a key-based fallback, `GOOGLE_APPLICATION_CREDENTIALS=/secrets/google/service-account.json` or `GOOGLE_SERVICE_ACCOUNT_FILE=/secrets/google/service-account.json` are still supported.

See [docs/deploy/cloud-run.md](/Users/macbook/Desktop/Agents/JobSearch/docs/deploy/cloud-run.md) for the container, secret, Redis, and Google Workspace domain-wide delegation setup.

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

## Known behavior

- A run that writes nothing to Sheets usually means job search returned zero qualifying jobs after local filtering, not that Sheets is broken.
- The web-search layer is nondeterministic, so repeated searches can return different jobs.
- Tracker rows are matched primarily by `duplicate_key`, then by posting URL and company/title/location.
- Existing tracker notes are preserved and new notes are appended.
- When Redis is configured, the orchestrator stores decisions, outcomes, reflection summaries, and follow-up tasks there. Google Sheets remains the human-readable mirror, not the strategy source of truth.
- Cloud Run production deployments should prefer ADC plus `GOOGLE_DELEGATED_USER`, not mounted long-lived keys.
- `GOOGLE_SERVICE_ACCOUNT_EMAIL` exists for the annoying cases where ADC knows who it is but refuses to say it out loud.

## Next steps

1. Refit the free-form coordinator to call the shared orchestrator core instead of bypassing it.
2. Add semantic memory and embeddings on top of the structured Redis history.
3. Introduce supervised resume-tailoring and application-prep flows without removing current guardrails.
