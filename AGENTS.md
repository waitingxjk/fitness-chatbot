# fitness-bot

- Python 3.12 only. Manage dependencies with uv and run commands through uv run.
- FastAPI exposes direct HTTP APIs. POST /v1/chat uses UTF-8 NDJSON; no MCP server.
- Keep LangGraph orchestration, model adapter, catalog, and HTTP contracts separate.
- Preserve .env and never commit credentials, generated virtual environments, or private chat data.
- src/fitness_bot/data contains the complete iOS catalog snapshot. Preserve exercise IDs,
  mediaId, camelCase exercise fields, source attribution and manifest checksums.
- Sync with uv run python scripts/sync_catalog.py --source ../flutter-init.
- Return cards only for actual active catalog records. Search max 2; recommendation max 3.
- Do not mutate workout records. iOS owns add-to-workout validation and draft creation.
- Do not call real LLMs in automated tests. Inject a model at the application boundary.
- Chat history must persist in MySQL. Complete the database commit before emitting done.
- Use .env.mysql and compose.yaml for local MySQL; never remove its volume without authorization.
- Server history is authoritative. Ignore client-supplied history and previous_result_ids in HTTP calls.
- Vector RAG is paused pending evaluation of a compact full-catalog prompt. Do not add it implicitly.
- Verify changes with uv run ruff check ., uv run ruff format --check ., and uv run pytest.
