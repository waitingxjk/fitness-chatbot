# Training Decision Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task, with tests before behavior changes.

**Goal:** Deliver traceable training reviews, complete next-session recommendations, and persisted plan feedback in the existing chatbot.

**Architecture:** Keep raw iOS workout snapshots read-only. Compute date-window evidence in a dedicated service; keep user settings and accepted plans separately in MySQL. Extend the LangGraph intent/response contract with review and session-plan types while preserving legacy card limits. Model interpretation cannot replace computed facts or bypass catalog, equipment, duration, or evidence validation.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, Pydantic, LangGraph, vanilla browser JavaScript, pytest with synthetic snapshots/model doubles.

Existing uncommitted identity/sync/history work is a dependency and must be preserved. Execute in the current development checkout; no commits bundling unrelated changes. Boyle book reading is deferred. E1–E5 and explicitly labelled product rules remain the evidence baseline.

## 1. Exact review evidence

Files: create `src/fitness_bot/training_review.py`, `tests/test_training_review.py`; modify `schemas.py`, `storage.py`.

- [x] Add failing API/store tests for 7-day vs preceding 21-day windows, over 30 sessions, boundary/timezone dates, mixed units, invalid values, drafts, deleted sessions, identity isolation, and replay after updates.
- [x] Query every current-user completed snapshot inside the requested UTC interval without the chat summary limit. Produce per-window counts, per-week rates, primary-part distributions, same-exercise/load-type/unit observations, IDs of every source snapshot/record/set, explicit quality caveats and omitted-comparison reasons. Never infer load convention or completion rate.
- [x] Inject these server-owned facts into saved chat context, replacing any echoed client facts and excluding them from the request hash.
- [x] Run `uv run pytest tests/test_training_review.py tests/test_workout_chat_context.py`.

## 2. Goals, availability, readiness, and confirmed cycle

Files: create `training_schemas.py`, `training_storage.py`, `training_routes.py`, `tests/test_training_profile.py`; modify `app.py`, `storage.py`.

- [x] Test empty profile, explicit updates, identity isolation, validation and stale-version rejection.
- [x] Persist goals, priority exercise IDs, experience, weekly availability, equipment and time cap; readiness is dated self-report with scale/timing, pain, fatigue and sleep. Preserve confirmed cycle separately from suggested adjustment.
- [x] Add GET/PUT profile API and load the profile into authoritative turn context. Unknown stays unknown; chat asks for the most consequential missing condition.

## 3. Structured review and full session planning

Files: modify `schemas.py`, `model.py`, `agent.py`; create `training_decision.py`, `tests/test_training_decision.py`.

- [x] Test review and session_plan intents alongside old search/recommendation caps.
- [x] Add structured model outputs: review findings distinguish fact/inference/suggestion, reference server facts, state comparison limitations, retention/adjustment/question. Full plan has theme, proposed cycle adjustment, ordered real catalog items, role, dose, effort, load basis, rests, preparation and switching budget, substitution conditions and feedback prompts.
- [x] Validate all cited evidence and active catalog IDs, equipment, dosage/detail type, priority order and upper time estimate; no numeric load copied across equipment or unknown recording conventions. Explain ordering as priority protection; no invented precise fatigue edges. Recompute after constraints change. Missing prerequisite or pain yields a focused clarification instead of a fabricated plan.
- [x] Render stable server facts and validated plan details without asking the language model to recalculate them.

## 4. Plan lifecycle and execution feedback

Files: modify `training_storage.py`, `training_routes.py`, `storage.py`; create `tests/test_training_plans.py`.

- [x] Persist generated plans atomically with the completed chat turn before done. Save immutable versions with rules/catalog/evidence/profile versions and parent plan IDs.
- [x] Test accepting, modified successor, skipping with reason, dated subjective feedback, and linking only the current user's completed iOS snapshot. Make retries idempotent and stale writes conflict. Keep workout snapshots unchanged.
- [x] Include accepted plan and actual feedback in the next decision context; do not confuse suggested/accepted/executed states or skipped plan with unwillingness to train.

## 5. Playground, documentation, and verification

Files: modify `web/index.html`, `web/app.js`, `web/style.css`, `web/stream.mjs`, `docs/api.md`, `README.md`; add browser rendering tests as needed.

- [x] Expose profile/readiness forms and review/plan quick prompts. Render range/data quality, evidence-linked findings, ordered session dose/rest/time, and lifecycle/feedback actions. Restore persisted results after reload and clear user-specific state on identity switch.
- [x] Test synthetic scenarios from the spec (sufficient/no/sync-gap history, mixed units, priority, unavailable equipment, short time, fatigue, unknown phase, cross-user access). Use no real LLM in automated tests.
- [x] Run `uv run ruff check .`, `uv run ruff format --check .`, `uv run pytest`, `node --test tests/web/*.test.mjs`; inspect the local UI with synthetic data and document remaining model-quality limits separately.
- [x] Review the complete diff against every requirement and fix findings before declaring the goal achieved.


Completion evidence and model-quality boundaries are recorded in [verification](2026-09-08-training-decision-verification.md). Local implementation only; no production deployment or professional individualized validation claimed.
