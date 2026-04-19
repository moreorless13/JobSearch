# JobSearchAgent

Job-search workflow built on the OpenAI Agents SDK with live OpenAI-powered search, Redis-backed orchestration state, Gmail monitoring, and Google Sheets tracker sync.

## Why the package layout looks slightly different from the draft

The OpenAI Agents SDK is imported as `agents`. A top-level local `agents/` directory would shadow the installed SDK package and break imports. This starter keeps app code under `job_agent/agents/` so `from agents import Agent` still resolves to the SDK.

## What is included

- `app.py`: runnable entrypoint for the coordinator workflow
- `job_agent/agents/`: coordinator and specialist agent builders
- `job_agent/resume.py`: versioned resume-reference normalization and tailored resume/cover-letter artifact generation
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
- Job intake verifies posting links and filters out invalid or closed roles before tracker sync
- Tracked job availability is rechecked every 3 days through the `availability` workflow and the daily workflow
- Tracker sync only writes jobs that clear deterministic decision thresholds
- Jobs added to the tracker and configured with `resume_reference_documents` generate versioned resume drafts under `output/doc/resumes/`, cover letters under `output/doc/cover_letters/`, and store both generated versions on the tracker row
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

Optional `.env` values:

- `OPENAI_MODEL` defaults to `gpt-4.1-mini`
- `REDIS_URL` enables Redis-backed orchestration state. Example: `redis://localhost:6379/0`
- `GMAIL_SEARCH_MAX_RESULTS` defaults to `25`
- `RESUME_TEMPLATE_DOCX_PATH` points to the Word resume template used when generating formatted DOCX and Google Doc resumes
- `COVER_LETTER_TEMPLATE_DOCX_PATH` points to a sample cover letter used for cover letter DOCX formatting and writing-style reference
- `RESUME_GOOGLE_DRIVE_FOLDER_ID` or `RESUME_GOOGLE_DRIVE_FOLDER_URL` publishes generated resume and cover letter Google Docs into a specific Drive folder
- `RESUME_GOOGLE_DRIVE_USE_DELEGATION=false` forces Drive publishing to use the service account directly, useful when the destination folder is shared with the service account
- `RESUME_GOOGLE_DRIVE_DELEGATED_USER` overrides `GOOGLE_DELEGATED_USER` for Drive publishing only
- `GOOGLE_SERVICE_ACCOUNT_EMAIL` for keyless delegated auth when the service account email cannot be detected from ADC automatically
- `GOOGLE_DELEGATED_USER` for Workspace domain-wide delegation across Gmail, Sheets, and Drive
- `GMAIL_DELEGATED_USER` remains supported as a Gmail-specific fallback
- `GOOGLE_APPLICATION_CREDENTIALS`, `GOOGLE_SERVICE_ACCOUNT_FILE`, or `GOOGLE_SERVICE_ACCOUNT_JSON` only if you are intentionally overriding ambient ADC behavior

If no key file or inline JSON is provided, the app now falls back to **Application Default Credentials**. That is the preferred Cloud Run path.

If you do set `GOOGLE_APPLICATION_CREDENTIALS` or `GOOGLE_SERVICE_ACCOUNT_FILE`, either may point to a Google service-account JSON key or an ADC credentials file. For delegated Gmail, Sheets, or Drive access, ADC-based files still need to resolve to a service account that can mint domain-wide delegated tokens.

For Gmail access, this repo requires Google Workspace domain-wide delegation and does not use local OAuth token flows:

- use Application Default Credentials or `GOOGLE_APPLICATION_CREDENTIALS`, `GOOGLE_SERVICE_ACCOUNT_FILE`, or `GOOGLE_SERVICE_ACCOUNT_JSON`
- set `GOOGLE_DELEGATED_USER` or `GMAIL_DELEGATED_USER`
- set `GOOGLE_SERVICE_ACCOUNT_EMAIL` if ADC cannot infer the service account email for delegated impersonation

