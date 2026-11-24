"""Durable conversation turns, request deduplication and database-backed leases."""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
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


class ObjectNotFound(Exception):
    """Object absent or owned by another user."""


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
        self.default_user_id = "local-user"

    async def initialize(self, default_user_id="local-user", image_directory=None):
        from fitness_bot import (  # noqa: F401
            custom_exercise_storage,
            training_storage,
            workout_storage,
        )
        from fitness_bot.identity import IdentityStore

        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        await IdentityStore(self).migrate(default_user_id, image_directory)
        self.default_user_id = default_user_id

    async def resolve_user(self, user_id=None):
        from fitness_bot.identity import IdentityStore

        return await IdentityStore(self).require_user_id(user_id or self.default_user_id)

    async def close(self):
        await self.engine.dispose()

    async def begin_turn(self, request: ChatRequest, *, user_id=None) -> StartedTurn:
        user_id = await self.resolve_user(user_id)
        # Retry only the initial conversation/request uniqueness race, never generation.
        for attempt in range(2):
            try:
                return await self._begin_turn(request, user_id)
            except IntegrityError:
                if attempt:
                    raise TurnConflict(
                        "Concurrent request; retry with the same request_id"
                    ) from None
        raise AssertionError("Unreachable")

    async def _begin_turn(self, request: ChatRequest, user_id) -> StartedTurn:
        from fitness_bot.identity import ConversationOwner, ImageOwner
        from fitness_bot.training_schemas import ReviewOptions

        cid, rid = str(request.conversation_id), str(request.request_id)
        digest = hashlib.sha256(
            json.dumps(
                request.model_dump(
                    mode="json",
                    exclude={
                        "history": True,
                        "previous_result_ids": True,
                        "context": {
                            "custom_exercise_drafts",
                            "workout_history",
                            "training_review",
                            "training_profile",
                            "session_plans",
                            "planning_target_date",
                            *(
                                field
                                for field in (
                                    "planning_target",
                                    "current_workout",
                                    "remaining_minutes",
                                )
                                if getattr(request.context, field) is None
                            ),
                            *(
                                {"plan_to_modify"}
                                if request.context.plan_to_modify is None
                                else set()
                            ),
                            *(
                                {"review_options"}
                                if request.context.review_options == ReviewOptions()
                                else set()
                            ),
                        },
                        **({"images": True} if not request.images else {}),
                    },
                ),
                sort_keys=True,
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
        async with self.sessions.begin() as session:
            owner = await session.get(ConversationOwner, cid)
            if owner is not None and owner.user_id != user_id:
                raise ObjectNotFound("Conversation not found")
            existing = await session.scalar(select(ChatTurn).where(ChatTurn.request_id == rid))
            if existing:
                existing_owner = await session.get(ConversationOwner, existing.conversation_id)
                if existing_owner is None or existing_owner.user_id != user_id:
                    raise ObjectNotFound("Request not found")
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
                session.add(ConversationOwner(conversation_id=cid, user_id=user_id))
            elif owner is None:
                raise ObjectNotFound("Conversation not found")
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
            owned_images = set(
                await session.scalars(
                    select(ImageOwner.image_id).where(ImageOwner.user_id == user_id)
                )
            )
            history = []
            for turn in reversed(previous):
                history.append(
                    Message(
                        role="user",
                        text=turn.user_text[:4000],
                        images=[
                            image_id
                            for image_id in media.get(turn.request_id, [])
                            if image_id in owned_images
                        ],
                    )
                )
                if turn.assistant_text.strip():
                    history.append(Message(role="assistant", text=turn.assistant_text[:4000]))
            # Followup chat turns have no new cards. Keep the most recent visible group.
            cards = next(
                (cards for turn in previous if (cards := (turn.response or {}).get("cards"))),
                [],
            )
            from fitness_bot.plan_inputs import restore_conversation_inputs
            from fitness_bot.training_review import load_training_review
            from fitness_bot.training_storage import load_session_plans, load_training_profile
            from fitness_bot.workout_context import load_workout_history

            profile = restore_conversation_inputs(
                await load_training_profile(session, user_id), previous
            )
            # Restore only intent/date. App-owned snapshots and remaining time
            # must come from this request, never a stale saved conversation turn.
            prior_planning = next(
                (
                    turn.context
                    for turn in previous
                    if (turn.context or {}).get("planning_target") is not None
                ),
                {},
            )
            target = request.context.planning_target or prior_planning.get("planning_target")
            prior_date = next(
                (
                    (turn.response or {}).get("session_plan")
                    or (turn.response or {}).get("plan_preview")
                    for turn in previous
                    if (turn.response or {}).get("session_plan")
                    or (turn.response or {}).get("plan_preview")
                ),
                {},
            )
            target_date = (
                prior_date.get("target_date")
                if (prior_date.get("planning_target", "next_workout") == (target or "next_workout"))
                else None
            )
            latest_context = previous[0].context if previous else {}
            if "planning_target_date" in latest_context:
                target_date = (
                    latest_context["planning_target_date"]
                    if (latest_context.get("planning_target") or "next_workout")
                    == (target or "next_workout")
                    else None
                )
            options = request.context.review_options
            if "time_zone" not in options.model_fields_set:
                options = options.model_copy(update={"time_zone": profile["profile"]["time_zone"]})

            context = request.context.model_copy(
                update={
                    "planning_target": target,
                    "planning_target_date": date.fromisoformat(target_date)
                    if target_date
                    else None,
                    "custom_exercise_drafts": [
                        draft
                        for turn in reversed(previous)
                        if (draft := (turn.response or {}).get("custom_exercise"))
                        and draft.get("status") == "draft"
                        and not draft.get("saved_result")
                    ][-3:],
                    "workout_history": await load_workout_history(session, user_id),
                    "training_review": await load_training_review(session, user_id, options),
                    "training_profile": profile,
                    "session_plans": await load_session_plans(session, user_id),
                }
            )
            prepared = request.model_copy(
                update={
                    "history": history[-30:],
                    "previous_result_ids": [c["exercise"]["id"] for c in cards][:3],
                    "context": context,
                }
            )
            prepared._user_id = user_id
            session.add(
                ChatTurn(
                    request_id=rid,
                    conversation_id=cid,
                    input_hash=digest,
                    user_text=request.message,
                    context=context.model_dump(mode="json"),
                )
            )
            if request.images:
                await session.flush()
                session.add(ChatTurnMedia(request_id=rid, image_ids=request.images))
            conversation.active_request_id = rid
            conversation.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
            conversation.updated_at = now
        return StartedTurn(prepared)

    async def complete(self, request_id: UUID, response: dict, *, user_id=None, context=None):
        from fitness_bot.identity import ConversationOwner

        user_id = await self.resolve_user(user_id)
        async with self.sessions.begin() as session:
            turn = await session.scalar(
                select(ChatTurn).where(ChatTurn.request_id == str(request_id))
            )
            if turn is None:
                raise TurnConflict("Unknown request")
            owner = await session.get(ConversationOwner, turn.conversation_id)
            if owner is None or owner.user_id != user_id:
                raise ObjectNotFound("Request not found")
            conversation = await session.scalar(
                select(Conversation)
                .where(Conversation.id == turn.conversation_id)
                .with_for_update()
            )
            if conversation.active_request_id != str(request_id) or turn.status != "generating":
                raise TurnConflict("Generation lease expired")
            turn.assistant_text = response["text"]
            if context is not None:
                turn.context = context.model_dump(mode="json")
            if plan := response.get("session_plan"):
                from fitness_bot.training_storage import persist_session_plan

                await persist_session_plan(session, user_id, request_id, plan)
            turn.response, turn.status, turn.updated_at = response, "completed", utcnow()
            conversation.active_request_id = None
            conversation.lease_expires_at = None
            conversation.updated_at = utcnow()

    async def fail(
        self,
        request_id: UUID,
        partial_text: str,
        code: str,
        *,
        user_id=None,
        partial_response: dict | None = None,
    ):
        from fitness_bot.identity import ConversationOwner

        user_id = await self.resolve_user(user_id)
        async with self.sessions.begin() as session:
            turn = await session.scalar(
                select(ChatTurn).where(ChatTurn.request_id == str(request_id))
            )
            if turn is None or turn.status != "generating":
                return
            owner = await session.get(ConversationOwner, turn.conversation_id)
            if owner is None or owner.user_id != user_id:
                raise ObjectNotFound("Request not found")
            conversation = await session.scalar(
                select(Conversation)
                .where(Conversation.id == turn.conversation_id)
                .with_for_update()
            )
            turn.assistant_text = partial_text
            if partial_response is not None:
                turn.response = partial_response
            turn.status = "cancelled" if code == "cancelled" else "failed"
            turn.error_code, turn.updated_at = code, utcnow()
            if conversation.active_request_id == str(request_id):
                conversation.active_request_id, conversation.lease_expires_at = None, None
                conversation.updated_at = utcnow()

    async def conversations(self, limit=50, offset=0, *, user_id=None):
        from fitness_bot.identity import ConversationOwner

        user_id = await self.resolve_user(user_id)
        async with self.sessions() as session:
            rows = await session.scalars(
                select(Conversation)
                .join(ConversationOwner, ConversationOwner.conversation_id == Conversation.id)
                .where(ConversationOwner.user_id == user_id)
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

    async def turns(self, conversation_id: UUID, *, after_id=0, limit=100, user_id=None):
        from fitness_bot.identity import ConversationOwner

        user_id = await self.resolve_user(user_id)
        async with self.sessions() as session:
            conversation = await session.get(Conversation, str(conversation_id))
            owner = await session.get(ConversationOwner, str(conversation_id))
            if conversation is None or owner is None or owner.user_id != user_id:
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
