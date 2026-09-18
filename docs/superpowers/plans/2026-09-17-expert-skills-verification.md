# Expert skills and review scopes — acceptance record

Date: 2026-09-17. Repository: `fitness-bot`. Implementation uses the existing dirty checkout;
a pre-task source/test/docs copy is retained at `/tmp/fitness-bot-before-expert-skills`.
No commits, migrations, production database mutations or real model calls were performed.

## Delivered behavior

- Five intents retained; consultation/search remain basic flows. Two independently maintained,
  versioned, packaged expert skills are actually inserted into model system messages.
- Review interprets recorded facts; recommendation consumes server facts directly for exercise
  alternatives, qualitative previews, complete plans and revisions. No extra review-model call is required.
- Text, catalog cards, structured review, preview and versioned session plan contracts preserved.
- Single reviews select latest or an exact owned, completed snapshot; explicit unavailable targets
  return 404 rather than falling back. Recorded exercise/set order, type-correct dose changes,
  catalog muscle overlap, duration and entry gaps are supplied as facts. Rest remains unknown.
- Multi reviews retain full-window totals and add sub-day spacing, same-primary-body-part recurrence,
  bounded evidence and explicitly confirmed cycle context. Cadence never establishes a cycle.
- Explicit user selection and timezone inheritance survive classification. Server facts overwrite
  forged client context and are persisted with the report. No workout mutation path was added.
- Reports flag unknown, ambiguous and truncated data. Playground evidence summaries support new metrics.

## Verification evidence

Commands ran through Python 3.12 / uv with `UV_CACHE_DIR=/tmp/graphify-uv-cache`.
`--no-sync` used the repository's existing complete virtual environment.

| Check | Result |
| --- | --- |
| Baseline `uv run --no-sync pytest -q` under sandbox | 180 passed; 2 existing wire tests blocked solely by local socket permissions |
| Final `uv run --no-sync pytest -q` with local-socket permission | **205 passed**, 13.00 seconds |
| `node --test tests/web/*.test.mjs` | **32 passed** |
| Final `node --test --test-reporter=dot tests/web/*.test.mjs` | exit 0, 32 tests |
| `uv run --no-sync ruff check .` | All checks passed |
| `uv run --no-sync ruff format --check .` | 92 files formatted |
| `uv build --offline --wheel --out-dir /tmp/fitness-bot-expert-skills-wheel` | Built `fitness_bot-0.1.0-py3-none-any.whl` |
| Isolated Python import with wheel first on `sys.path` | Both SKILL.md documents read by `importlib.resources` directly from wheel |
| Independent review and final targeted verification | All reported findings fixed; 12 review-scope tests passed |

The build initially lacked hatchling in the offline cache. A normal network-enabled build fetched
build dependencies; the final wheel rebuilt offline. The wire tests use injected models and local
HTTP ports, not external model endpoints.

## New regression coverage

`tests/test_expert_skills.py` adds 11 tests covering actual prompt composition, role/output constraints,
method separation, allowlisted package access, classifier guidance, and lightweight chat/search.
`tests/test_review_scopes.py` adds 12 tests covering:

- scope defaults and paired-ID validation;
- record/set ordering, numeric changes, duration and unavailable actual rest;
- latest/exact selection, cutoff, ownership, deleted/active/future exclusions;
- subsecond ordering and same-day intervals;
- confirmed cycle and baseline overlap;
- server-owned chat context, evidence references and report persistence;
- explicit selection precedence and saved timezone at a local date boundary;
- duration sets with legacy irrelevant numeric fields;
- duplicate sequence ambiguity, bounded details and visible truncation.

The web suite adds one test for sequence/timing/cadence/cycle and unknown-metric summaries.
Missing-feature tests were observed failing before implementation. Review findings were reproduced
with regression cases before their fixes (including subsecond crash, wrong dose type, timezone
selection and hidden truncation).

## Limits

Acceptance uses synthetic snapshots, SQLite-backed test storage and injected/captured model adapters.
It verifies orchestration, facts, validation, output contracts, persistence/replay regressions and
packaging. It does **not** establish live-model intent accuracy, coaching quality, clinical efficacy,
production MySQL compatibility beyond the existing test contract, or browser visual QA.
The final suite emits one pre-existing Starlette/AnyIO deprecation warning.
Actual per-set timing and rest are absent from the upload schema; these remain unknown.

## User acceptance correction — recent sessions, not recent days

User feedback on 2026-09-17: the generic request “帮我分析下历史健身记录” was incorrectly
framed as seven calendar days versus a preceding 21-day baseline, producing an empty-recent-window
report despite available historical workouts. That behavior passed previous technical tests but did
not satisfy the intended business meaning of reviewing recent history.