For Sheets access, either:

- use domain-wide delegation with `GOOGLE_DELEGATED_USER` or `GMAIL_DELEGATED_USER`, or
- share the tracker spreadsheet with the service account email as an editor

For Drive publishing, share the destination folder with the service account and set `RESUME_GOOGLE_DRIVE_USE_DELEGATION=false`, or use `GOOGLE_DELEGATED_USER`/`RESUME_GOOGLE_DRIVE_DELEGATED_USER` for Workspace domain-wide delegation. If generic `GOOGLE_DELEGATED_USER` is set for Gmail, Drive will try delegated upload first and fall back to direct service-account upload.

Delegation is the preferred production path for Cloud Run Jobs.

### Local keyless testing

For **Sheets-only** local testing without a key:

```bash
gcloud auth application-default login
```

Then leave `GOOGLE_DELEGATED_USER`, `GOOGLE_APPLICATION_CREDENTIALS`, `GOOGLE_SERVICE_ACCOUNT_FILE`, and `GOOGLE_SERVICE_ACCOUNT_JSON` unset in `.env`.

For **Gmail plus Sheets** local testing without a key, you need ADC backed by service-account impersonation:

```bash
gcloud auth application-default login \
  --impersonate-service-account=YOUR_SERVICE_ACCOUNT@PROJECT_ID.iam.gserviceaccount.com
```

Then set:

```env
GOOGLE_DELEGATED_USER=your-workspace-user@yourdomain.com
GOOGLE_SERVICE_ACCOUNT_EMAIL=YOUR_SERVICE_ACCOUNT@PROJECT_ID.iam.gserviceaccount.com
```

`GOOGLE_DELEGATED_USER` must be a real Workspace mailbox, not the service account email. Without impersonation, Sheets can still work locally, but Gmail will not.

The candidate profile is loaded from `schemas/candidate_profile.json`. `JOB_TRACKER_SHEET_URL` from `.env` overrides the sheet URL in that file at runtime. The candidate profile now also supports `top_level_objective`, `company_priorities`, `decision_thresholds`, `resume_template_document_path`, `cover_letter_template_document_path`, `resume_google_drive_folder_id`, `resume_google_drive_folder_url`, and versioned `resume_reference_documents` for resume-writing flows.

Example reference entry:

```json
{
  "label": "Solutions Engineer Resume (v1.0)",
  "version": "v1.0",
  "path": "/absolute/path/to/Solutions Engineer Resume.docx",
  "kind": "resume"
}
```

## Run

```bash
python app.py
```

Examples:

```bash
python app.py --workflow jobs
python app.py --workflow daily
python app.py --workflow availability
python app.py --workflow gmail
python app.py --workflow reflect
python app.py --workflow backfill-materials
python app.py --input "Search for new matching jobs and update my tracker."
```

Use `availability` for the tracker hygiene pass that rechecks due posting links and marks rows when jobs disappear. The `daily` workflow runs this automatically, and each row becomes due again 3 days after its last availability check.

Use `backfill-materials` for the one-off tracker pass that generates a fresh tailored resume and cover letter for existing tracker rows. `backfill-resumes` and `backfill-cover-letters` run each side independently.

Free-form `--input` runs use the coordinator agent. Today, if the coordinator asks a follow-up question, the app automatically replies `yes` and continues until no follow-up questions remain or the loop limit is hit. That behavior is intentional in the current CLI flow, but it is still blunt enough to deserve supervision instead of blind trust.

## Deploy

This repository is currently a CLI workload, so Google Cloud deployment should target **Cloud Run Jobs** rather than a request-serving Cloud Run service.

Recommended deployment order:

