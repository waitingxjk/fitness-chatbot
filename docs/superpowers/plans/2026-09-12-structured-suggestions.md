# Structured Suggestions Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development for the bounded Flutter integration task; implement backend and web sequentially in the root task. Steps use checkbox syntax for tracking.

**Goal:** Deliver structured single-exercise and ordered session suggestions with visible editable per-set cards and explicit workout acceptance/start behavior.
**Architecture:** Python normalizes and validates prescriptions against catalog and evidence. Web and Flutter render the same contract; Flutter owns local workout creation. Existing immutable plans and identity boundaries are retained.
**Tech Stack:** FastAPI/Pydantic/SQLAlchemy, vanilla JS, Flutter/Drift.

## Task 1 — backend contracts and generation
Files: src/fitness_bot/prescriptions.py (new normalization/validation), training_schemas.py, schemas.py, agent.py, model.py, training_decision.py; tests/test_prescriptions.py.
- [x] Write tests for legacy expansion, explicit mixed warmup/work sets, type/unit mismatch, accurate time, unknown load and recommendation cards.
- [x] Add shared per-set input/output; normalize aggregate fields from explicit rows and validate against catalog. Retain existing progression/evidence protections.
- [x] Generate recommendation prescriptions with the model and constrain results to retrieved active IDs; distinguish single/alternatives/session. Search remains informational.
- [x] Run targeted Python tests before integration.

## Task 2 — immutable user adjustments
Files: training_routes.py, training_storage.py, prescriptions.py; tests/test_plan_revisions.py.
- [x] Add POST /v1/training/plans/{id}/revision with revision_id, expected_revision and ordered exercise_id/set_prescriptions items.
- [x] Validate doses/loads/catalog and recompute sets/time, create immutable child, protect retries/account ownership/stale versions. Preserve originals.
- [x] Test accepted plan revision, retry, invalid input, cross-user access and stale writes.

## Task 3 — website visualization
Files: web/prescriptions.mjs (new), web/training.mjs, web/app.js, web/styles.css; tests/web/prescriptions.test.mjs.
- [x] Render shared card identity, reason, set summary, expandable per-set editor; display trial loads honestly and unit conventions.
- [x] Render session overview and ordered cards, persist adjustments as child version before whole-plan confirmation, show superseded/local-unsaved states accurately.
- [x] Render single/alternatives distinctly, expose app add only when a real bridge exists; no web write to workouts.
- [x] Test serializers/render helpers, retry handling, saved turn restoration; inspect desktop and mobile with synthetic data.

## Task 4 — Flutter receiving and execution (independent worker)
Files: training_companion service/models/controller/widgets; workout pending plan model/repository/coordinator; current training page; app/providers; focused tests.
- [x] Integrate configured server with existing user identity and NDJSON chat; parse shared set contract with legacy compatibility.
- [x] Support card editing and single add to actual active workout, idempotently preserving incomplete rows.
- [x] Fetch/accept ordered plans, persist pending plans separately; expose pending current-training UI, explicit Start atomically initializes all exercises/sets without completed records.
- [x] Keep active workout intact, queued plan available later, restart-safe idempotency. Test receiving/accept/start/duplicate and identity behavior.

## Task 5 — verification and review
- [x] Python ruff check/format + full pytest, node test suite, Flutter analyze and relevant/full tests.
- [x] Spec compliance review followed by quality review; fix actionable findings.
- [x] Restart local chatbot, verify health and open browser; document exact verified limits. Do not commit unrelated work.

Verification evidence and known limits: [verification record](2026-09-12-structured-suggestions-verification.md). Synthetic checks and real GPT smoke tests are complete after matching the deployment name and Responses interface in the user's `.env` example.
