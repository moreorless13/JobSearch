# Cloud Run Deployment

This repository is currently a CLI application, not an HTTP server. That makes **Cloud Run Jobs** the correct Google Cloud target for preset workflows such as `daily`, `jobs`, `availability`, `gmail`, `reflect`, and the one-off material backfill workflows.

If you want a request-driven API on Cloud Run services later, add an HTTP server layer first. In its current form, the container should run to completion and exit.

## What This Repo Needs

- `OPENAI_API_KEY`
- `JOB_TRACKER_SHEET_URL`
- `GOOGLE_DELEGATED_USER` when using Google Workspace domain-wide delegation
- `GOOGLE_SERVICE_ACCOUNT_EMAIL` when delegated ADC cannot infer the runtime service account automatically
- `REDIS_URL`

For the recommended Cloud Run setup, you do **not** need:

- `GOOGLE_APPLICATION_CREDENTIALS`
- `GOOGLE_SERVICE_ACCOUNT_FILE`
- `GOOGLE_SERVICE_ACCOUNT_JSON`

The current code uses:

- Google Workspace domain-wide delegation in `job_agent/tools/gmail.py`, `job_agent/tools/sheets.py`, and `job_agent/tools/drive.py`
- `REDIS_URL` for orchestration state in `job_agent/state.py`

## Recommended Google Cloud Architecture

- **Cloud Run Job** for execution
- **Artifact Registry** for the container image
- **Secret Manager** for `OPENAI_API_KEY`
- **Memorystore for Redis** for persistent orchestration state
- **Google Sheets API** enabled in the same project as the service account
- **Gmail API** enabled and domain-wide delegation authorized in Google Workspace Admin
- **Google Drive API** enabled when publishing generated resumes as Google Docs

## Build The Container

```bash
gcloud builds submit --tag us-central1-docker.pkg.dev/PROJECT_ID/job-search/jobsearch-agent:latest
```

Replace `PROJECT_ID` and region as needed. Create the Artifact Registry repository first if it does not exist.

## Create Secrets

Recommended Secret Manager secrets:

- `openai-api-key`

You no longer need a long-lived Google service-account JSON key on Cloud Run if the job uses Application Default Credentials. Keep a JSON key only as a fallback for local or legacy deployments.

## Create The Cloud Run Job

Example:

```bash
gcloud run jobs create jobsearch-daily \
  --image us-central1-docker.pkg.dev/PROJECT_ID/job-search/jobsearch-agent:latest \
  --region us-central1 \
  --task-timeout 30m \
  --max-retries 1 \
  --service-account your-cloud-run-sa@PROJECT_ID.iam.gserviceaccount.com \
  --set-env-vars OPENAI_MODEL=gpt-4.1-mini \
  --set-env-vars JOB_TRACKER_SHEET_URL="https://docs.google.com/spreadsheets/d/SPREADSHEET_ID/edit" \
  --set-env-vars RESUME_GOOGLE_DRIVE_FOLDER_ID="DRIVE_FOLDER_ID" \
  --set-env-vars RESUME_GOOGLE_DRIVE_USE_DELEGATION="false" \
  --set-env-vars GOOGLE_DELEGATED_USER="user@your-domain.com" \
  --set-env-vars GOOGLE_SERVICE_ACCOUNT_EMAIL="your-cloud-run-sa@PROJECT_ID.iam.gserviceaccount.com" \
  --set-env-vars REDIS_URL="redis://REDIS_HOST:6379/0" \
  --set-secrets OPENAI_API_KEY=openai-api-key:latest \
  --args="--workflow","daily"
```

Notes:

- Use `--args="--workflow","jobs"` for the job-search-only workflow.
- Use `--args="--workflow","availability"` for the tracker link and availability recheck workflow. The `daily` workflow also runs this pass and only rechecks rows whose last availability check is at least 3 days old.
- Use `--args="--workflow","gmail"` for Gmail sync.
- Use `--args="--workflow","reflect"` for strategy reflection.
- Use `--args="--workflow","backfill-materials"` for a one-off tracker pass that generates resumes and cover letters for existing rows.
- Redis is checked during CLI startup. If `REDIS_URL` is missing or unreachable, the job exits before running the workflow.
- Do not set `GOOGLE_APPLICATION_CREDENTIALS` on Cloud Run for the recommended keyless path.
- Tailored resume drafts under `output/doc/resumes/` and cover letters under `output/doc/cover_letters/` live on the job's ephemeral filesystem unless Drive publishing is configured.

## Domain-Wide Delegation Notes

For Google Workspace APIs to work with domain-wide delegation:

1. Enable the Gmail, Sheets, and Drive APIs in Google Cloud as needed by the workflows you run.
2. Enable domain-wide delegation for the service account.
3. In Google Workspace Admin, authorize the service account client ID for `https://www.googleapis.com/auth/gmail.readonly`, `https://www.googleapis.com/auth/spreadsheets`, and `https://www.googleapis.com/auth/drive.file`.
4. Set `GOOGLE_DELEGATED_USER` to the mailbox you want the job to impersonate. `GMAIL_DELEGATED_USER` still works as a backward-compatible fallback.
5. If you are using keyless ADC on Cloud Run, grant the runtime principal `Service Account Token Creator` on the target service account if your impersonation setup requires it.

For Sheets, delegation now uses the same delegated user when present. If you choose not to use domain-wide delegation for Sheets, the tracker spreadsheet can still be shared with the service account email directly.

For Drive publishing, delegation uses the same delegated user when present. If you choose not to use delegation for Drive, set `RESUME_GOOGLE_DRIVE_USE_DELEGATION=false` and share the destination folder with the service account email directly. The uploader will also fall back to direct service-account upload when delegated Drive upload fails.

## Redis On Cloud Run

Do not use `redis://localhost:6379/0` on Cloud Run. Use a managed Redis endpoint instead.

Recommended:

- Provision **Memorystore for Redis**
- Attach the Cloud Run job to the same network path required to reach the Redis instance
- Set `REDIS_URL` to the Memorystore host and port

If Redis is missing or unreachable, the job exits during startup before workflow execution.

## Execute The Job

```bash
gcloud run jobs execute jobsearch-daily --region us-central1
```

## Update An Existing Job

```bash
gcloud run jobs update jobsearch-daily \
  --image us-central1-docker.pkg.dev/PROJECT_ID/job-search/jobsearch-agent:latest \
  --region us-central1
```

## Logs

```bash
gcloud run jobs executions list --job jobsearch-daily --region us-central1
gcloud run jobs executions describe EXECUTION_NAME --region us-central1
```

## Current Limitations

- There is no HTTP server entrypoint yet, so deploy as a Cloud Run Job, not a service.
- Gmail depends on Google Workspace domain-wide delegation.
- Sheets can use the same delegated Workspace user as Gmail, but direct spreadsheet sharing with the service account still works as a fallback.
- Redis requires a reachable managed endpoint; local Homebrew Redis is only for local development.
- Keyless ADC is now the preferred Cloud Run auth path; mounted service-account keys are only a fallback.
- Generated resume and cover letter artifacts are local files unless `RESUME_GOOGLE_DRIVE_FOLDER_ID` or `RESUME_GOOGLE_DRIVE_FOLDER_URL` is configured for Drive publishing.
