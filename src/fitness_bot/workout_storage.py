"""Versioned aggregate replacement on the existing database/session infrastructure."""

import asyncio
import json
from datetime import UTC, datetime

from sqlalchemy import JSON, BigInteger, Boolean, String, select, update
from sqlalchemy.dialects.mysql import VARCHAR
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Mapped, mapped_column

from fitness_bot.identity import USER_ID
from fitness_bot.storage import Base, ConversationStore
from fitness_bot.workout_schemas import WorkoutUpload

# IDs remain case-sensitive on MySQL as they are on iOS and SQLite.
ID_COLUMN = String(191).with_variant(VARCHAR(191, collation="utf8mb4_bin"), "mysql")


class WorkoutRow(Base):
    __tablename__ = "user_workout_snapshots"

    user_id: Mapped[str] = mapped_column(USER_ID, primary_key=True)

    source_id: Mapped[str] = mapped_column(ID_COLUMN, primary_key=True)
    workout_id: Mapped[str] = mapped_column(ID_COLUMN, primary_key=True)
    version: Mapped[int] = mapped_column(BigInteger)
    deleted: Mapped[bool] = mapped_column(Boolean)
    workout: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Preserve microseconds across MySQL and SQLite, including idempotent acknowledgements.
    uploaded_at: Mapped[str] = mapped_column(String(32))


class WorkoutConflict(Exception):
    """A stale upload or different content for an already accepted version."""


def receipt(row):
    return {
        "userId": row.user_id,
        "sourceId": row.source_id,
        "workoutId": row.workout_id,
        "version": row.version,
        "uploadedAt": row.uploaded_at,
    }


def snapshot(row):
    return {**receipt(row), "deleted": row.deleted, "workout": row.workout}


class WorkoutStore:
    def __init__(self, store: ConversationStore):
        self.sessions = store.sessions
        self.store = store

    async def upload(self, workout_id: str, payload: WorkoutUpload, *, user_id=None):
        user_id = await self.store.resolve_user(user_id)
        if payload.workout is not None and payload.workout.id != workout_id:
            raise ValueError("Path workout_id must match workout.id")
        for attempt in range(5):
            try:
                return await self._upload(workout_id, payload, user_id)
            except IntegrityError:
                # Concurrent first insert: the next transaction sees the winning row.
                if attempt == 4:
                    raise
            except OperationalError as exc:
                # MySQL can deadlock on concurrent absent-key/gap locks. SQLite may
                # report a busy writer. Retry the complete transaction, never just SQL.
                code = exc.orig.args[0] if exc.orig.args else None
                retryable = code in (1205, 1213) or "database is locked" in str(exc.orig)
                if not retryable or attempt == 4:
                    raise
            await asyncio.sleep(0.01 * (2**attempt))
        raise AssertionError("Unreachable")

    async def _upload(self, workout_id, payload, user_id):
        content = payload.workout.model_dump(mode="json") if payload.workout else None
        now = datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
        key = (
            WorkoutRow.user_id == user_id,
            WorkoutRow.source_id == payload.sourceId,
            WorkoutRow.workout_id == workout_id,
        )
        async with self.sessions.begin() as session:
            # The version predicate and write are one atomic SQL operation. Starting
            # with the write also serializes SQLite, where FOR UPDATE is unsupported.
            changed = await session.execute(
                update(WorkoutRow)
                .where(*key, WorkoutRow.version < payload.version)
                .values(
                    version=payload.version,
                    deleted=payload.deleted,
                    workout=content,
                    uploaded_at=now,
                )
            )
            row = await session.scalar(select(WorkoutRow).where(*key).with_for_update())
            if row is None:
                row = WorkoutRow(
                    user_id=user_id,
                    source_id=payload.sourceId,
                    workout_id=workout_id,
                    version=payload.version,
                    deleted=payload.deleted,
                    workout=content,
                    uploaded_at=now,
                )
                session.add(row)
                await session.flush()
            elif not changed.rowcount and (
                row.version != payload.version
                or row.deleted != payload.deleted
                or json.dumps(row.workout, sort_keys=True) != json.dumps(content, sort_keys=True)
            ):
                raise WorkoutConflict("Stale version or different content for the same version")
            result = receipt(row)
        # The transaction context has committed before any receipt escapes.
        return result

    async def read(self, source_id: str, workout_id: str, *, user_id=None):
        user_id = await self.store.resolve_user(user_id)
        async with self.sessions() as session:
            row = await session.get(WorkoutRow, (user_id, source_id, workout_id))
            return snapshot(row) if row else None

    async def list(self, source_id: str, *, limit=50, offset=0, user_id=None):
        user_id = await self.store.resolve_user(user_id)
        async with self.sessions() as session:
            rows = await session.scalars(
                select(WorkoutRow)
                .where(WorkoutRow.user_id == user_id, WorkoutRow.source_id == source_id)
                .order_by(WorkoutRow.workout_id)
                .limit(limit)
                .offset(offset)
            )
            return [snapshot(row) for row in rows]
