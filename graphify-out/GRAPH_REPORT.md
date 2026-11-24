# Graph Report - fitness-bot  (2026-09-17)

## Corpus Check
- 108 files · ~114,392 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 6 file(s) not represented in the graph (top: (none) 2, .example 1, .exercises-dataset 1)

## Summary
- 1046 nodes · 2950 edges · 51 communities (47 shown, 4 thin omitted)
- Extraction: 83% EXTRACTED · 17% INFERRED · 0% AMBIGUOUS · INFERRED: 494 edges (avg confidence: 0.93)
- Token cost: unavailable — host subagent tools do not expose usage. AST extraction uses no LLM; semantic extraction used two host subagents. Zero counters in raw extraction are placeholders, not measured usage.

## Community Hubs (Navigation)
- Playground Chat Interface
- Training Plan Lifecycle
- Training Evidence Contract Tests
- Multimodal Model Integration
- Application Bootstrap Tests
- Identity Ownership Migrations
- Chat Streaming Contract Tests
- Training Prescription Validation Tests
- Agent Schema Integration
- Web Prescription Editors
- Vendored Markdown Engine
- Model Response Contracts
- Workout Context API Tests
- Catalog Preview Tests
- Workout Storage Persistence
- Training Decision Orchestration
- Command Line Tools
- Training Evidence Principles
- Training Input Schemas
- Training Review Conversation Tests
- HTTP Application Routes
- Persistence Architecture Decisions
- Training Playground Design
- Workout Snapshot Schemas
- Conversation Persistence
- Safe Markdown Rendering
- Structured Suggestion Design
- Agent Orchestration Tests
- Backend API Architecture
- Markdown Inline Parsing
- Identity Authorization Tests
- Prescription Dose Validation
- Workout Version Conflicts
- Chat API Tests
- Third Party Attribution
- Qualitative Preview Validation
- Historical Prompt Architecture
- Catalog Snapshot Manifest
- Prescription Evidence Alignment
- Bounded Workout Context
- Markdown Tokenization
- Image Upload Design
- Search Transaction Retry Tests
- Markdown Block Rendering
- Workout Request Validation
- User Bootstrap Schema
- Markdown List Rendering
- Markdown Parser Entry
- Project Package Metadata
- Playground Brand Icon

## God Nodes (most connected - your core abstractions)
1. `Catalog` - 77 edges
2. `SearchPlan` - 74 edges
3. `create_app()` - 69 edges
4. `Settings` - 60 edges
5. `CompanionAgent` - 57 edges
6. `ChatRequest` - 44 edges
7. `OpenAICompanionModel` - 32 edges
8. `ConversationStore` - 32 edges
9. `IdentityStore` - 30 edges
10. `validate_session_plan()` - 28 edges

## Surprising Connections (you probably didn't know these)
- `iOS owns workout mutation` --semantically_similar_to--> `Acceptance is distinct from workout start`  [INFERRED] [semantically similar]
  AGENTS.md → docs/superpowers/specs/2026-09-12-structured-suggestions-design.md
- `Packaged same-origin playground` --semantically_similar_to--> `Playground HTML`  [INFERRED] [semantically similar]
  docs/superpowers/plans/2026-09-07-web-playground.md → src/fitness_bot/web/index.html
- `main()` --uses--> `Exercise`  [INFERRED]
  scripts/sync_catalog.py → src/fitness_bot/schemas.py
- `produce()` --indirect_call--> `request()`  [INFERRED]
  src/fitness_bot/agent.py → tests/test_training_decision.py
- `test_legacy_base_url_and_secret_redaction()` --uses--> `Settings`  [INFERRED]
  tests/test_edges.py → src/fitness_bot/config.py

## Import Cycles
- 3-file cycle: `src/fitness_bot/identity.py -> src/fitness_bot/storage.py -> src/fitness_bot/training_storage.py -> src/fitness_bot/identity.py`
- 3-file cycle: `src/fitness_bot/identity.py -> src/fitness_bot/storage.py -> src/fitness_bot/workout_storage.py -> src/fitness_bot/identity.py`

## Communities (51 total, 4 thin omitted)

### Community 0 - "Playground Chat Interface"
Cohesion: 0.06
Nodes (72): ref_node_assert_strict, ref_node_test, addImages(), api(), busyUI(), clearAttachments(), clearIdentityView(), connect() (+64 more)

### Community 1 - "Training Plan Lifecycle"
Cohesion: 0.07
Nodes (61): collections, functools, post, PlanRevisionInput, BaseModel, User-authored immutable plan revisions and idempotent persistence., revised_plan(), RevisedItem (+53 more)

