# Custom Exercises Implementation Plan

**Goal:** Create personal exercises through chat using an exercise-authoring expert skill, with durable metadata and an avatar usable by web and native clients.

**Architecture:** A fixed packaged skill produces a typed draft after catalog lookup and clarification. The graph returns only metadata. Explicit frontend confirmation validates and saves the exercise together with its confirmation receipt. A request-local catalog combines standard and personal records. Existing thumbnail endpoints serve deterministic built-in SVG avatars. No workout snapshots are changed.

**Tech Stack:** Python 3.12, Pydantic, SQLAlchemy, LangGraph, FastAPI NDJSON, existing browser cards.

## Approved business rules
- Personal exercises are private to their owner, separate from the versioned standard snapshot.
- Names and recording type are required. Unknown classification remains unknown; user input and inferred fields have separate provenance.
- Names/aliases are checked before creation; ambiguous identity or recording type leads to a single focused question. Ordinary consultation/search does not create records.
- Creation intent and draft readiness are separate. A ready draft requires user intent; even explicit natural-language save requests only return metadata. Saving requires a separate frontend confirmation action.
- Every custom record has an avatar (built-in key plus authenticated thumbnail URL); model output cannot supply arbitrary URLs or markup.
- Chat persists only the draft metadata. Confirming the draft saves the exercise and confirmation receipt atomically; retries do not create duplicates.
- Stable IDs survive edits; archival preserves history. Recording type cannot be changed through metadata edits.
- Historical imported IDs are not automatically remapped by this feature.

## Execution (inline; preserve existing uncommitted work)
- [x] Add offline integration tests for creation, account isolation, replay/deduplication, clarification, standard matches, avatar access, search and review.
- [x] Add typed draft/spec and private persistence with normalization, validation, updates, archival and catalog overlay.
- [x] Add allowlisted exercise-authoring skill, planner intent and structured model boundary; integrate read-only NDJSON draft responses and a separate confirmation transaction.
- [x] Add personal exercise HTTP management and thumbnail support; verify existing cards show avatars and custom metadata.
- [x] Document API and behavior; run Ruff, pytest and browser module tests. Verify live app startup without sending real user data to a model.

Validation commands: `uv run pytest tests/test_custom_exercises.py tests/test_expert_skills.py`; `uv run ruff check .`; `uv run ruff format --check .`; `uv run pytest`; `node --test tests/web/*.test.mjs`.
