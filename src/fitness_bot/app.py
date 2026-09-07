import asyncio
import json
import logging
from contextlib import aclosing, asynccontextmanager
from pathlib import Path
from uuid import UUID

import anyio
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError

from fitness_bot.agent import CompanionAgent
from fitness_bot.catalog import Catalog
from fitness_bot.config import Settings
from fitness_bot.images import IMAGE_TYPES, MAX_IMAGE_BYTES, ImageStore
from fitness_bot.model import OpenAICompanionModel
from fitness_bot.schemas import ChatRequest, Exercise, SearchPlan
from fitness_bot.storage import ConversationStore, TurnConflict

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, agent: CompanionAgent | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not settings.database_url:
            raise RuntimeError("DATABASE_URL required; run scripts/setup_mysql.py and start MySQL")
        app.state.store = ConversationStore(
            settings.database_url.get_secret_value(), settings.chat_timeout_seconds + 30
        )
        await app.state.store.initialize()
        app.state.images = ImageStore(settings.image_directory)
        app.state.catalog = agent.catalog if agent else Catalog.load()
        app.state.agent = agent
        if agent is None and settings.model_configured:
            app.state.agent = CompanionAgent(app.state.catalog, OpenAICompanionModel(settings))
        try:
            yield
        finally:
            await app.state.store.close()

    app = FastAPI(title="练伴 API", version="0.1.0", lifespan=lifespan)

    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "catalog_count": len(app.state.catalog.exercises),
            "catalog_version": app.state.catalog.version,
            "chat_configured": app.state.agent is not None,
            "history_storage": "database",
        }

    @app.get("/v1/conversations")
    async def list_conversations(
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ):
        return await app.state.store.conversations(limit, offset)

    @app.post("/v1/images", status_code=201)
    async def upload_image(request: Request):
        media_type = request.headers.get("content-type", "").split(";")[0].lower()
        if media_type not in IMAGE_TYPES.values():
            raise HTTPException(415, "仅支持 JPEG、PNG、WebP 静态图片。")
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > MAX_IMAGE_BYTES:
                raise HTTPException(413, "每张图片不能超过 5 MiB。")
            data.extend(chunk)
        try:
            return await asyncio.to_thread(app.state.images.save, bytes(data), media_type)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        except OSError:
            raise HTTPException(503, "本地图片存储不可用。") from None

    @app.get("/v1/images/{image_id}")
    async def image_file(image_id: str):
        try:
            asset = await asyncio.to_thread(app.state.images.describe, image_id)
        except (ValueError, FileNotFoundError):
            raise HTTPException(404, "图片不存在。") from None
        return FileResponse(
            app.state.images.path(image_id),
            media_type=asset["mime_type"],
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    @app.get("/v1/conversations/{conversation_id}/turns")
    async def list_turns(
        conversation_id: UUID,
        after_id: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=100),
    ):
        turns = await app.state.store.turns(conversation_id, after_id=after_id, limit=limit)
        if turns is None:
            raise HTTPException(404, "Conversation not found")
        for turn in turns:
            turn["images"] = [
                {"id": image_id, "url": f"/v1/images/{image_id}"} for image_id in turn["images"]
            ]
        return turns

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
        for image_id in payload.images:
            if not app.state.images.path(image_id).is_file():
                raise HTTPException(422, "图片不存在，请重新上传。")
        try:
            started = await app.state.store.begin_turn(payload)
        except TurnConflict as exc:
            raise HTTPException(409, str(exc)) from None
        except SQLAlchemyError:
            raise HTTPException(503, "Conversation storage unavailable") from None
        payload = started.request
        request_id = str(payload.request_id)

        async def stream():
            sequence = 0
            partial_text = ""
            persisted = started.cached_response is not None

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
                if started.cached_response is not None:
                    yield encode(
                        {"type": "done", "response": started.cached_response, "replayed": True}
                    )
                    return
                async with (
                    asyncio.timeout(settings.chat_timeout_seconds),
                    aclosing(app.state.agent.stream(payload)) as events,
                ):
                    async for event in events:
                        if await request.is_disconnected():
                            return
                        if event["type"] == "text_delta":
                            partial_text += event["delta"]
                        if event["type"] == "done":
                            await app.state.store.complete(payload.request_id, event["response"])
                            persisted = True
                        yield encode(event)
            except TimeoutError:
                await app.state.store.fail(payload.request_id, partial_text, "timeout")
                persisted = True
                yield encode({"type": "error", "code": "timeout", "message": "回复超时，请重试。"})
            except Exception as exc:
                # Do not log provider payloads, credentials, user text, or training history.
                logger.warning(
                    "Chat failed request_id=%s exception_type=%s", request_id, type(exc).__name__
                )
                try:
                    await app.state.store.fail(
                        payload.request_id, partial_text, "generation_failed"
                    )
                    persisted = True
                except SQLAlchemyError:
                    logger.error("Could not persist failure request_id=%s", request_id)
                yield encode(
                    {"type": "error", "code": "generation_failed", "message": "回复失败，请重试。"}
                )
            finally:
                if not persisted:
                    # Starlette cancels streaming tasks on disconnect. Shield the short DB cleanup.
                    with anyio.CancelScope(shield=True):
                        try:
                            async with asyncio.timeout(5):
                                await app.state.store.fail(
                                    payload.request_id, partial_text, "cancelled"
                                )
                        except (SQLAlchemyError, TimeoutError):
                            logger.error("Could not persist cancellation request_id=%s", request_id)

        return StreamingResponse(
            stream(),
            media_type="application/x-ndjson",
            headers={
                "Cache-Control": "no-cache, no-store",
                "X-Accel-Buffering": "no",
                "X-Request-ID": request_id,
            },
        )

    app.mount(
        "/playground",
        StaticFiles(directory=Path(__file__).parent / "web", html=True),
        name="playground",
    )

    return app
