# Ordered report composition verification

## Delivered scope

- Review requests now use retrieve → compose → respond, with a frozen portable outline.
- Adjacent text intents merge; the server assigns block, chart and dataset IDs.
- Chart specifications and bounded inline JSON datasets are separate from Markdown blocks.
- Ordered block events stream validated sections, preserve partial reports on interruption,
  and persist the same references for history replay. Legacy consumers retain text events.
- The web renders interleaved text and charts, with safe fallbacks and cache revalidation.
- Other intents retain their existing generation paths. Flutter rendering is documented,
  but no Flutter client implementation was changed.

## Automated validation

- Full Python suite: 258 passed (one existing Starlette deprecation warning).
- Web Node suite: 51 passed.
- Ruff checks passed; 109 files already formatted. JavaScript syntax checks passed.
- Actual TCP tests verify that a chart arrives before generation finishes and that cancellation
  closes the provider stream while preserving emitted blocks and chart resources.
- Provider tests cover complete tool calls, nonzero tool indices, malformed/truncated JSON,
  missing completion markers, length termination and multiple calls.
- Independent specification and quality reviews completed; material findings were corrected.

## Browser and runtime checks

- Isolated SQLite database and injected model: desktop and 390 × 844 viewport.
- Verified text/chart interleaving, three chart types, incremental delivery, history replay,
  and mobile layout without horizontal overflow. Temporary viewport override reset.
- Verified the versioned entry URL resolves mixed cached module versions.
- Restarted the actual local application on port 8000 with the updated code.
- /health returned 200, chat_configured=true and demo_mode=false.
- Playground HTML, versioned app.js and content-blocks.mjs returned 200 with Cache-Control: no-cache.
- Temporary QA server and browser tab were closed.

No real model provider was called during automated or browser acceptance tests. These checks
validate composition, delivery and rendering; they do not establish real-model prose quality.

## Integration references

- Protocol: docs/api.md, Ordered report composition v1.
- Synthetic portable payload: tests/fixtures/report_composition_v1.json.

## Follow-up: all analysis omitted in a completed report

The reported persisted response had layout_fallback=true, one frequency-only text block, no
artifacts, and omitted_findings=1. The original raw provider output was not retained, so its
specific outline-validation failure cannot be reconstructed. A manual run against the configured
provider with repository-only synthetic facts reproduced whole-block rejection of literal numeric
quantities, including Chinese quantities such as 两次, and revealed that generic repair feedback
was insufficient.

Changes:
- Retry invalid composition once; record bounded reason codes without raw content.
- If still invalid, compile a server-selected outline with available charts and relevant evidence.
- Repair each invalid prose block at most once, keeping its frozen ID and evidence scope.
- Explain Chinese quantity restrictions and full-phrase fact slots explicitly in generation/repair.
- Never emit done when all analysis is rejected; persist partial facts and chart resources.
- Display partially omitted historical/live responses accurately, with a retry control.

Verification:
- Full offline Python suite: 264 passed; targeted final rerun: 29 passed.
- Web suite: 52 passed; Ruff and JavaScript syntax checks passed.
- Final manual real-provider run used only synthetic repository data: six blocks, three charts,
  three valid findings, two repaired findings, zero omitted findings.
- Restarted localhost:8000. Browser confirmed the screenshot's historical response now displays
  分析未完成 and 再次发送此问题. Existing saved response content was not rewritten.
- Automatic approval rejected a proposed real-user-record provider replay because explicit
  authorization for that additional transfer was missing. It was not executed. All subsequent
  provider diagnostics used synthetic data and did not read the user database.

## Follow-up: explicit negative statements rejected as affirmative claims

A subsequent real request retained charts but omitted analysis. Runtime diagnostics recorded
unbound_numbers followed by invalid_report_contract during repair; raw output was not retained,
so the exact repair failure cannot be inferred from those codes alone.

