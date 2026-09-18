# Structured suggestions verification — 2026-09-12

## Delivered behavior

- Single suggestions, choose-one alternatives and ordered whole-session plans have distinct semantics and share explicit per-set prescriptions.
- Cards show catalog identity and images, reasons, a summary and editable warmup/working rows, repetitions or duration, load conventions, rest and target effort. Unknown weight remains unknown.
- Whole-plan adjustments create immutable successor versions before acceptance. Stale-parent writes, duplicate requests and cross-user access are covered. Reopening a conversation resolves to the latest version.
- Flutter receives recommendations through the configured server using the existing account identity. Single additions validate the actual local active workout. Accepted whole plans stay pending until explicit Start; starting creates ordered incomplete records atomically. Existing active training is retained.
- Each actual set has an explicit completion control. Invalid entries cannot be marked complete; untouched suggested rows remain incomplete. Unknown load units synchronize to the displayed unit when actual data is entered.
- A later user report exposed a missing-input branch: explicit session requests returned a prose discussion with no cards. That branch now produces ordered informational candidate cards plus `plan_preview`, with no sets, load, acceptance or start action. Known equipment or resolvable historical IDs bound the candidate catalog; reported pain prevents candidate generation. Old saved deferred replies display an explanation and a regeneration entry point.

## Verified

- Backend: 173 Python tests passed after Responses API compatibility fixes; Ruff checks and formatting passed. A later prompt clarification passed 29 focused model and training-decision tests. One dependency deprecation warning was reported.
- Web: 28 JavaScript tests passed. Synthetic browser checks covered single cards, per-set editing, saved plan revisions, confirmation, history restoration, desktop and mobile. No browser console errors were observed.
- Flutter: final full suite passed 391 tests with 2 existing integration skips; static analysis reported no issues. Both repositories passed whitespace checks.
- Local service health, card module and catalog thumbnail returned successfully after restart. The real service remains at `http://127.0.0.1:8000/playground/`.
- Separate synthetic preview at `http://127.0.0.1:18082/playground/` uses a temporary database and a deterministic model. Its visible banner labels the data as simulated. Desktop and mobile screenshots were visually inspected.
- Backend/web and Flutter spec and quality findings were fixed and re-reviewed. A separate focused Flutter run passed all 5 tests for server-generated contract compatibility and planned-set completion through persisted history and outgoing sync payloads.
- Real GPT verification now passes: the `.env` example uses deployment `gpt-5.4-mini-1` and `client.responses.create`. The earlier calls incorrectly used `gpt-5.4-mini` and Chat Completions. Configuration now selects Responses explicitly and preserves the deployment suffix; streamed text blocks are rendered correctly. A temporary database with synthetic conditions passed ordinary chat, a single exercise card and a complete plan with 3 exercises, 10 working sets and an estimated upper duration of 48 minutes. The plan prompt explicitly leaves the progression exercise unset for maintain/reduce/rest; the existing validator remains intact.
- After the preview fix: 178 Python tests and 31 web tests passed; 2 Flutter contract tests verified that candidates remain informational. Real GPT returned six ordered candidate cards for the same request wording with incomplete synthetic conditions. Desktop/mobile browser inspection confirmed card visibility, absent set editors and no console errors. Personal conversation data was read locally to diagnose the missing branch, but was not replayed to the model.
- Subsequent user-authorized diagnosis replayed the failed request three times against the configured GPT without changing saved turns. Two replays failed preview prose validation; the third returned six selections, five outside the four allowed candidates. Preview output now enumerates allowed IDs, substitutes qualitative text for invalid dose prose, and excludes invalid/duplicate IDs while retaining valid cards in order. Local regression coverage verifies both recovery paths and preserves rejection of unexpected prescription fields. Final verification: 182 Python tests and 31 web tests passed; Ruff and whitespace checks passed. The final ID-filtering change was not replayed against GPT because the three-replay authorization was exhausted. The local service was restarted with the final changes.

## Limits

- The earlier `Insufficient quota available for instant inference` response came from a call that did not match the user's GPT deployment example. It did not establish that the configured deployment had exhausted quota. The earlier synthetic real-model smoke tests passed; these few samples do not establish broad recommendation quality. Private workout history was used only in the later three explicitly authorized diagnostic replays described above.
- Flutter buffers the NDJSON response until its final result. Pending/start state and duplicate protection are local to the app; this work does not automatically submit plan execution events back to the server.
- The website visualizes and confirms plans. Single-card edits there are page-local; adding an exercise to an active workout happens in the app.
- No device/simulator manual pass, deployment or source-control commit is claimed. Existing unrelated work is preserved. OpenPencil source files were not changed.
