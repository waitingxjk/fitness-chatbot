# Training decision verification

Date: 2026-09-08. Local implementation of the reviewed v1 scope is complete. Boyle book reading
remains deferred. This verifies software behavior and a small synthetic quality sample, not validated
individual prescription or a production rollout.

## Requirement audit

| Requirement | Evidence |
| --- | --- |
| Complete user/date-window facts, references, quality and sync caveats | `test_training_review.py`: >30 sessions, timezone boundaries, units, invalid/deleted/draft/unfinished data, identity, authoritative context and replay |
| Explicit goals, equipment/time, dated readiness and separate confirmed cycle | `test_training_profile.py`, `test_training_decision.py`; mobile goal form exercised in offline browser |
| Structured review with fact/inference/action and trustworthy counts | Review renderer supplies numbers and observation detail; guards reject numeric/effective-set claims in model prose; synthetic review sample inspected |
| Whole-session plan, real catalog, order, time and alternatives | `test_training_decision.py`: four-item plans, priority/equipment/dose/time checks, trimming, historical load gating, incompatible optional alternatives removed |
| Traceable load conditions and specific effort before progression | `test_training_evidence.py`: immutable per-user confirmations, revocation, snapshot replacement, units, exact evidence pairs, invalid newest/second-newest records, in-flight change rejection |
| Single-variable, exercise-specific adjustment and cycle/readiness constraints | Explicit progression signal and protected item; sets/reps checked against source dose; high fatigue and confirmed deload/recovery block increases |
| Immutable plan lifecycle and actual feedback | `test_training_plans.py`, `test_training_plan_review.py`: proposal/accept/skip/modify/execute/feedback, ownership, retry, stale versions, retained execution evidence |
| Preserve workout ownership and existing card protocol | Complete test suite includes upload/storage/chat identity and legacy search/recommendation contract tests; no code writes workouts from training decisions |
| Playground forms, lifecycle, persistence, identity reset | Offline browser: goal/readiness, full plan, adopt, skip reason, reload, mobile form; new confirmation save/reopen retained checkboxes and precise source timestamp, no console errors |
| Model quality separated from deterministic tests | Manual public-catalog/synthetic-only samples: normal, fatigue, short equipment/time, reduction of prior plan, sparse-history review |

## Final validation

- Ruff check and format check pass.
- Full Python suite: 150 passed (SQLite/injected models; local socket tests included).
- Browser JavaScript suite: 22 passed.
- Source/wheel package build verified.
- Four independent code-review passes across the implementation. All actionable findings fixed,
  including invalid second-newest evidence, revocation during generation, precise timestamp defaults,
  and added/assistance load conventions. Full plan text is sent only after successful commit.
- Real local MySQL service is restarted at port 8000; health and new read-only APIs are smoke-tested.

## Manual model samples

[Saved synthetic samples](../../knowledge/training/evaluations/2026-09-08-synthetic-samples.json)
contain inputs, drafts and validation outcomes. No real training or conversation history was sent.
The earlier normal sample produced a valid priority bench/row plan after time trimming. New samples:
fatigue → reduce, five working sets, upper estimate 32 minutes; short dumbbell session → four working
sets within 19 minutes; modification of an accepted ten-set plan → four working sets within 19 minutes.

Initial drafts exposed exact-time text being rejected and one incompatible optional substitute.
The validator now allows only the user's exact time cap in the title, and removes invalid optional
alternatives without weakening checks on the primary work. Final samples pass local validation.
The sparse review initially called completed sets effective sets and inferred return to training from
missing data. Prompt/validation now keep numeric facts in program-rendered sections and reject those
known errors; one bounded regeneration is available. The revised sample was inspected separately.

## Explicit limits

User confirmation is self-report, not independent verification of technique, device sync completeness,
or load measurement. P09's two observations, 14-day freshness, RIR range and one-step adjustments are
product rules, not established individual physiological thresholds. An execution link is not proof
that every prescribed set/repetition was achieved. Exact grip/back/technical fatigue edges remain
unknown without reviewed metadata, as specified by P06. Model language can still be mistaken; these
few samples are not a statistical quality study. No professional individual review is claimed.
