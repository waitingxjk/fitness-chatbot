"""Server-generated users, installation binding, opaque tokens and legacy migrations."""

import asyncio
import hashlib
import json
import re
import secrets
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import DateTime, ForeignKey, String, inspect, select, text, update
from sqlalchemy.dialects.mysql import VARCHAR
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from sqlalchemy.orm import Mapped, mapped_column

from fitness_bot.storage import Base, Conversation, utcnow
from fitness_bot.workout_schemas import Identifier

USER_ID = String(64).with_variant(VARCHAR(64, collation="utf8mb4_bin"), "mysql")
INTERNAL_ID = String(32).with_variant(VARCHAR(32, collation="utf8mb4_bin"), "mysql")
INSTALLATION_ID = String(191).with_variant(VARCHAR(191, collation="utf8mb4_bin"), "mysql")


def valid_user_id(value: str) -> str:
    """Accept internal IDs and known legacy lookup aliases; never creates a user."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", value):
        raise ValueError("Invalid user ID or legacy alias")
    return value


class BootstrapRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    installationId: Identifier
    displayName: str | None = Field(default=None, max_length=120)

    @field_validator("displayName")
    @classmethod
    def normalize_name(cls, value):
        return value.strip() or None if value is not None else None


class LegacyUser(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(USER_ID, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class LegacyUserToken(Base):
    __tablename__ = "user_tokens"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class User(Base):
    __tablename__ = "user"
    id: Mapped[str] = mapped_column(INTERNAL_ID, primary_key=True)
    display_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class UserToken(Base):
    __tablename__ = "user_access_tokens"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("user.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class LegacyUserAlias(Base):
    __tablename__ = "legacy_user_aliases"
    alias: Mapped[str] = mapped_column(USER_ID, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("user.id"), index=True)


class UserInstallation(Base):
    __tablename__ = "user_installations"
    installation_id: Mapped[str] = mapped_column(INSTALLATION_ID, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("user.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ConversationOwner(Base):
    __tablename__ = "conversation_owners"
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), primary_key=True)
    user_id: Mapped[str] = mapped_column(USER_ID, index=True)


class ImageOwner(Base):
    __tablename__ = "image_owners"
    user_id: Mapped[str] = mapped_column(USER_ID, primary_key=True)
    image_id: Mapped[str] = mapped_column(String(64), primary_key=True)


class IdentityMigration(Base):
    __tablename__ = "identity_migrations"
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    default_user_id: Mapped[str] = mapped_column(USER_ID)


class InstallationConflict(Exception):
    """An installation cannot safely be assigned to the requested principal."""


def user_info(user):
    return {"userId": user.id, "displayName": user.display_name}


def retryable(exc):
    if isinstance(exc, IntegrityError):
        return True
    code = exc.orig.args[0] if exc.orig.args else None
    return code in (1205, 1213) or "database is locked" in str(exc.orig)


class IdentityStore:
    def __init__(self, store):
        self.sessions = store.sessions
        self.engine = store.engine

    async def find_user(self, identifier):
        async with self.sessions() as session:
            user = await session.get(User, identifier)
            if user is None:
                alias = await session.get(LegacyUserAlias, identifier)
                user = await session.get(User, alias.user_id) if alias else None
            return user_info(user) if user else None

    async def require_user_id(self, identifier):
        user = await self.find_user(identifier)
        if user is None:
            raise ValueError("User not found")
        return user["userId"]

    async def create_user(self, display_name=None):
        name = BootstrapRequest(installationId="admin", displayName=display_name).displayName
        async with self.sessions.begin() as session:
            user = User(id=uuid4().hex, display_name=name)
            session.add(user)
            await session.flush()
            result = user_info(user)
        return result

    async def migrate(self, default_user_id, image_directory=None):
        # Each version's marker and data changes commit together. DDL is additive.
        for name, migration in [
            ("multiuser-v1", lambda: self._migrate_v1(default_user_id, image_directory)),
            ("internal-user-id-v2", lambda: self._migrate_v2(default_user_id)),
        ]:
            for attempt in range(5):
                try:
                    await migration()
                    break
                except (IntegrityError, OperationalError) as exc:
                    if not retryable(exc):
                        raise
                    async with self.sessions() as session:
                        if await session.get(IdentityMigration, name):
                            break
                    if attempt == 4:
                        raise
                    await asyncio.sleep(0.01 * 2**attempt)

    async def _migrate_v1(self, default_user_id, image_directory):
        from fitness_bot.workout_storage import WorkoutRow

        async with self.sessions.begin() as session:
            if await session.get(IdentityMigration, "multiuser-v1"):
                return
            session.add(IdentityMigration(name="multiuser-v1", default_user_id=default_user_id))
            if await session.get(LegacyUser, default_user_id) is None:
                session.add(LegacyUser(id=default_user_id))
            await session.flush()
            owned = select(ConversationOwner.conversation_id)
            for cid in await session.scalars(
                select(Conversation.id).where(Conversation.id.not_in(owned))
            ):
                session.add(ConversationOwner(conversation_id=cid, user_id=default_user_id))
            connection = await session.connection()
            tables = await connection.run_sync(lambda c: inspect(c).get_table_names())
            if "workout_snapshots" in tables:
                rows = (await session.execute(text("SELECT * FROM workout_snapshots"))).mappings()
                for row in rows:
                    workout = row["workout"]
                    if isinstance(workout, str):
                        workout = json.loads(workout)
                    session.add(
                        WorkoutRow(
                            user_id=default_user_id,
                            source_id=row["source_id"],
                            workout_id=row["workout_id"],
                            version=row["version"],
                            deleted=bool(row["deleted"]),
                            workout=workout,
                            uploaded_at=row["uploaded_at"],
                        )
                    )
            if image_directory:
                for path in Path(image_directory).glob("*"):
                    if path.is_file() and re.fullmatch(r"[0-9a-f]{64}", path.name):
                        session.add(ImageOwner(user_id=default_user_id, image_id=path.name))

    async def _migrate_v2(self, default_user_id):
        from fitness_bot.workout_storage import WorkoutRow

        async with self.sessions.begin() as session:
            if await session.get(IdentityMigration, "internal-user-id-v2"):
                return
            marker = IdentityMigration(name="internal-user-id-v2", default_user_id=default_user_id)
            session.add(marker)
            await session.flush()
            legacy_users = {row.id: row for row in await session.scalars(select(LegacyUser))}
            old_ids = set(legacy_users)
            for table in [ConversationOwner, ImageOwner, WorkoutRow, LegacyUserToken]:
                old_ids.update(await session.scalars(select(table.user_id).distinct()))
            mapping = {}
            allocated = old_ids | set(await session.scalars(select(User.id)))
            for old_id in sorted(old_ids):
                new_id = uuid4().hex
                while new_id in allocated:
                    new_id = uuid4().hex
                allocated.add(new_id)
                mapping[old_id] = new_id
                created_at = legacy_users[old_id].created_at if old_id in legacy_users else utcnow()
                session.add(
                    User(
                        id=new_id, display_name=old_id, created_at=created_at, updated_at=created_at
                    )
                )
            await session.flush()
            for old_id, new_id in mapping.items():
                session.add(LegacyUserAlias(alias=old_id, user_id=new_id))
            await session.flush()
            for row in await session.scalars(select(LegacyUserToken)):
                session.add(
                    UserToken(
                        id=row.id,
                        user_id=mapping[row.user_id],
                        token_hash=row.token_hash,
                        created_at=row.created_at,
                        expires_at=row.expires_at,
                        revoked_at=row.revoked_at,
                    )
                )
            for table in [ConversationOwner, ImageOwner, WorkoutRow]:
                for old_id, new_id in mapping.items():
                    await session.execute(
                        update(table).where(table.user_id == old_id).values(user_id=new_id)
                    )
            previous = await session.get(IdentityMigration, "multiuser-v1")
            marker.default_user_id = mapping.get(previous.default_user_id, default_user_id)

    async def bootstrap(self, installation_id, display_name=None, principal=None):
        payload = BootstrapRequest(installationId=installation_id, displayName=display_name)
        for attempt in range(5):
            try:
                return await self._bootstrap(payload, principal)
            except (IntegrityError, OperationalError) as exc:
                if not retryable(exc) or attempt == 4:
                    raise
                await asyncio.sleep(0.01 * 2**attempt)
        raise AssertionError("Unreachable")

    async def _bootstrap(self, payload, principal):
        from fitness_bot.workout_storage import WorkoutRow

        async with self.sessions.begin() as session:
            binding = await session.get(UserInstallation, payload.installationId)
            if binding:
                if principal is not None and binding.user_id != principal:
                    raise InstallationConflict("Installation belongs to another user")
                return user_info(await session.get(User, binding.user_id))
            owners = set(
                await session.scalars(
                    select(WorkoutRow.user_id)
                    .where(WorkoutRow.source_id == payload.installationId)
                    .distinct()
                )
            )
            if len(owners) > 1:
                raise InstallationConflict("Installation has ambiguous historical ownership")
            existing_owner = next(iter(owners), None)
            if principal is not None:
                if existing_owner is not None and existing_owner != principal:
                    raise InstallationConflict("Installation belongs to another user")
                user = await session.get(User, principal)
                if user is None:
                    raise ValueError("User not found")
            elif existing_owner:
                legacy_alias = await session.scalar(
                    select(LegacyUserAlias).where(LegacyUserAlias.user_id == existing_owner)
                )
                if legacy_alias is None:
                    raise InstallationConflict("Installation already has an unbound owner")
                user = await session.get(User, existing_owner)
            else:
                user = User(id=uuid4().hex, display_name=payload.displayName)
                session.add(user)
                await session.flush()
            session.add(UserInstallation(installation_id=payload.installationId, user_id=user.id))
            result = user_info(user)
        return result

    async def issue_token(self, user_id, expires_at=None):
        user_id = await self.require_user_id(user_id)
        token, token_id = secrets.token_urlsafe(32), str(uuid4())
        async with self.sessions.begin() as session:
            session.add(
                UserToken(
                    id=token_id,
                    user_id=user_id,
                    token_hash=hashlib.sha256(token.encode()).hexdigest(),
                    expires_at=expires_at,
                )
            )
        return {"userId": user_id, "tokenId": token_id, "token": token, "expiresAt": expires_at}

    async def revoke_token(self, token_id):
        async with self.sessions.begin() as session:
            row = await session.get(UserToken, token_id)
            if row is None:
                return False
            row.revoked_at = utcnow()
            return True

    async def authenticate(self, token):
        digest = hashlib.sha256(token.encode()).hexdigest()
        async with self.sessions() as session:
            row = await session.scalar(select(UserToken).where(UserToken.token_hash == digest))
            if (
                row is None
                or row.revoked_at is not None
                or (row.expires_at is not None and row.expires_at <= utcnow())
            ):
                return None
            return row.user_id

    async def own_image(self, user_id, image_id):
        user_id = await self.require_user_id(user_id)
        try:
            async with self.sessions.begin() as session:
                if await session.get(ImageOwner, (user_id, image_id)) is None:
                    session.add(ImageOwner(user_id=user_id, image_id=image_id))
        except IntegrityError:
            if not await self.owns_image(user_id, image_id):
                raise

    async def owns_image(self, user_id, image_id):
        user = await self.find_user(user_id)
        if user is None:
            return False
        async with self.sessions() as session:
            return await session.get(ImageOwner, (user["userId"], image_id)) is not None


async def resolve_identity(request: Request):
    if not request.url.path.startswith("/v1/"):
        return
    settings, identity = request.app.state.settings, request.app.state.identity
    claimed = request.headers.get("x-user-id")
    try:
        if settings.auth_enabled:
            scheme, _, token = request.headers.get("authorization", "").partition(" ")
            user_id = (
                await identity.authenticate(token) if scheme.lower() == "bearer" and token else None
            )
            if user_id is None:
                raise HTTPException(
                    401, "Valid Bearer token required", headers={"WWW-Authenticate": "Bearer"}
                )
            if claimed is not None:
                claimed_user = await identity.find_user(claimed)
                if claimed_user is None or claimed_user["userId"] != user_id:
                    raise HTTPException(403, "X-User-Id does not match token principal")
        elif request.url.path == "/v1/users/bootstrap":
            # The installation binding, rather than a user-supplied ID, decides identity.
            request.state.user_id = None
            return
        else:
            try:
                identifier = valid_user_id(
                    claimed if claimed is not None else settings.default_user_id
                )
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None
            user = await identity.find_user(identifier)
            if user is None:
                raise HTTPException(404, "User not found; bootstrap an installation first")
            user_id = user["userId"]
    except SQLAlchemyError:
        raise HTTPException(503, "Identity storage unavailable") from None
    request.state.user_id = user_id
