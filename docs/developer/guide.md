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
- `gmail_monitor.txt`
- `job_search.txt`
- `tracker.txt`
- Schemas live under `schemas/`.
- The explain path is available through `python app.py --explain "<question>"`.
