# Single-turn Candidate Plan Implementation Plan

**Goal:** Generate a complete editable workout proposal in one conversation turn, using explicit conditions, preferences and history; one user save action promotes it to an accepted plan, without starting a workout.

**Approved behavior (2026-09-22):** The user explicitly chose historical/preference defaults, then clarified equipment may be assumed available until corrected. Do not require a multi-step questionnaire. Keep completed workouts unchanged. Do not turn inferred defaults into saved profile facts.

**Architecture:** Resolve candidate inputs in a separate backend module after explicit session inputs are extracted. Preserve explicit conversation input across turns. Attach assumption provenance to plans. Keep current-workout snapshot and new-work time requirements. Missing readiness does not block a next-workout proposal; explicit pain still blocks, fatigue reduces, and automatic progression still needs evidence. Same-exercise historical loads can be labeled unverified references; never transfer loads across exercises or manufacture weights. Frontend displays assumptions and per-set references using existing full-plan and explicit acceptance flow.

**Tech Stack:** Python/FastAPI/Pydantic/SQLite tests; Flutter/Drift widget and parser tests; OpenPencil canonical component pipeline.

- [x] Backend regression first: incomplete profile + history generates a full proposed plan; defaults have provenance; explicit equipment wins; no fabricated readiness; prior conversation overrides restored without changing persistent profile.
- [x] Implement input resolution in `src/fitness_bot/plan_inputs.py`, integrate with `agent.py` and `storage.py`, allow missing next-workout readiness in `training_decision.py`, update model/skill instructions to produce complete one-turn proposals with explained volume and all set rows.
- [x] Add `history_reference` load strategy for same-exercise observed loads, preserve unknown convention as unknown, never qualify as progression evidence; allow unchanged references through revisions but require explicit convention for changed numeric loads. Test invalid units, unrelated exercises, stale/latest sources and confirmation/revision behavior.
- [x] Frontend regression first, then display candidate assumptions, unknown historical references and clear acceptance. Retain explicit save/start separation and valid per-set editing. Keep canonical OpenPencil source and scripts consistent for visual component changes.
- [x] Run focused then full backend pytest/Ruff, Flutter analyze/tests, design checks if changed. Restart local backend, rebuild simulator with existing server/account configuration, and verify visible review/save flow with synthetic data (no automated real-model calls).

No push or unrelated cleanup. Preserve existing working-tree changes in both repositories.

## Verification

- Backend: 366 tests passed; Ruff check and format passed. Existing Starlette/AnyIO deprecation warning.
- Flutter: 510 passed, 3 skipped; analyze clean; simulator debug build succeeded.
- OpenPencil: 47 tests, 29 scenarios, 97 reusable masters, 343 resolving refs; zero lint errors.
- Independent review found and verified fixes for per-set evidence revalidation at edit/accept, explicit conversation restrictions across profile updates, and added-weight references.
- Isolated model substitutes and SQLite used for automated tests; no real model output quality claim.
- Local backend restarted and simulator updated with existing account/server configuration; historical preview messages stay immutable and new requests use the revised flow.

## Live failure follow-up

- The reported generation failure was caused by a real model choosing `history` for an unverified same-exercise observation. The server now resolves eligibility itself: maintain may use a labeled `history_reference`, reduce retains trial loading, and progression still rejects unverified evidence. Unknown or cross-exercise observation IDs remain invalid.
- Live retry exposed all-or-nothing time trimming: a six-item proposal lost its only chest movement and retained duplicate back coverage. Budget fitting now trims extra non-priority working sets down to a product allocation floor of two before removing exercises, preserves priority/progression work and warmups/rest/load, then favors retaining distinct catalog body parts. It records the allocation assumption and recalculates each summary.
- New regression tests cover model mislabeling, save eligibility, coverage-aware time fitting and heterogeneous per-set rows. Backend full suite: 373 passed; Ruff check clean. Automated tests use fake models; manual real-model verification is separate.
- Manual live verification: original failed simulator request retried successfully after eligibility fix; follow-up with final budget rules streamed `done` and a proposed plan with 15 total / 11 working sets, estimated 45–49 minutes. Simulator reopened the saved conversation and full-plan details, showing per-set reps/rest/history references and the confirm/save button. No plan was accepted or workout started.
