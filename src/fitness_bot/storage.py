"""Durable conversation turns, request deduplication and database-backed leases."""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from fitness_bot.schemas import ChatRequest, Message


def utcnow():
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    active_request_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ChatTurn(Base):
    __tablename__ = "chat_turns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(36), unique=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    input_hash: Mapped[str] = mapped_column(String(64))
    user_text: Mapped[str] = mapped_column(Text)
    context: Mapped[dict] = mapped_column(JSON)
    assistant_text: Mapped[str] = mapped_column(Text, default="")
    response: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="generating")
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ChatTurnMedia(Base):
    __tablename__ = "chat_turn_media"

    request_id: Mapped[str] = mapped_column(ForeignKey("chat_turns.request_id"), primary_key=True)
    image_ids: Mapped[list] = mapped_column(JSON)


class TurnConflict(Exception):
    """A busy conversation or reused request ID with incompatible input."""


@dataclass
class StartedTurn:
    request: ChatRequest
    cached_response: dict | None = None


class ConversationStore:
    def __init__(self, url: str, lease_seconds: float = 120):
        self.engine = create_async_engine(url, pool_pre_ping=True)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.lease_seconds = lease_seconds

    async def initialize(self):
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def close(self):
        await self.engine.dispose()

    async def begin_turn(self, request: ChatRequest) -> StartedTurn:
        # Retry only the initial conversation/request uniqueness race, never generation.
        for attempt in range(2):
            try:
                return await self._begin_turn(request)
            except IntegrityError:
                if attempt:
                    raise TurnConflict(
                        "Concurrent request; retry with the same request_id"
                    ) from None
        raise AssertionError("Unreachable")

    async def _begin_turn(self, request: ChatRequest) -> StartedTurn:
        cid, rid = str(request.conversation_id), str(request.request_id)
        digest = hashlib.sha256(
            json.dumps(
                request.model_dump(
                    mode="json",
                    exclude={"history", "previous_result_ids"}
                    | ({"images"} if not request.images else set()),
                ),
                sort_keys=True,
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
        async with self.sessions.begin() as session:
            existing = await session.scalar(select(ChatTurn).where(ChatTurn.request_id == rid))
            if existing:
                if existing.input_hash != digest:
                    raise TurnConflict("request_id was already used for different input")
                if existing.status == "completed":
                    return StartedTurn(request, existing.response)
                raise TurnConflict("Request already exists; use a new request_id to retry failures")
            conversation = await session.scalar(
                select(Conversation).where(Conversation.id == cid).with_for_update()
            )
            now = utcnow()
            if conversation is None:
                conversation = Conversation(id=cid, title=request.message[:120] or "图片对话")
                session.add(conversation)
                await session.flush()
            elif conversation.active_request_id:
                if conversation.lease_expires_at and conversation.lease_expires_at > now:
                    raise TurnConflict("Conversation is already generating a reply")
                interrupted = await session.scalar(
                    select(ChatTurn).where(ChatTurn.request_id == conversation.active_request_id)
                )
                if interrupted and interrupted.status == "generating":
                    interrupted.status, interrupted.error_code = "failed", "interrupted"
                    interrupted.updated_at = now
            previous = list(
                (
                    await session.scalars(
                        select(ChatTurn)
                        .where(ChatTurn.conversation_id == cid, ChatTurn.status == "completed")
                        .order_by(ChatTurn.id.desc())
                        .limit(15)
                    )
                ).all()
            )
            media = {
                row.request_id: row.image_ids
                for row in await session.scalars(
                    select(ChatTurnMedia).where(
                        ChatTurnMedia.request_id.in_([turn.request_id for turn in previous])
                    )
                )
            }
            history = []
            for turn in reversed(previous):
                history.append(
                    Message(
                        role="user",
                        text=turn.user_text[:4000],
                        images=media.get(turn.request_id, []),
                    )
                )
                if turn.assistant_text.strip():
                    history.append(Message(role="assistant", text=turn.assistant_text[:4000]))
            # Followup chat turns have no new cards. Keep the most recent visible group.
            cards = next(
                (cards for turn in previous if (cards := (turn.response or {}).get("cards"))),
                [],
            )
            prepared = request.model_copy(
                update={
                    "history": history[-30:],
                    "previous_result_ids": [c["exercise"]["id"] for c in cards][:3],
                }
            )
            session.add(
                ChatTurn(
                    request_id=rid,
                    conversation_id=cid,
                    input_hash=digest,
                    user_text=request.message,
                    context=request.context.model_dump(mode="json"),
                )
            )
            if request.images:
                await session.flush()
                session.add(ChatTurnMedia(request_id=rid, image_ids=request.images))
            conversation.active_request_id = rid
            conversation.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
            conversation.updated_at = now
        return StartedTurn(prepared)

    async def complete(self, request_id: UUID, response: dict):
        async with self.sessions.begin() as session:
            turn = await session.scalar(
                select(ChatTurn).where(ChatTurn.request_id == str(request_id))
            )
            if turn is None:
                raise TurnConflict("Unknown request")
            conversation = await session.scalar(
                select(Conversation)
                .where(Conversation.id == turn.conversation_id)
                .with_for_update()
            )
            if conversation.active_request_id != str(request_id) or turn.status != "generating":
                raise TurnConflict("Generation lease expired")
            turn.assistant_text = response["text"]
            turn.response, turn.status, turn.updated_at = response, "completed", utcnow()
            conversation.active_request_id = None
            conversation.lease_expires_at = None
            conversation.updated_at = utcnow()

    async def fail(self, request_id: UUID, partial_text: str, code: str):
        async with self.sessions.begin() as session:
            turn = await session.scalar(
                select(ChatTurn).where(ChatTurn.request_id == str(request_id))
            )
            if turn is None or turn.status != "generating":
                return
            conversation = await session.scalar(
                select(Conversation)
                .where(Conversation.id == turn.conversation_id)
                .with_for_update()
            )
            turn.assistant_text = partial_text
            turn.status = "cancelled" if code == "cancelled" else "failed"
            turn.error_code, turn.updated_at = code, utcnow()
            if conversation.active_request_id == str(request_id):
                conversation.active_request_id, conversation.lease_expires_at = None, None
                conversation.updated_at = utcnow()

    async def conversations(self, limit=50, offset=0):
        async with self.sessions() as session:
            rows = await session.scalars(
                select(Conversation)
                .order_by(Conversation.updated_at.desc(), Conversation.id)
                .limit(limit)
                .offset(offset)
            )
            return [
                {
                    "id": r.id,
                    "title": r.title,
                    "created_at": r.created_at,
                    "updated_at": r.updated_at,
                }
                for r in rows
            ]

    async def turns(self, conversation_id: UUID, *, after_id=0, limit=100):
        async with self.sessions() as session:
            conversation = await session.get(Conversation, str(conversation_id))
            if conversation is None:
                return None
            rows = await session.scalars(
                select(ChatTurn)
                .where(ChatTurn.conversation_id == str(conversation_id), ChatTurn.id > after_id)
                .order_by(ChatTurn.id)
                .limit(limit)
            )
            rows = list(rows)
            media = {
                row.request_id: row.image_ids
                for row in await session.scalars(
                    select(ChatTurnMedia).where(
                        ChatTurnMedia.request_id.in_([turn.request_id for turn in rows])
                    )
                )
            }
            return [
                {
                    "id": r.id,
                    "request_id": r.request_id,
                    "user_text": r.user_text,
                    "images": media.get(r.request_id, []),
                    "assistant_text": r.assistant_text,
                    "context": r.context,
                    "response": r.response,
                    "status": r.status,
                    "error_code": r.error_code,
                    "created_at": r.created_at,
                    "updated_at": r.updated_at,
                }
                for r in rows
            ]