### Community 2 - "Training Evidence Contract Tests"
Cohesion: 0.09
Nodes (45): pytest, isolated_database(), fixture, Automated tests never connect to the development MySQL or use real credentials., edit_payload(), test_accepted_plan_can_be_adjusted_without_changing_original_prescription(), test_edit_rejects_unknown_exercises_and_excess_time(), test_invalid_or_stale_edit_leaves_original_untouched() (+37 more)

### Community 3 - "Multimodal Model Integration"
Cohesion: 0.07
Nodes (25): base64, io, langchain_core_messages, os, pil, Create local Docker MySQL credentials without replacing existing files., secrets, lifespan() (+17 more)

### Community 4 - "Application Bootstrap Tests"
Cohesion: 0.06
Nodes (26): BaseSettings, create_app(), chat(), stream(), Settings, main(), test_failure_log_identifies_code_location_without_sensitive_exception_text(), ImageModel (+18 more)

### Community 5 - "Identity Ownership Migrations"
Cohesion: 0.13
Nodes (23): DeclarativeBase, ConversationOwner, IdentityMigration, IdentityStore, ImageOwner, InstallationConflict, LegacyUser, LegacyUserAlias (+15 more)

### Community 6 - "Chat Streaming Contract Tests"
Cohesion: 0.06
Nodes (26): search_exercises(), SearchPlan, HistoryModel, test_followup_receives_full_cards_across_multiple_chat_turns(), plan(), test_http_restart_restores_context_and_duplicate_replays(), test_recommendation_cannot_substitute_an_unretrieved_exercise(), plan() (+18 more)

### Community 7 - "Training Prescription Validation Tests"
Cohesion: 0.21
Nodes (30): Path, validate_session_plan(), SessionDraft, test_catalog_integrity(), test_explicit_sets_drive_summary_and_time_including_warmup(), test_legacy_plan_expands_each_working_set_without_inventing_load(), test_model_cannot_bypass_effort_limits_through_per_set_values(), test_per_set_wrong_dose_type_is_rejected() (+22 more)

### Community 8 - "Agent Schema Integration"
Cohesion: 0.12
Nodes (22): langgraph_config, langgraph_graph, pydantic, pydantic_alias_generators, pydantic_settings, Synthetic, offline browser QA fixture. No private MySQL data or model calls., Loopback-only server for synthetic Flutter upload integration tests., socket (+14 more)

### Community 9 - "Web Prescription Editors"
Cohesion: 0.17
Nodes (21): button(), cleanRows(), conventions, copy(), exercisePrescriptionCard(), field(), paintRows(), loadLabel() (+13 more)

### Community 10 - "Vendored Markdown Engine"
Cohesion: 0.09
Nodes (15): constructor(), fences(), parseMarkdown(), postprocess(), preprocess(), processAllTokens(), provideLexer(), provideParser() (+7 more)

### Community 11 - "Model Response Contracts"
Cohesion: 0.14
Nodes (12): collections_abc, langchain_openai, Protocol, SyntheticModel, CompanionModel, OpenAI-compatible LLM boundary, replaceable in offline tests., ChatRequest, ExerciseCard (+4 more)

### Community 12 - "Workout Context API Tests"
Cohesion: 0.14
Nodes (17): copy, fixture, parametrize, test_commit_failure_returns_503_without_acknowledging_or_persisting(), test_structure_validation(), upload(), chat(), test_database_history_reaches_both_model_stages_and_persists() (+9 more)

### Community 13 - "Catalog Preview Tests"
Cohesion: 0.12
Nodes (11): Catalog, test_followup_prompt_includes_full_previous_exercise(), PreviewModel, An incomplete session request still has ordered, non-executable catalog cards., test_incomplete_session_returns_ordered_preview_cards_and_persists_them(), test_model_preview_preserves_cards_when_narrative_contains_unvalidated_doses(), test_preview_candidate_scope_uses_known_history_when_equipment_is_unknown(), test_preview_draft_cannot_carry_unvalidated_prescriptions_or_narrative_loads() (+3 more)

### Community 14 - "Workout Storage Persistence"
Cohesion: 0.17
Nodes (18): dataclasses, datetime, hashlib, json, sqlalchemy, sqlalchemy_dialects_mysql, sqlalchemy_exc, sqlalchemy_ext_asyncio (+10 more)

