# Changelog

Current behavior version: `1.0.0`

## 2026-04-16

- Added DOCX and Google Drive publishing for cover letters, with cover-letter template support for formatting and writing-style reference.

## 2026-04-16

- Added one-off tracker backfill workflows for generating resumes, cover letters, or both for existing tracker rows.

## 2026-04-16

- Changed tracked job intake to generate both a tailored resume and cover letter, storing `resume_version` and `cover_letter_version` on tracker rows.

## 2026-04-16

- Updated the documentation generator to preserve existing changelog entries and append new changes at the end.

## 2026-04-16

- Added formatted DOCX resume generation from a Word template and Google Docs publishing into a configured Drive folder.

## 2026-04-16

- Added Drive publishing fallback from delegated user credentials to direct service-account upload for service-account-shared resume folders.
