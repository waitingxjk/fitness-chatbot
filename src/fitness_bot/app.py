import asyncio
import json
import logging
from contextlib import aclosing, asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from fitness_bot.agent import CompanionAgent
from fitness_bot.catalog import Catalog
from fitness_bot.config import Settings
from fitness_bot.model import OpenAICompanionModel
from fitness_bot.schemas import ChatRequest, Exercise, SearchPlan

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, agent: CompanionAgent | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.catalog = agent.catalog if agent else Catalog.load()
        app.state.agent = agent
        if agent is None and settings.model_configured:
            app.state.agent = CompanionAgent(app.state.catalog, OpenAICompanionModel(settings))
        yield

    app = FastAPI(title="练伴 API", version="0.1.0", lifespan=lifespan)

    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "catalog_count": len(app.state.catalog.exercises),
            "catalog_version": app.state.catalog.version,
            "chat_configured": app.state.agent is not None,
        }

    @app.get("/v1/exercises", response_model=list[Exercise])
    async def search_exercises(
        q: str = Query(default="", max_length=200),
        body_part: str | None = Query(default=None, max_length=64),
        equipment: str | None = Query(default=None, max_length=64),
        target_muscle: str | None = Query(default=None, max_length=64),
        limit: int = Query(default=20, ge=1, le=100),
    ):
        return app.state.catalog.search(
            SearchPlan(
                query=q,
                body_part=body_part,
                equipment=equipment,
                target_muscle=target_muscle,
            ),
            limit,
        )

    @app.get("/v1/exercises/{exercise_id}", response_model=Exercise)
    async def get_exercise(exercise_id: str):
        exercise = app.state.catalog.get(exercise_id)
        if exercise is None:
            raise HTTPException(404, "Exercise not found")
        return exercise

    @app.post(
        "/v1/chat",
        responses={
            200: {
                "description": "UTF-8 NDJSON stream; see docs/api.md for event schemas.",
                "content": {"application/x-ndjson": {"schema": {"type": "string"}}},
            }
        },
        response_class=StreamingResponse,
    )
    async def chat(payload: ChatRequest, request: Request):
        if app.state.agent is None:
            raise HTTPException(
                503, "Chat model not configured; set OPENAI_API_KEY and OPENAI_MODEL"
            )
        request_id = str(uuid4())

        async def stream():
            sequence = 0

            def encode(event):
                nonlocal sequence
                sequence += 1
                return (
                    json.dumps(
                        {
                            **event,
                            "request_id": request_id,
                            "conversation_id": str(payload.conversation_id),
                            "sequence": sequence,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )

            try:
                async with (
                    asyncio.timeout(settings.chat_timeout_seconds),
                    aclosing(app.state.agent.stream(payload)) as events,
                ):
                    async for event in events:
                        if await request.is_disconnected():
                            return
                        yield encode(event)
            except TimeoutError:
                yield encode({"type": "error", "code": "timeout", "message": "回复超时，请重试。"})
            except Exception as exc:
                # Do not log provider payloads, credentials, user text, or training history.
                logger.warning(
                    "Chat failed request_id=%s exception_type=%s", request_id, type(exc).__name__
                )
                yield encode(
                    {"type": "error", "code": "generation_failed", "message": "回复失败，请重试。"}
                )

        return StreamingResponse(
            stream(),
            media_type="application/x-ndjson",
            headers={
                "Cache-Control": "no-cache, no-store",
                "X-Accel-Buffering": "no",
                "X-Request-ID": request_id,
            },
        )

    return app