### Community 15 - "Training Decision Orchestration"
Cohesion: 0.16
Nodes (17): AgentState, legacy_sets(), Expand old immutable snapshots without rewriting them., ChatResponse, ProcessingStep, preview_candidates(), preview_cards(), Qualitative catalog previews; never prescriptions or accepted workout plans. (+9 more)

### Community 16 - "Command Line Tools"
Cohesion: 0.10
Nodes (16): argparse, asyncio, httpx, pathlib, Stream a chat response: uv run python scripts/chat.py '找两个哑铃胸部动作'., Measure full-catalog prompt variants locally; does not call a model API., main(), Administrator-only CLI: issue a secret once, or revoke by non-secret token ID. (+8 more)

### Community 17 - "Training Evidence Principles"
Cohesion: 0.13
Nodes (22): Training Evidence Register, E1 resistance training principles, E2 exercise order, E3 free weights and machines, E4 foam rolling, E5 deloading consensus, Goal-dependent product applicability, New Functional Training for Sports (+14 more)

### Community 18 - "Training Input Schemas"
Cohesion: 0.16
Nodes (17): SetDose, summarize_sets(), ConfirmedCycle, EffortReport, ExerciseSuggestionDraft, ObservationSource, PreviewExercise, model_validator (+9 more)

### Community 19 - "Training Review Conversation Tests"
Cohesion: 0.12
Nodes (12): fastapi_testclient, Conversation can progress before a quantified session is ready to generate., test_accepting_previous_offer_with_target_date_streams_successfully(), ainvoke(), test_missing_profile_uses_history_aware_dialogue_instead_of_repeating_gate(), plan(), OfflineModel, test_boundaries_units_invalid_sets_and_source_references() (+4 more)

### Community 20 - "HTTP Application Routes"
Cohesion: 0.14
Nodes (18): anyio, contextlib, FastAPI, fastapi_exceptions, fastapi_responses, fastapi_routing, fastapi_staticfiles, Identifier (+10 more)

### Community 21 - "Persistence Architecture Decisions"
Cohesion: 0.12
Nodes (19): Repository Rules, Python 3.12 and uv, Vector RAG paused, Server-authoritative MySQL history, iOS owns workout mutation, Local MySQL Compose, Loopback port 13306, MySQL 8.4 service (+11 more)

### Community 22 - "Training Playground Design"
Cohesion: 0.12
Nodes (19): Immutable SessionPlan lifecycle, Complete date-window training facts, Web Playground Plan, Packaged same-origin playground, Observable trace, no hidden reasoning, Training Decision Implementation Plan, Atomic immutable plan lifecycle, Constrained structured review and plan (+11 more)

### Community 23 - "Workout Snapshot Schemas"
Cohesion: 0.17
Nodes (12): math, finite_json(), BaseModel, model_validator, iOS-owned workout snapshots. Validation never changes training semantics., SnapshotModel, StoredWorkout, WorkoutReceipt (+4 more)

### Community 24 - "Conversation Persistence"
Cohesion: 0.19
Nodes (8): Message, ChatTurn, ChatTurnMedia, ConversationStore, UUID, StartedTurn, test_legacy_migration_preserves_data_and_never_readopts(), test_concurrent_bootstrap_does_not_create_extra_users()

### Community 25 - "Safe Markdown Rendering"
Cohesion: 0.20
Nodes (11): decode(), element(), link(), markdownNodes(), renderMarkdown(), safeLink(), tokensToNodes(), inline() (+3 more)

### Community 26 - "Structured Suggestion Design"
Cohesion: 0.14
Nodes (16): Incomplete informational plan preview, Immutable user-edited plan successors, Structured Suggestions Implementation Plan, Explicit Flutter pending-plan start, Immutable user adjustment endpoint, Shared per-set backend/web/Flutter contract, Cross-client contract verification, 182 Python and 31 web tests reported (+8 more)

### Community 27 - "Agent Orchestration Tests"
Cohesion: 0.22
Nodes (10): shutil, CompanionAgent, produce(), PlannedModel, test_followup_context_is_forwarded_without_cross_request_memory(), test_legacy_base_url_and_secret_redaction(), test_no_results_and_chat_do_not_fabricate_cards(), test_planner_only_receives_terms_usable_as_primary_filters() (+2 more)

### Community 28 - "Backend API Architecture"
Cohesion: 0.16
Nodes (14): Revocable observation attestations, POST /v1/users/bootstrap, POST /v1/chat NDJSON contract, HTTP API v1, Unified user identity and authorization, Latest-pair progression eligibility, Monotonic workout snapshot backup, Versioned evidence rule records (+6 more)

