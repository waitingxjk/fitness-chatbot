# Expert Skills Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development for the bounded skill/prompt task, with root integrating review scopes and conducting acceptance.

**Goal:** Implement two runtime expert skills and evidence-grounded single/multiple workout reviews without breaking existing chatbot contracts.

**Architecture:** Keep the existing three-node graph and five intents. Package trusted method documents and compose them into task prompts; extend server-owned review facts and report rendering additively. Deterministic statistics, authentication, validation and persistence stay in Python.

**Tech Stack:** Python 3.12, uv, FastAPI, Pydantic, SQLAlchemy, LangGraph, existing browser ES modules.

## Task 1 — Runtime skills and prompt composition

- [x] Add failing captured-message tests in `tests/test_expert_skills.py` for review/recommendation loading, separation and task selection.
- [x] Add `src/fitness_bot/skills/__init__.py`, `training-review/SKILL.md` and `training-recommendation/SKILL.md`. Use fixed resource names, version metadata and deterministic composition.
- [x] Refactor `model.py` to use skill methods for review, recommendation, preview and complete plan generation, while retaining task schema constraints and public method signatures. Update classification instructions for single/multiple review selection.
- [x] Run `uv run pytest tests/test_expert_skills.py tests/test_model_config.py`; inspect wheel resources.

## Task 2 — Review scope and evidence

- [x] Add failing tests in `tests/test_review_scopes.py`: latest/explicit single selection, ordered records and sets, missing rest timestamps, per-user isolation, interval/cycle evidence and legacy default behavior.
- [x] Extend `training_schemas.ReviewOptions` with scope and paired source/workout identifiers. Reject conflicting selection parameters.
- [x] Add `review_details.py` to calculate bounded evidence and enrich `training_review.py` without changing historical snapshot data.
- [x] Extend `training_decision.evidence_index` and `render_review`; retain existing evidence identifiers and date-window statistics.
- [x] Map unavailable explicit review targets to 404 in `training_routes.py`; ensure chat-owned context and replay behavior remain intact.
- [x] Run `uv run pytest tests/test_review_scopes.py tests/test_training_review.py tests/test_training_plan_review.py`.

## Task 3 — Presentation and acceptance

- [x] Add/extend `tests/web/training.test.mjs` for scope evidence labels; update `web/training.mjs` formatting where needed.
- [x] Add integration coverage for a single-workout chat review with injected model, persistence and reference validation.
- [x] Update `README.md` and `docs/api.md` with skill boundaries, scope parameters, missing-data behavior and backward compatibility.
- [x] Run `uv run ruff check .`, `uv run ruff format --check .`, `uv run pytest`, `node --test tests/web/*.test.mjs`.
- [x] Independently review source changes against saved pre-task baseline; fix findings and repeat affected checks.
- [x] Write `docs/superpowers/plans/2026-09-17-expert-skills-verification.md` with actual commands/results and limitations.
