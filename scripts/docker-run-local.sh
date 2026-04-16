#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ADC_FILE_DEFAULT="${HOME}/.config/gcloud/application_default_credentials.json"
ADC_FILE="${GOOGLE_ADC_FILE:-$ADC_FILE_DEFAULT}"
REDIS_URL_VALUE="${DOCKER_REDIS_URL:-redis://host.docker.internal:6379/0}"

if [[ ! -f "$ADC_FILE" ]]; then
  echo "ADC file not found: $ADC_FILE" >&2
  echo "Set GOOGLE_ADC_FILE to a readable ADC JSON path before running this script." >&2
  exit 1
fi

exec docker run --rm \
  --env-file "$ROOT_DIR/.env" \
  -e GOOGLE_SERVICE_ACCOUNT_FILE=/var/secrets/google/application_default_credentials.json \
  -e REDIS_URL="$REDIS_URL_VALUE" \
  -v "$ADC_FILE:/var/secrets/google/application_default_credentials.json:ro" \
  jobsearch-agent:local \
  "$@"