### Community 29 - "Markdown Inline Parsing"
Cohesion: 0.19
Nodes (14): br(), codespan(), del(), em(), fe(), html(), image(), J() (+6 more)

### Community 30 - "Identity Authorization Tests"
Cohesion: 0.26
Nodes (10): headers_for(), make_app(), Model, parametrize, test_chat_attachment_not_found_does_not_disclose_existence(), test_chat_owner_and_request_replay(), test_concurrent_migration_runs_once(), test_disabled_identity_validation_and_case_sensitive_workouts() (+2 more)

### Community 31 - "Prescription Dose Validation"
Cohesion: 0.22
Nodes (9): re, model_sets(), PrescriptionLoad, BaseModel, model_validator, Shared prescription contract; planned targets never imply completed activity., trial_load(), validate_dose_type() (+1 more)

### Community 32 - "Workout Version Conflicts"
Cohesion: 0.19
Nodes (8): Exception, A stale upload or different content for an already accepted version., receipt(), snapshot(), WorkoutConflict, WorkoutStore, fixture, stores()

### Community 33 - "Chat API Tests"
Cohesion: 0.19
Nodes (9): catalog(), FakeModel, fixture, parametrize, test_full_catalog_and_search(), test_graph_stream_cards_and_context(), test_http_stream_and_validation(), test_missing_model_config_returns_503() (+1 more)

### Community 34 - "Third Party Attribution"
Cohesion: 0.18
Nodes (11): Non-media dataset MIT license, Upstream Media Notice, Gym visual attribution, Separate media permission and 180px limit, Marked License, Markdown redistribution conditions, Marked MIT copyright notice, hasaneyldrm exercise dataset (+3 more)

### Community 35 - "Qualitative Preview Validation"
Cohesion: 0.24
Nodes (6): validate_prescription_reason(), qualitative_preview(), prose(), Replace invalid dose prose without inventing or weakening exercise data., field_validator, test_preview_prose_recovery_does_not_relax_the_structural_contract()

### Community 36 - "Historical Prompt Architecture"
Cohesion: 0.20
Nodes (10): General + shared + task prompts, Prompt Snapshot 2026-09-08, Prompt, schemas, and validation gates, Latest intent determines request routing, Missing-record policy alignment gap, Original client-supplied history design, Historical Backend Bootstrap Plan, Direct FastAPI NDJSON, no MCP (+2 more)

### Community 37 - "Catalog Snapshot Manifest"
Cohesion: 0.22
Nodes (8): catalog_version, exercise_count, media, sha256, catalog_terms_zh_hans.json, exercises_zh_hans.json, source, upstream

### Community 38 - "Prescription Evidence Alignment"
Cohesion: 0.25
Nodes (8): Authoritative per-set prescriptions, Text and card order agreement, Core maps to waist, not target core, Live Chat Smoke 2026-09-05, Two synthetic real-model smoke samples, P07 complete feasibility budget, Per-set dose, load, rest and effort, Shared structured suggestion cards

### Community 39 - "Bounded Workout Context"
Cohesion: 0.43
Nodes (6): StoredWorkoutHistory, _json_size(), load_workout_history(), _project(), Read-only, bounded projection of synced training for the model context., Use the caller's transaction and already resolved internal identity. Order by…

### Community 40 - "Markdown Tokenization"
Cohesion: 0.29
Nodes (7): blockTokens(), inlineTokens(), lex(), lexer(), lexInline(), me(), reflink()

### Community 41 - "Image Upload Design"
Cohesion: 0.50
Nodes (5): User-owned image uploads, Image Input Plan, chat_turn_media associations, Native image_url multimodal blocks, Local image ID upload contract

### Community 42 - "Search Transaction Retry Tests"
Cohesion: 0.40
Nodes (3): parametrize, test_mysql_deadlock_or_lock_timeout_retries_entire_transaction(), once()

### Community 43 - "Markdown Block Rendering"
Cohesion: 0.40
Nodes (5): blockquote(), code(), heading(), hr(), z()

### Community 45 - "User Bootstrap Schema"
Cohesion: 0.50
Nodes (3): BootstrapRequest, BaseModel, field_validator

### Community 46 - "Markdown List Rendering"
Cohesion: 0.67
Nodes (3): checkbox(), list(), listitem()

### Community 47 - "Markdown Parser Entry"
Cohesion: 0.67
Nodes (3): k(), parse(), parser()