1. Enable the required Google Cloud APIs: Cloud Run, Cloud Build, Artifact Registry, Secret Manager, Gmail API, Sheets API, and Redis API if you want Memorystore.
2. Confirm Google Workspace domain-wide delegation is authorized for the service account client ID with at least `https://www.googleapis.com/auth/gmail.readonly`, `https://www.googleapis.com/auth/spreadsheets`, and `https://www.googleapis.com/auth/drive.file`.
3. Create an Artifact Registry Docker repository.
4. Create a Secret Manager secret for `OPENAI_API_KEY`. A Google credential secret is not required for the recommended keyless ADC path.
5. Build and push the container image with `gcloud builds submit`.
6. Provision Memorystore for Redis if you want Redis-backed orchestration state. Otherwise omit `REDIS_URL` and the app will run in degraded stateless mode.
7. Attach the correct service account to the Cloud Run Job and grant it `Service Account Token Creator` on the delegated target account if required for your impersonation setup.
8. Create a Cloud Run Job that sets `JOB_TRACKER_SHEET_URL`, `GOOGLE_DELEGATED_USER`, and any optional `REDIS_URL`. Set `RESUME_GOOGLE_DRIVE_FOLDER_ID` when resume and cover letter Google Docs should be published to Drive. Set `GOOGLE_SERVICE_ACCOUNT_EMAIL` if the runtime cannot infer it automatically.
9. Execute the job and inspect the first execution logs before scheduling it.

Typical production env for the Cloud Run Job:

```env
JOB_TRACKER_SHEET_URL=https://docs.google.com/spreadsheets/d/YOUR_SHEET_ID/edit
RESUME_TEMPLATE_DOCX_PATH=/app/templates/MVB - Solutions Engineer Resume.docx
RESUME_GOOGLE_DRIVE_FOLDER_ID=YOUR_DRIVE_FOLDER_ID
RESUME_GOOGLE_DRIVE_USE_DELEGATION=false
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
  "resume_artifacts": [],
  "cover_letter_artifacts": [],
  "tracker_updates": [],
  "qa_results": [],
  "documentation_updates": [],
  "needs_review": [],
  "follow_up_questions": [],
  "assistant_response": null
}
```

`jobs` and `daily` workflows will write to Google Sheets when qualifying jobs are found. `reflect` updates Redis strategy state only and preserves the existing top-level output contract.

## Application Materials

- Resume and cover letter drafts are generated for every job found and added to the tracker.
- Generated resume drafts are versioned as `v1.0`, `v1.1`, and so on, and written under `output/doc/resumes/`.
- Generated cover letters are versioned the same way and written under `output/doc/cover_letters/` as Markdown and DOCX.
- When `resume_template_document_path` or `RESUME_TEMPLATE_DOCX_PATH` is configured, resume generation writes a formatted DOCX using that file as the Word template.
- When `cover_letter_template_document_path` or `COVER_LETTER_TEMPLATE_DOCX_PATH` is configured, cover letter generation uses that document for DOCX formatting and writing-style reference.
- When `resume_google_drive_folder_id`, `resume_google_drive_folder_url`, `RESUME_GOOGLE_DRIVE_FOLDER_ID`, or `RESUME_GOOGLE_DRIVE_FOLDER_URL` is configured, resume and cover letter DOCX files are uploaded to Drive and converted into Google Docs in that folder.
- The generated artifact versions are stored in the tracker row as `resume_version` and `cover_letter_version`.
- Reference resumes should be explicitly labeled with their version number in `resume_reference_documents`.

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
- Resume drafts generated under `output/doc/resumes/` and cover letters generated under `output/doc/cover_letters/` are local artifacts unless Drive publishing is configured.
- Cloud Run production deployments should prefer ADC plus `GOOGLE_DELEGATED_USER`, not mounted long-lived keys.
- `GOOGLE_SERVICE_ACCOUNT_EMAIL` exists for the annoying cases where ADC knows who it is but refuses to say it out loud.

## Next steps

1. Refit the free-form coordinator to call the shared orchestrator core instead of bypassing it.
2. Add semantic memory and embeddings on top of the structured Redis history.
3. Introduce supervised resume-tailoring and application-prep flows without removing current guardrails.
