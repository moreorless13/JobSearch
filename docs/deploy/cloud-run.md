# Cloud Run Deployment

This repository is currently a CLI application, not an HTTP server. That makes **Cloud Run Jobs** the correct Google Cloud target for preset workflows such as `daily`, `jobs`, `gmail`, and `reflect`.

If you want a request-driven API on Cloud Run services later, add an HTTP server layer first. In its current form, the container should run to completion and exit.

## What This Repo Needs

- `OPENAI_API_KEY`
- `JOB_TRACKER_SHEET_URL`
- `GOOGLE_DELEGATED_USER` when using Google Workspace domain-wide delegation
- `GOOGLE_SERVICE_ACCOUNT_EMAIL` when delegated ADC cannot infer the runtime service account automatically
- `REDIS_URL` if you want Redis-backed state instead of degraded stateless mode

The current code uses:

- Google Workspace domain-wide delegation in `job_agent/tools/gmail.py` and `job_agent/tools/sheets.py`
- `REDIS_URL` for orchestration state in `job_agent/state.py`

## Recommended Google Cloud Architecture

- **Cloud Run Job** for execution
- **Artifact Registry** for the container image
- **Secret Manager** for `OPENAI_API_KEY`
- **Memorystore for Redis** if you want persistent orchestration state
- **Google Sheets API** enabled in the same project as the service account
- **Gmail API** enabled and domain-wide delegation authorized in Google Workspace Admin

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
  --set-env-vars GOOGLE_DELEGATED_USER="user@your-domain.com" \
  --set-env-vars GOOGLE_SERVICE_ACCOUNT_EMAIL="your-cloud-run-sa@PROJECT_ID.iam.gserviceaccount.com" \
  --set-env-vars REDIS_URL="redis://REDIS_HOST:6379/0" \
  --set-secrets OPENAI_API_KEY=openai-api-key:latest \
  --args="--workflow","daily"
```

Notes:

- Use `--args="--workflow","jobs"` for the job-search-only workflow.
- Use `--args="--workflow","gmail"` for Gmail sync.
- Use `--args="--workflow","reflect"` for strategy reflection.
- If you do not want Redis yet, omit `REDIS_URL` and the app will run in degraded stateless mode.

## Domain-Wide Delegation Notes

For Gmail to work with Workspace domain-wide delegation:

1. Enable the Gmail API in Google Cloud.
2. Enable domain-wide delegation for the service account.
3. In Google Workspace Admin, authorize the service account client ID for `https://www.googleapis.com/auth/gmail.readonly` and `https://www.googleapis.com/auth/spreadsheets`.
4. Set `GOOGLE_DELEGATED_USER` to the mailbox you want the job to impersonate. `GMAIL_DELEGATED_USER` still works as a backward-compatible fallback.
5. If you are using keyless ADC on Cloud Run, grant the runtime principal `Service Account Token Creator` on the target service account if your impersonation setup requires it.

For Sheets, delegation now uses the same delegated user when present. If you choose not to use domain-wide delegation for Sheets, the tracker spreadsheet can still be shared with the service account email directly.

## Redis On Cloud Run

Do not use `redis://localhost:6379/0` on Cloud Run. Use a managed Redis endpoint instead.

Recommended:

- Provision **Memorystore for Redis**
- Attach the Cloud Run job to the same network path required to reach the Redis instance
- Set `REDIS_URL` to the Memorystore host and port

If you leave Redis out, the job still runs, but orchestration history and reflection state fall back to degraded stateless mode.

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
