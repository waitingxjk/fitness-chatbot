# Readable review narrative and evidence charts

User-approved direction: replace the repeated audit-form output with a clear leading assessment,
flexible topic sections and useful charts, taking the attached comparison as a writing/layout reference.
Do not copy its unverified personal prescriptions or assume its dates match current records.

Implementation:
1. Root: allow optional titled Markdown findings; keep legacy fields for stored/test compatibility but
   remove them from the runtime model schema. First finding is an untitled lead assessment, following
   findings have content-specific headings. No forced repeated comparison/explanation/missing-info labels.
2. Root: let prose embed exact server fact values through allowlisted `{{fact:KEY}}` references bound
   to its cited evidence. Resolve before rendering, never show placeholders. Preserve existing freeform
   prose validation outside references and keep all source facts available in collapsed detail.
3. Root: derive deterministic chart payloads from the selected snapshots: actual-date timeline,
   completed sets per session, primary body-part completed-set distribution. Unknown body parts stay
   visible. Data always reconciles to facts; no mixed-unit tonnage or invented fatigue scores.
4. Frontend sub-agent (subagent-driven-development): implement safe responsive chart renderer and tests
   using the payload contract below. Root integrates charts ahead of collapsed evidence and applies
   narrative render/prompt changes. Root reviews integration and regression behavior.
5. Verify full Python/web suites, Ruff, streaming prefix consistency, and actual desktop/mobile browser
   rendering with an injected model and synthetic data. Restart the user's port 8000 service afterward.

Chart contract: `review.charts` is an array of objects with `id`, `kind` (`timeline`, `columns`, `bars`),
`title`, `description`, `unit`, `points`, `truncated`. Every point has `label` and finite nonnegative
`value`; timeline/columns points also have ISO `at` and owned source refs. Timeline value is one per
session; x position is proportional to timestamp. Empty/no-measurement charts are omitted. Per-session
set bars use completed-set counts, including invalid doses as the existing aggregate does; label that
these are recorded completed sets. Primary body-part bars partition the selected completed-set count.

Files for frontend sub-agent: new `src/fitness_bot/web/review-charts.mjs`, append styles to
`src/fitness_bot/web/styles.css`, new `tests/web/review-charts.test.mjs`. No edits to other files.
Export `renderReviewCharts(root, charts)`; root integrates it. Safe DOM/SVG only, no dependencies/CDNs,
accessible labels plus an expandable data table, responsive desktop/mobile layout, honest zero baseline.
