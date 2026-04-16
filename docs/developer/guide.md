# Developer Guide

Current behavior version: `1.0.0`

Add behavior changes in code, then let the documentation service capture the new manifest and render guides.

## Change Rules

- Workflow, tool, prompt, schema, decision, QA, and strategy changes are tracked through manifest diffs.
- Major versions are reserved for workflow removals or output contract changes.
- Minor versions cover new or changed behavior.
- Patch versions cover documentation-only refreshes.

## Working Surface

- Prompt files: 
- `coordinator.txt`
- `cover_letter_writer.txt`
- `gmail_monitor.txt`
- `job_search.txt`
- `resume_writer.txt`
- `tracker.txt`
- Schemas live under `schemas/`:
- `candidate_profile.example.json`
- `candidate_profile.json`
- `normalized_job.json`
- `tracker_row.json`
- `WorkflowOutput` is a public interface. Changes such as `resume_artifacts` and `cover_letter_artifacts` should be treated as contract changes.
- Application-material drafting behavior is split between `job_agent/resume.py`, writer agents, Drive publishing in `job_agent/tools/drive.py`, and tracker sync in the orchestrator.
- The explain path is available through `python app.py --explain "<question>"`.
