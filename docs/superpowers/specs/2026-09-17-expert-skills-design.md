# Chatbot expert skills and review scopes

## Confirmed business design

Keep five user intents: consultation, exercise search, exercise recommendation, training review, and session-plan generation/revision. Expert methods are two independently maintained runtime skills: **training-review** (understand past training) and **training-recommendation** (decide next steps). A skill is trusted packaged method content, with inputs, procedure, evidence limits, output expectations and examples; clients cannot supply skill names, paths or instructions. Recommendations consume server review facts without requiring a separate model-generated review report on every turn.

Preserve text, search/recommendation cards, structured reviews, informational previews and complete versioned plans. Multiple recommendation cards remain alternatives. Acceptance remains distinct from execution; no skill mutates actual workout snapshots.

Review supports single completed workouts and date-window reviews. Single reviews expose recorded exercise order, ordered sets and load/repetition/effort changes, primary/secondary body-part context, duration and recorded entry gaps. No actual set timestamps exist in the upload schema: rest intervals must remain unknown. Multi reviews expose workout spacing, repeat-body-part spacing, distributions, and explicitly confirmed cycle context. Observed cadence must not be relabeled as a training cycle. Missing, ambiguous and truncated evidence remains explicit.

## Implementation contract

`ReviewOptions` gains `scope: single|multiple` (default multiple), optional `source_id` and `workout_id`; identifiers must be paired and only valid for single scope. Single without IDs selects latest completed workout before the requested cutoff; explicit unavailable/foreign/deleted/active targets return not found, never substitute another workout. Scope is available through existing review API and model classification schema. Facts retain existing fields for compatibility and add scope, selected session details, interval evidence and confirmed cycle context. Reviews retain facts/findings/limitations and render the selected scope in Markdown and Playground evidence panels.

Runtime skill content is shipped under `src/fitness_bot/skills/`, loaded from a fixed allowlist via package resources and composed with existing role/rules/task-output constraints. Search and generic conversation stay lightweight; recommendation, preview and plan tasks use recommendation methods; structured review uses review methods. Existing programmatic safety, ownership, version, persistence and dose gates remain authoritative.

## Acceptance

- Skill resources are loaded in actual captured model messages, have isolated method boundaries, and are packaged in wheel.
- Synthetic single review proves ordered exercises/sets, recorded numeric progression, duration, interval uncertainty and user isolation; unavailable explicit targets never silently fall back.
- Synthetic multi review proves within-day and cross-day spacing, body-part recurrence and known-versus-unknown cycle context.
- Chat chooses single scope, restores server facts, emits and persists a scope-aware report; all five intents and five result forms remain compatible.
- Full Python and web suites, Ruff lint/format, packaging checks. No real model calls in tests.

The user authorized implementation and acceptance in this conversation. Work builds on the existing dirty worktree; unrelated work is preserved and no bulk commit is made.