Local reproduction established a separate concrete validator bug: explicit denials such as
“已完成组不等于有效组” and “不能据此认定恢复训练” were rejected by keyword presence. The validator now
exempts only narrow explicit negative constructions, preserving the original text, and continues
to reject affirmative claims elsewhere in the same response. Added passing tests for these
statements and mixed positive/negative cases. Expanded diagnostic codes for repair-ID mismatch,
missing structured output, and unsupported training-status claims.

Validation: 266 offline Python tests passed; Ruff lint/format passed. Real-record replay was not
executed: automatic approval required explicit confirmation of both the record/conversation scope
and the configured Azure hostname. The confirmation question remains pending. This patch is not
claimed to establish that all omissions in the latest real request are resolved.

## Authorized real-record replay and final correction

The user explicitly confirmed the record/conversation scope and the configured Azure destination;
manual replay proceeded. Subsequent diagnostics used only the selected review facts and related
conversation (excluding the broader workout-history summary). No workout/history rows were changed.

Real-provider diagnostics identified:
- Repair could replace one rejected approximate quantity with another instead of removing it.
- A correct selected-sample count was rejected solely because it was not a fact-slot expression.
- Crucially, provided catalog fact keys such as `recent.part.upper legs` contained spaces, while
  FACT_PATTERN disallowed spaces. Even correctly copied server-provided keys were rejected.

Corrections:
- Repair sees the exact failing sentences and only cited evidence/slots, without unrelated history.
  It returns content only; block identity stays server-owned.
- Exact selected-sample references can be bound to cited server count slots. Incorrect counts,
  unrelated frequency/load claims and uncited scope still fail. Complete cadence has a count slot.
- Fact keys are parsed without assuming catalog codes are space-free. Exact key lookup and evidence
  checks remain mandatory; invented keys and uncited facts remain rejected.

Final validation:
- Frozen-outline real replay: four valid findings, three charts, zero omissions, one repaired block.
- Fresh composition → generation → validation real replay: three valid findings, three charts,
  six ordered blocks, zero omissions and zero repairs.
- Full offline Python suite: 276 passed. Ruff lint/format passed.
- Local port 8000 restarted with corrected code. Prior saved replies were not rewritten.

## Short report resource IDs and portable inline references

- Server allocates type prefixes plus eight lowercase hexadecimal characters for block, chart,
  dataset and fact IDs; detects collisions inside the report. Persisted retries/history keep IDs.
- The model copies frozen fact IDs. Unknown IDs, wrong resource types, business keys and uncited
  facts are rejected. Text is split into text/fact_ref parts; layout/done carry the fact registry.
- Streaming deltas and partial history retain inline parts alongside resolved compatibility text.
  The web renders values literally and supports old long IDs / reports without parts.
- Added synthetic Flutter/web contract fixture `tests/fixtures/report_short_ids_v1.json` and
  API documentation for report-local uniqueness and cross-report `(result_id, resource_id)` keys.
- Validation: 278 offline Python tests passed; 55 web tests passed; Ruff lint/format passed.
  Coverage includes forced allocation collisions, catalog labels containing spaces, invalid/uncited
  refs, repair retaining IDs, streaming/persisted equality, and Markdown-like literal fact labels.
- Authorized manual real replay (same selected records and configured Azure destination): four
  validated findings, three charts, three fact references, zero omissions and zero repairs.
  No source workout records or saved replies were changed by that replay.
- Restarted localhost:8000 and refreshed the existing Playground tab. Legacy history still renders.

## Imported exercises missing catalog classification

Read-only tracing established that imported exercises deliberately preserve source IDs when no
catalog equivalence was confirmed. Aggregation uses exact catalog IDs; unmatched source exercises
were labeled as an unknown body part. The presentation now says “待归类动作”, reports matched/total
coverage, and cautions against treating the matched subset as a complete distribution. Review facts
include `body_part_coverage` per window. No source records or catalog identity mappings were changed.
A separate classification layer remains a product choice; this change does not fill missing labels.
Validation: 279 offline Python tests, Ruff lint/format passed; a read-only real-data aggregation
confirmed the corrected coverage description. Existing persisted reports retain their original data.