Corrected the server selection default to the latest **7 completed workouts across all history**.
Added bounded `workout_count` and explicit `selection_mode` (`recent_workouts` / `date_window`).
Fewer available sessions are all used with an honest selected-count label. Explicit date requests
retain date-window behavior. Count-selected samples have no baseline evidence or invented weekly
rate. Prompts and both skill methods distinguish sample counts from calendar windows. General
limitations remain in structured facts and an expandable Playground panel instead of repeated
paragraphs in the answer body. No existing persisted answer was rewritten.

Six new API/chat regression cases were observed failing before implementation, then passed. Added
captured-classifier coverage and count-based web evidence labels. An existing plan-model test
fixture had hard-coded baseline evidence; it now chooses only server-provided evidence IDs, just
as the real model is instructed to do. Validation still rejects unavailable evidence.

Final verification after correction:

- `uv run --no-sync pytest -q`: **212 passed**, 18.87 seconds; same one deprecation warning.
- `node --test --test-reporter=spec tests/web/*.test.mjs`: **33 passed**.
- `uv run --no-sync ruff check .`: passed.
- `uv run --no-sync ruff format --check .`: **94 files** formatted.
- Local web service restarted on port 8000 for user retesting. Automated checks do not call real models.

## User acceptance correction — invalid evidence and structured streaming

Observed runtime failure for request `52cebae0-0850-4e9e-94b0-8edfb561d59f`:
`ValueError` at `training_decision.validate_review`, specifically the unknown-evidence guard.
The previous model retry could repeat an unavailable reference and abort the whole report.
No private prompt, provider output or credentials were logged or exported during diagnosis.

- Review output schemas now enumerate the current evidence IDs. Independent validation drops
  invalid findings rather than substituting made-up references. `omitted_findings` and a visible
  note explain omissions; valid findings and server facts remain available.
- The model adapter streams structured tool arguments. Only closed, validated findings are
  released; partial trailing strings/objects are held. The report starts with server facts,
  then appends validated findings and the final synthesis, without duplicate sections.
- Plan/preview generation streams only closed, dose-free theme/rationale under an explicit
  provisional label. Executable prescriptions remain behind full validation and DB commit.
  The existing authoritative `done` replaces provisional prose in the browser.
- Failed/truncated streams preserve visible partial text. Disconnect immediately closes nested
  provider generators and persists cancellation status; the next turn can proceed.

Verification after final code changes:

- `uv run --no-sync pytest -q`: **224 passed**, 16.66 seconds; existing one deprecation warning.
- `node --test --test-reporter=dot tests/web/*.test.mjs`: **33 passed**.
- Ruff lint passed; format check passed for **96 files**.
- Offline wheel build passed.
- Added 9 adapter/recovery tests, including actual LangChain tool-argument chunk parsing, invalid
  references through HTTP and persisted reports, dose filtering, truncated responses and immediate
  provider closure. Added 3 real local TCP tests: review finding before model completion, provisional
  plan prose before model completion followed by persisted result, and review disconnect cleanup.
- Failure and early-output tests were observed red before fixes. Automated tests use model doubles;
  no real model requests were made. Service restarted on port 8000 for user retesting.


## User acceptance correction — narrative and visual evidence

- Removed the repeated audit-form output from the runtime review schema and rendered prose.
  Findings support optional content-specific headings and natural Markdown; an untitled opening
  assessment is followed by relevant topics. Legacy input fields remain compatible.
- Source-bound inline fact references let the model cite accurate numbers inside its explanation.
  The server validates citation ownership and resolves slots before streaming/persistence. Tests
  cover unknown/uncited slots, title/tail leakage, streamed/final prefix consistency, and per-finding
  recovery. A stale shared prompt claiming the default was seven days was corrected to seven workouts.
- Server-derived charts show actual-date cadence, completed sets per session and primary body-part
  distribution, including unknown categories. Detail is bounded with truncation notices; no mixed-unit
  tonnage is calculated. Chart counts reconcile with selected records, including the latest-seven scope.
- Playground renders safe DOM/SVG charts, exact-data tables, local-date axes and point readouts.
  Analysis evidence is deduplicated and collapsed, as are general data limitations.

Final verification:

- `uv run --no-sync pytest -q`: **233 passed**, 16.11 seconds; one existing deprecation warning.
- `node --test tests/web/*.test.mjs`: **41 passed** (8 chart tests).
- Ruff lint passed; format check passed for **99 files**.
- Actual browser QA used an isolated SQLite service and injected model, with seven synthetic workouts.
  Verified streaming selection summary, natural Markdown, all three charts, exact data-table counts,
  click readout, and persisted history replay. Inspected desktop and 390px mobile layouts; no horizontal
  overflow in the report/charts. Temporary viewport override reset afterward.
- Browser fixture confirms rendering and data plumbing; automated checks do not call the real LLM or
  certify its prose quality. The normal port 8000 service is restarted for user acceptance with real data.
