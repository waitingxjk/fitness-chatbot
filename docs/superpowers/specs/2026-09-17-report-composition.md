# Ordered report composition

Approved in conversation: add a composition layer before final content generation. It emits an ordered
outline, references to required charts, portable chart specifications and data. Generate text against
that outline, keeping chart references separate. Merge adjacent text sections into one display block.

Scope: backend and Playground report delivery; publish a Flutter-consumable contract. Existing workout,
recommendation and plan behavior remains compatible. Flutter rendering itself is a separate integration.
No dependency on HTML, executable JavaScript, or external chart URLs in the report contract.

Review graph: understand → retrieve → compose → respond. Composition uses the review skill and current
server-owned facts to produce text intents/cited evidence and chart keys. A deterministic compiler
validates the outline, allocates UUID-based report/block/artifact/dataset IDs, resolves chart data from
facts, merges adjacent text sections, and freezes the plan before generating prose. Only supported
chart keys with available data can be selected. Invalid outlines fall back to a bounded text-only plan.

Final ChatResponse adds schema_version=1, outline, blocks, artifacts, datasets. Existing text/review/cards
remain compatible. Blocks are markdown {id,type,content} or chart_ref {id,type,artifact_id}. Outline has
ordered ids/types and purposes/evidence on text items. Registry entries contain format=chart-spec/v1,
chart_type timeline/columns/bars, title/description/unit, dataset_id, field mappings and truncation.
Datasets are inline JSON rows for this bounded release. IDs and snapshots persist with the response.
Do not add a public filesystem path or unauthenticated resource endpoint.

NDJSON additions: report_layout {schema_version,outline,artifacts,datasets}; block_start {block},
block_delta {block_id,delta}; block_end {block_id}; block {block:chart_ref}. Compatibility text_delta
contains the same ordered Markdown without chart markers. All generated text must cite the compiler's
allowed evidence and resolve exact fact slots before publication. Stream only validated closed content;
never publish incomplete JSON. Text is generated in one structured streaming call, not one call per block.
Failure/cancellation retains visible partial content and never sends successful done. done remains the
persisted authoritative full response; cached retries reuse the same IDs and data.

The web renderer consumes ordered blocks, looks up artifacts/datasets and reuses safe DOM/SVG charts.
It must not duplicate compatibility text or append legacy charts when the block protocol is present.
Older responses retain the existing renderer. Unsupported/missing charts show a readable fallback.
