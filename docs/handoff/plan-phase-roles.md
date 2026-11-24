# Plan phases and purpose roles

Status: implemented in the Python generator/validator and Flutter plan flow, 2026-09-22. Canonical visual source is `../flutter-init/design/openpencil/fitness_exercise_record_v0_3.op`; exported review screens live in `../flutter-init/design/openpencil/previews/plan-stages/`.

## Wire contract

New model output uses `SemanticSessionDraft` and `semantics_version: 1`. The server returns one ordered `items` list containing **every** activity, including preparation and cooldown. Saving and starting use this entire list. An item has:

- `item_id`: server-issued occurrence identity, stable through user revisions.
- `exercise`: unchanged active catalog record with its existing dose type.
- `phase`: `warmup`, `main`, or `cooldown`, in that order.
- `purpose_role`: one of the eight values below.
- `purpose`: short reason for this occurrence; quantitative instructions stay in set prescriptions.
- `role`: existing internal ordering/priority classification, separate from the user-facing purpose label.
- `set_prescriptions`: a nonempty ordered array with the original dose/load fields.

| purpose_role | Display label | Allowed phase |
| --- | --- | --- |
| general_warmup | 一般热身 | warmup |
| mobility | 活动准备 | warmup |
| technique | 技术练习 | warmup, main |
| primary | 重点训练 | main |
| accessory | 辅助训练 | main |
| conditioning | 体能训练 | main |
| cooldown | 缓和活动 | cooldown |
| stretching | 拉伸放松 | main, cooldown |

Phase labels: 热身准备、主体训练、整理放松. Roles belong to plan occurrences, not the exercise catalog. A workout need not use every role. The same catalog exercise may appear once in each phase (e.g. elliptical as warmup and cooldown); each occurrence has a distinct item ID. Current-workout additions still cannot duplicate existing workout exercises.

## Activity versus strength sets

Primary/accessory items use `kind: warmup | working`, with at least one working set. Other purposes use `kind: activity`, one row per activity bout. An activity has repetitions **or** duration according to its real catalog detail type and may have zero rest/transition time. No arbitrary catalog type substitution is allowed: many existing stretching records are repetition-based and remain so.

Activity rows count toward execution and time but not strength working-set totals. Compatibility RIR fields may remain in activity rows; clients do not display or interpret them as an activity effort target. Activity guidance is the item's qualitative `effort`. Activity rows do not automatically inherit historical strength loads. Weighted technique defaults to trial selection; user edits retain the normal load/convention validation.

## Time and completeness

`phase_summaries` contains phase, localized label, item_count, estimated_seconds, working_sets, warmup_sets and activity_count. Phase estimates sum execution, inter-row rest and transitions. Plan estimates add `buffer_minutes` exactly once. Legacy `preparation_minutes` and `finish_minutes` are derived phase subtotals for semantic plans, **not** extra time to add.

A complete next-workout proposal has all three nonempty phases. Rest outcomes contain no invented activities. Current-workout additions may contain only the needed phases; they do not force new warmup or cooldown work into an existing session.

Budget fitting protects preparation/cooldown and priority work; it first reduces extra non-priority main working sets, then removable main items, favoring distinct main-body-part coverage. Insufficient budget still fails validation rather than silently deleting required phases or compressing rest. All quantities are candidate product proposals, not claims of individual optimality.

## Revision and compatibility

Clients submit `item_id`, `exercise_id` and rows on revisions. Omitted item IDs are allowed only when that exercise unambiguously resolves to one prior occurrence. A revision cannot replace an occurrence's catalog exercise, change its role/phase, reorder across phases, or remove the last item of a required next-workout phase. Fresh IDs cannot forge new occurrences.

Legacy plans with absent/zero semantics version remain readable; they are not rewritten. When the model reduces a legacy plan, its original exercises map to main, original dose restrictions stay in force, and new concrete preparation/cooldown must fit within the original reserved minutes. Semantic parents cannot downgrade to the legacy version.

Confirmation saves a pending plan. Starting remains a separate action; no phase is auto-completed and workout history is unchanged.

## Verification

- Shared synthetic output: `examples/phase-plan.json`, mirrored as Flutter `test/fixtures/workout_plan_wire/phases.json`.
- Backend tests cover all roles, invalid phase pairs, phase order, duplicate occurrences, timing, budget protection, user revisions, legacy reduction, progression identity and HTTP generation/acceptance.
- Flutter tests cover strict/legacy parsing, full/partial-current phases, activity editing, occurrence-ID requests, narrow/large-text layouts and starting every prescribed row including duplicate cross-phase exercises.
- Automated tests inject fake models; manual live-model checks are separate.

## Invalid model output recovery

Generation performs at most one internal model correction when the initial structured draft fails
schema or plan-domain validation. The correction receives the rejected draft (in memory only),
bounded field paths/error codes, allowlisted local constraint explanations and the selected
catalog dose types. The model must return a complete replacement, which passes the same validator,
parent-plan constraints and time budget before any candidate is committed. No automatic seconds-to-
repetitions conversion, catalog mutation, acceptance or workout start is performed.

Transport errors and cancellation do not trigger semantic correction, and the existing total chat
timeout covers both attempts. Streamed qualitative text remains provisional; correction does not
stream another draft. Invalid second output fails without a saved plan. Diagnostics log schema field
paths/codes and fixed local constraint descriptions only, never rejected values, unknown field names,
provider payloads or workout history. Offline regressions live in `tests/test_plan_generation_repair.py`.

For GPT-5 deployments only, plan generation/correction defaults to `reasoning_effort=low`
when no effort was explicitly configured. Explicit values (including `none`) remain unchanged;
search/review use their existing configuration. This is scoped to plan calls, with the same total
request timeout. Synthetic manual probes found default provider responses reporting zero reasoning
tokens and repeated schema/domain violations; a low-effort fresh request produced a valid three-
phase candidate. This does not establish a guaranteed success rate or validate a real user's plan.
