"""Upgrade a populated pre-internal-ID database, including real turn/media/snapshot content."""

import hashlib
import json
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from fitness_bot.identity import (
    ConversationOwner,
    IdentityMigration,
    IdentityStore,
    ImageOwner,
    LegacyUser,
    LegacyUserToken,
)
from fitness_bot.schemas import ChatRequest
from fitness_bot.storage import (
    Base,
    ChatTurn,
    ChatTurnMedia,
    Conversation,
    ConversationStore,
    utcnow,
)
from fitness_bot.workout_schemas import WorkoutUpload
from fitness_bot.workout_storage import WorkoutRow, WorkoutStore


async def seed_legacy(store):
    tables = [
        Conversation.__table__,
        ChatTurn.__table__,
        ChatTurnMedia.__table__,
        ConversationOwner.__table__,
        ImageOwner.__table__,
        IdentityMigration.__table__,
        WorkoutRow.__table__,
        LegacyUser.__table__,
        LegacyUserToken.__table__,
    ]
    async with store.engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))
        for old_id in ["Alice", "alice"]:
            await conn.execute(
                LegacyUser.__table__.insert(), {"id": old_id, "created_at": utcnow()}
            )
        for token_id, token, expiry, revoked in [
            ("valid", "legacy-token-secret", None, None),
            ("expired", "old-expired", utcnow() - timedelta(days=1), None),
            ("revoked", "old-revoked", None, utcnow()),
        ]:
            await conn.execute(
                LegacyUserToken.__table__.insert(),
                {
                    "id": token_id,
                    "user_id": "Alice",
                    "token_hash": hashlib.sha256(token.encode()).hexdigest(),
                    "created_at": utcnow(),
                    "expires_at": expiry,
                    "revoked_at": revoked,
                },
            )
    request = ChatRequest(message="历史图片问答", images=["a" * 64])
    # Freeze the old wire shape instead of inheriting new server-owned fields
    # whenever the current ChatRequest schema grows.
    legacy_input = {
        "message": request.message,
        "images": request.images,
        "request_id": str(request.request_id),
        "conversation_id": str(request.conversation_id),
        "context": {"active_workout": None, "recent_exercises": [], "available_equipment": []},
    }
    digest = hashlib.sha256(
        json.dumps(
            legacy_input,
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    payload = WorkoutUpload.model_validate(
        json.loads((Path(__file__).parent / "fixtures/workout_upload.json").read_text())
    )
    response = {"text": "完整历史回答", "cards": []}
    async with store.sessions.begin() as session:
        session.add(IdentityMigration(name="multiuser-v1", default_user_id="Alice"))
        session.add(Conversation(id=str(request.conversation_id), title="历史图片问答"))
        await session.flush()
        session.add(
            ConversationOwner(conversation_id=str(request.conversation_id), user_id="Alice")
        )
        session.add(
            ChatTurn(
                request_id=str(request.request_id),
                conversation_id=str(request.conversation_id),
                input_hash=digest,
                user_text=request.message,
                context={},
                assistant_text=response["text"],
                response=response,
                status="completed",
            )
        )
        await session.flush()
        session.add(ChatTurnMedia(request_id=str(request.request_id), image_ids=request.images))
        for old_id in ["Alice", "alice"]:
            session.add(ImageOwner(user_id=old_id, image_id="a" * 64))
            session.add(
                WorkoutRow(
                    user_id=old_id,
                    source_id="legacy-install",
                    workout_id=payload.workout.id,
                    version=9,
                    deleted=False,
                    workout=payload.workout.model_dump(mode="json"),
                    uploaded_at="2026-01-01T00:00:00.123456Z",
                )
            )
        session.add(
            WorkoutRow(
                user_id="Alice",
                source_id="unique-install",
                workout_id="deleted",
                version=10,
                deleted=True,
                workout=None,
                uploaded_at="2026-01-01T00:00:00.123456Z",
            )
        )
    return request, payload.workout.model_dump(mode="json"), response


async def test_v2_migration_preserves_tokens_turns_media_and_complete_workouts(tmp_path):
    store = ConversationStore(f"sqlite+aiosqlite:///{tmp_path / 'v1.db'}")
    request, content, response = await seed_legacy(store)
    await store.initialize("Alice")
    identity = IdentityStore(store)
    user = await identity.find_user("Alice")
    other = await identity.find_user("alice")
    assert user["userId"] != "Alice" and len(user["userId"]) == 32
    assert user["userId"] != other["userId"]
    assert user["displayName"] == "Alice"
    assert await identity.authenticate("legacy-token-secret") == user["userId"]
    assert await identity.authenticate("old-expired") is None
    assert await identity.authenticate("old-revoked") is None
    turns = await store.turns(request.conversation_id, user_id=user["userId"])
    assert turns[0]["images"] == request.images and turns[0]["response"] == response
    assert (await store.begin_turn(request, user_id=user["userId"])).cached_response == response
    for principal in [user, other]:
        snapshot = await WorkoutStore(store).read(
            "legacy-install", content["id"], user_id=principal["userId"]
        )
        assert snapshot["workout"] == content and snapshot["version"] == 9
        assert snapshot["uploadedAt"] == "2026-01-01T00:00:00.123456Z"
        assert await identity.owns_image(principal["userId"], "a" * 64)
    assert (await identity.bootstrap("unique-install"))["userId"] == user["userId"]
    from fitness_bot.identity import InstallationConflict

    with pytest.raises(InstallationConflict):
        await identity.bootstrap("legacy-install")
    await store.initialize("different-default")
    assert await identity.find_user("Alice") == user
    assert await identity.find_user("different-default") is None
    assert await identity.revoke_token("valid")
    assert await identity.authenticate("legacy-token-secret") is None
    await store.initialize("Alice")
    assert await identity.authenticate("legacy-token-secret") is None
    async with store.sessions() as session:
        assert (await session.execute(text("SELECT count(*) FROM users"))).scalar() == 2
        assert (await session.execute(text("SELECT count(*) FROM user_tokens"))).scalar() == 3
        assert set(await session.scalars(select(WorkoutRow.user_id))) == {
            user["userId"],
            other["userId"],
        }
    await store.close()


async def test_failed_v2_copy_rolls_back_mapping_and_can_retry(tmp_path):
    from fitness_bot.identity import LegacyUserAlias, User

    store = ConversationStore(f"sqlite+aiosqlite:///{tmp_path / 'failed.db'}")
    await seed_legacy(store)

    def fail_alias_flush(session, context, instances):
        if any(isinstance(row, LegacyUserAlias) for row in session.new):
            raise SQLAlchemyError("synthetic migration failure")

    event.listen(Session, "before_flush", fail_alias_flush)
    try:
        with pytest.raises(SQLAlchemyError):
            await store.initialize("Alice")
    finally:
        event.remove(Session, "before_flush", fail_alias_flush)
    async with store.sessions() as session:
        assert await session.get(IdentityMigration, "internal-user-id-v2") is None
        assert (await session.scalars(select(User))).all() == []
        assert set(await session.scalars(select(WorkoutRow.user_id))) == {"Alice", "alice"}
    await store.initialize("Alice")
    assert (await IdentityStore(store).find_user("Alice"))["userId"] != "Alice"
    await store.close()
