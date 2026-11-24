# Workout Phase and Purpose Roles Implementation Plan

**Goal:** Show and generate complete candidate plans with three ordered phases and eight user-approved occurrence-level purpose labels.

**Architecture:** Preserve the flat ordered `items` wire list so all phases are saved and started through the existing lifecycle. Add versioned `phase`, `purpose_role` and server-generated `item_id` per occurrence. Retain the legacy `role` as internal priority, distinct from user-facing purpose. Add `activity` set rows for preparation/cooldown/conditioning without misreporting them as strength working sets. All phase durations derive from item execution/rest/transition time; the overall estimate adds buffer once. Older immutable plans remain readable.

**Tech Stack:** canonical OpenPencil component pipeline, Python/Pydantic/FastAPI, Flutter widgets/Drift.

## Agreed semantic mapping

| purpose_role | Label | Allowed phase |
| --- | --- | --- |
| general_warmup | 一般热身 | warmup |
| mobility | 活动准备 | warmup |
| technique | 技术练习 | warmup, main |
| primary | 重点训练 | main |
| accessory | 辅助训练 | main |
| conditioning | 体能训练 | main |
| cooldown | 缓和活动 | cooldown |
| stretching | 拉伸放松 | main, cooldown |

Phases: warmup 热身准备 → main 主体训练 → cooldown 整理放松. Purpose is attached to this plan occurrence, never permanently to catalog exercises. Not every workout needs every role. `kind=warmup/working` denotes sets within primary/accessory training; `activity` denotes a prescribed activity bout, which counts toward duration but not strength working sets. Optional stage/role examples are product classifications, not efficacy guarantees.

## Ordered tasks

- [x] OpenPencil: update Ready overview/detail through reusable phase headers, purpose badges and phase-aware rows; retain canonical file and 29 business scenarios. Demonstrate all eight badges in component library, export before/after, inspect images, run node tests/verify/design lint. Produce design before changing runtime generation.
- [x] Backend regressions: versioned complete plans contain concrete warmup/main/cooldown items, require valid role/phase combinations, allow one catalog exercise in separate phases via unique item IDs, maintain dose type/load restrictions, activity rows have no automatic history load, all phases included once in time. Preserve legacy draft compatibility.
- [x] Backend implementation: new `plan_semantics.py` owns roles/phase validation and summaries; integrate schemas, generation prompt, budget fitting and edit validation. Model adapter requests a strict semantic draft; direct old fixtures still decode. Complete next-workout plans require all phases; current additions may contain only needed phases. Rest/no-addition has no fabricated activities.
- [x] Revision/lifecycle regressions: item IDs prevent cross-phase exercise collisions; old clients may omit item IDs only when the exercise is unambiguous; preserve phase order and complete next-workout structure after edits; evidence validates every remaining row. No automatic acceptance/start or completed-record mutation.
- [x] Flutter if included in this turn: parse version/phase/purpose/activity, group overview/detail by phase with role and purpose; edit/reorder within a phase, send item IDs on revisions, retain all items when starting, preserve legacy rendering. Add targeted parsing/widget/lifecycle tests.
- [x] Synchronize handoff protocol and fixtures, run full pytest/Ruff and applicable Flutter/design checks. Inspect actual generated wire shape using fake-model tests; real model only explicit manual verification outside automated tests. Deploy simulator only with mutually compatible backend/frontend.

No commits, push or unrelated cleanup. Keep pre-existing working-tree changes in both repositories.

## Verification outcome

- OpenPencil: 48 tests, 29 preserved scenarios, 106 masters / 367 resolving refs, zero lint errors; before/after, full groups and eight-role library exported and visually reviewed.
- Backend: 395 tests passed; Ruff check and format clean. Independent review confirmed legacy reduction, cross-phase progression identity, activity effort, phase protection and no version downgrade.
- Flutter: 518 passed, three existing skips; analyze clean; simulator debug build installed with the existing local endpoint and account. Focused tests include narrow/large-text layout and complete occurrence persistence.
- Shared synthetic wire example validates HTTP generation, revision and idempotent acceptance without starting a workout. Completed records remain untouched.
- Manual real-history/model verification was blocked before execution by automatic approval review: it requires explicit authorization to send synchronized training history to the configured Azure model endpoint. The user was asked with the exact receiving host and may skip this optional live check. No blocked request was retried or bypassed.

## Generation failure follow-up

The next simulator run failed at the model/schema boundary; the original diagnostic did not retain
field paths. Separate manual probes used only a fictitious profile, empty history and no database
access. They reproduced invalid rests/transitions, repetition/duration mismatches, lost leading-zero
IDs and incomplete phases. Added bounded same-request correction with exact safe constraint feedback,
in-memory rejected drafts, focused correction catalogs and unchanged final validation. GPT-5 plan
calls now default to low reasoning only when the operator did not explicitly configure effort.

Final verification: 415 backend tests passed; Ruff check/format and git diff whitespace checks pass.
Independent review passed, including malformed-ID correction scope, privacy, cancellation and scoped
reasoning overrides. A real-model synthetic failed-ID candidate corrected successfully into eight
items/three phases. A fresh synthetic generation using final defaults succeeded with ten items and
all three phases, including dose/time validation. The original real user's history was not replayed.
Local backend was restarted with these changes; no candidate accepted and no workout started.
