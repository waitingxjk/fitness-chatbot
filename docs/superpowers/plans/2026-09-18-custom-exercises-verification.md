# Custom exercises verification — 2026-09-18

Final boundary: **chat produces metadata only; a separate user confirmation saves the exercise.**
No development-account exercise or workout was created by this verification.

## Automated verification

- `uv run ruff check .`: passed.
- `uv run ruff format --check .`: passed (121 files).
- `git diff --check`: passed.
- `uv run pytest -q`: **300 passed**. The existing Starlette/AnyIO deprecation warning remains.
- `node --test tests/web/*.test.mjs`: **58 passed**.
- All automated model calls use injected doubles and isolated temporary SQLite databases.
- Loopback streaming tests require local socket permission; they passed with that permission.

Coverage includes read-only chat drafts even for explicit save wording, save after confirmation,
repeat confirmations, restored history state, owner isolation, standard-name reuse, private search,
avatars, classification in review, archival, optimistic edits, rollback after inserted exercise,
clarification/follow-ups, and replacement of spoofed client draft context with server-owned metadata.

## Browser acceptance

Used temporary local server on port 8788 with injected authoring model and disposable SQLite.
Verified visible avatar, metadata-only preview, confirmation button, save success, refresh restoring
saved state, and editing of personal metadata. The temporary server and its browser tab were closed.

## Manual model smoke

Invoked the configured real planner/authoring model once with a generic wall-angel creation request,
standard catalog and empty history. No database connection was used. It returned custom_exercise /
draft with body part, muscles, bodyweight recording, instructions, inference markers and mobility
avatar metadata, with zero saved exercise cards. No personal training data was sent.

## Local service

Restarted the existing fitness-bot server on port 8000 after verification. Health is 200, standard
catalog remains 1,324 records, draft-save route is present in OpenAPI, and SVG avatar returns 200.
The new table is initialized by existing metadata startup; no workout mutation or history-ID repair
is performed. No Git commit was made because the checkout includes substantial pre-existing work.

## Name correction follow-up

- Exercise-authoring skill 1.1.0 checks likely typos, homophones and speech transcription errors
  before asking the user for a full movement description. A candidate need not be in the standard
  catalog; uncertain names are confirmed, never silently renamed or added as permanent aliases.
- Planner retains authoring intent across candidate confirmation, explicit correction and rejection.
- Added regression coverage for preserving the original input, candidate confirmation, rejection,
  and metadata-only drafts. `uv run pytest -q`: **302 passed**; Ruff and diff checks passed.
- Manual configured-model smoke with empty history/database disconnected: “抢天使” yielded
  “你是指墙天使吗”; confirmation yielded a wall-angel draft; rejection requested the actual
  movement description. No exercise was saved. Local service restarted with the updated rules.

## Draft editing, name correction and invalid references (2026-09-18)

- Reproduced the invalid `existing_exercise_id` crash in offline HTTP tests. The same
  `custom_exercise_authoring.author_response` failure site appeared in the local server log.
- Added independent structured name resolution before metadata completion. A corrected name
  must be used by the final definition. Invalid catalog references / mismatched names receive
  one bounded repair attempt, then a non-crashing clarification if unresolved.
- Manual configured-model check using only synthetic conversations and the public catalog:
  `创建一个强天使动作` and `创建一个新动作 抢天使` both asked whether the user meant 墙天使.
  `是墙天使`, with a synthetic old 强天使 draft, produced a ready 墙天使 definition without
  retaining the erroneous alias. All three classified as custom_exercise. No exercise writes.
- Draft card now supports editing all metadata before separate confirmation. PUT updates only
  chat draft metadata, with owner checks and optimistic versions. Confirmation requires the
  reviewed version; legacy bodyless confirmation is restricted to version 1. Saved drafts are
  immutable, and duplicate confirmation returns the original receipt.
- Browser acceptance on isolated port 8788 / temporary SQLite / injected model:
  edited name, aliases, body part and recording type; applied edits; refreshed and verified
  persistence; cancelled another edit; confirmed save; refreshed and opened saved details,
  which retained the edited name, classification and recording type. No real account mutated.
- Verification: Ruff check / format check passed; **310 pytest tests passed**, **60 browser
  module tests passed**. The full pytest run needs local loopback for streaming wire tests.

## Confidence-based name resolution (2026-09-18, skill 1.3.0)

- Supersedes the earlier always-confirm-typos policy: a unique high-confidence interpretation
  proceeds directly to draft completion, with a brief adopted-name notice. Multiple plausible
  movements or conflicting descriptions still ask one focused clarification.
- Name resolution records discarded erroneous names from the current input and prior draft.
  Completion removes those aliases and their “动作” suffix variants. Explicitly intentional
  custom naming is preserved. Confirmation/save remains a separate frontend action.
- Added an independent identity check when a proposed existing catalog record has a different
  name/alias, so a similar movement (e.g. upper-back stretch) cannot substitute for wall angel.
- Manual configured-model verification, synthetic input only: 强天使 and 抢天使 -> ready 墙天使
  with correction metadata; old erroneous draft + 是墙天使 -> correct ready draft; 飞鸟 -> asks
  chest fly vs reverse fly; rejection -> asks for actual movement; intentional 强天使 -> retains
  the chosen name. No personal database writes were made by these checks.
- Offline regression: 314 pytest tests passed; Ruff check and format check passed.