## Knowledge Gaps
- **51 isolated node(s):** `fitness-bot`, `source`, `upstream`, `catalog_version`, `exercise_count` (+46 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 236 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **4 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `SearchPlan` connect `Chat Streaming Contract Tests` to `Training Plan Lifecycle`, `Chat API Tests`, `Multimodal Model Integration`, `Application Bootstrap Tests`, `Training Evidence Contract Tests`, `Agent Schema Integration`, `Search Transaction Retry Tests`, `Model Response Contracts`, `Catalog Preview Tests`, `Training Decision Orchestration`, `Training Input Schemas`, `Training Review Conversation Tests`, `HTTP Application Routes`, `Agent Orchestration Tests`, `Identity Authorization Tests`?**
  _High betweenness centrality (0.056) - this node is a cross-community bridge._
- **Why does `create_app()` connect `Application Bootstrap Tests` to `Workout Version Conflicts`, `Training Plan Lifecycle`, `Chat API Tests`, `Multimodal Model Integration`, `Training Evidence Contract Tests`, `Identity Ownership Migrations`, `Chat Streaming Contract Tests`, `Agent Schema Integration`, `Model Response Contracts`, `Catalog Preview Tests`, `User Bootstrap Schema`, `Command Line Tools`, `Training Review Conversation Tests`, `HTTP Application Routes`, `Conversation Persistence`, `Agent Orchestration Tests`, `Identity Authorization Tests`?**
  _High betweenness centrality (0.048) - this node is a cross-community bridge._
- **Why does `Catalog` connect `Catalog Preview Tests` to `Training Plan Lifecycle`, `Chat API Tests`, `Multimodal Model Integration`, `Application Bootstrap Tests`, `Training Evidence Contract Tests`, `Chat Streaming Contract Tests`, `Training Prescription Validation Tests`, `Agent Schema Integration`, `Search Transaction Retry Tests`, `Training Review Conversation Tests`, `HTTP Application Routes`, `Agent Orchestration Tests`, `Identity Authorization Tests`?**
  _High betweenness centrality (0.026) - this node is a cross-community bridge._
- **Are the 66 inferred relationships involving `Catalog` (e.g. with `CompanionAgent` and `create_app()`) actually correct?**
  _`Catalog` has 66 INFERRED edges - model-reasoned connections that need verification._
- **Are the 42 inferred relationships involving `SearchPlan` (e.g. with `SyntheticModel` and `AgentState`) actually correct?**
  _`SearchPlan` has 42 INFERRED edges - model-reasoned connections that need verification._
- **Are the 9 inferred relationships involving `create_app()` (e.g. with `lifespan()` and `Catalog`) actually correct?**
  _`create_app()` has 9 INFERRED edges - model-reasoned connections that need verification._
- **Are the 51 inferred relationships involving `Settings` (e.g. with `run()` and `OpenAICompanionModel`) actually correct?**
  _`Settings` has 51 INFERRED edges - model-reasoned connections that need verification._
## Scope and Integrity Notes

- Scan root: `/Users/jiakaixiao/Projects/ios-apps/fitness-bot`; 108 supported files, 114,392 detected words. Existing uncommitted project work is included.
- Graph health warning: 265 dangling endpoints; 16 self-loops; 120 same-endpoint edge collapses. Raw extraction is retained in `extraction-audit.json`; see `graph-health.json` for counts and examples.
- Semantic extraction covered all 25 documents and one SVG. Plans, dated prompt snapshots, and verification reports describe their source-specific state; they must not all be interpreted as current implementation.
- Code/data files without extracted structural nodes: `docs/catalog-token-counts.json`, `docs/knowledge/training/evaluations/2026-09-08-synthetic-samples.json`, `src/fitness_bot/data/catalog_terms_zh_hans.json`, `src/fitness_bot/data/exercises_zh_hans.json`, `tests/fixtures/workout_upload.json`. Detection does not imply that the full contents of data records were indexed.
- This is a structural and documentary map, not a runtime trace. Low cohesion alone is not a refactoring recommendation.
- Tool runtime: Python 3.12, graphifyy 0.9.63; invoked skill version 0.9.53. The isolated environment is in `.venv/` inside this directory and is Git-ignored.

## Token Reduction Benchmark

```text
graphify token reduction benchmark
──────────────────────────────────────────────────
  Corpus:          52,300 words → ~69,733 tokens (naive)
  Graph:           1,046 nodes, 2,950 edges
  Avg query cost:  ~16,884 tokens
  Reduction:       4.1x fewer tokens per query

  Per question:
    [2.5x] what is the main entry point
    [9.9x] what connects the data layer to the api
    [4.6x] what are the core abstractions
```

This is a heuristic estimate. Its 52,300-word corpus estimate differs from detection (114,392 words); it is not measured token savings.
