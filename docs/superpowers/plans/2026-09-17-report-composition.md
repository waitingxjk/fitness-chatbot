# Ordered Report Composition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development.

**Goal:** Stream and persist portable ordered content blocks with separately referenced chart data.
**Architecture:** Add a bounded compose graph node and a pure compiler; generate grounded prose against
its frozen outline. Keep legacy text/events while the web renders the new blocks in order.
**Tech Stack:** Python 3.12, Pydantic, LangGraph, FastAPI NDJSON, browser DOM/SVG and Node tests.

Preserve this existing dirty worktree and branch. Do not commit unrelated accumulated changes.

- [x] Backend contract/compiler: create presentation.py with validated outline models, UUID allocation,
  adjacent-text normalization and chart registries derived only from review_charts(facts). Tests must
  assert text/text/chart/text/chart normalization, unique and stable references, missing chart/evidence
  rejection, empty-history fallback and bounded output. Run pytest tests/test_presentation.py red first.
- [x] Model and graph: add compose between retrieve/respond. Add model composition and generation methods
  using enum schemas for evidence, chart keys and frozen block IDs. Test prompt capture and structured
  snapshots. Check missing/reordered/rewritten blocks, invalid prose and provider cancellation.
- [x] Stream assembly: yield report_layout, block_start/delta/end, chart block in exact outline order;
  retain text_delta for old consumers. Attach blocks/resources to final response before DB commit.
  Test via HTTP including replay ID equality and partial failure/cancellation. Existing adapters without
  composition methods keep the validated review path; all final responses can carry a Markdown block.
- [x] Web worker: create content-blocks.mjs and tests, integrate stream.mjs/app.js/training.mjs. Verify
  interleaving, append-only block updates, authoritative done/history, malformed references, no HTML
  injection, legacy fallback and no duplicate charts/text. Own only assigned web files.
- [x] Review: independent spec and quality audits; fix material findings. Run Ruff, full pytest and all
  Node tests. Browser QA with an injected model and isolated database at desktop/mobile widths.
- [x] Delivery: update docs/api.md and README with the exact portable schema/events, compatibility and
  Flutter adoption requirements; restart port 8000 and verify health/page/resources. Record test results.
