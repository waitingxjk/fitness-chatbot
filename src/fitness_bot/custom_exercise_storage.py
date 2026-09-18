"""Owner-scoped catalog extensions. Never modifies standard data or workout records."""

import hashlib
import unicodedata
from uuid import uuid4

from sqlalchemy import JSON, Integer, String, UniqueConstraint, select
from sqlalchemy.orm import Mapped, mapped_column

from fitness_bot.catalog import Catalog
from fitness_bot.custom_exercise_schemas import CustomExerciseSpec
from fitness_bot.identity import USER_ID, User
from fitness_bot.schemas import Exercise
from fitness_bot.storage import Base, ObjectNotFound, TurnConflict, utcnow


class CustomExerciseRow(Base):
    __tablename__ = "custom_exercises"
    __table_args__ = (UniqueConstraint("user_id", "name_key"),)
    user_id: Mapped[str] = mapped_column(USER_ID, primary_key=True)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name_key: Mapped[str] = mapped_column(String(64))
    definition: Mapped[dict] = mapped_column(JSON)
    exercise: Mapped[dict] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(Integer)


def normalized_name(value):
    return "".join(unicodedata.normalize("NFKC", value).casefold().split())


def name_key(name):
    return hashlib.sha256(normalized_name(name).encode()).hexdigest()


async def personal_catalog(session, user_id, base=None):
    base = base or Catalog.load()
    rows = await session.scalars(
        select(CustomExerciseRow).where(CustomExerciseRow.user_id == user_id)
    )
    return base.with_personal([Exercise.model_validate(r.exercise) for r in rows])


def validate_definition(spec, catalog):
    for kind, values in (
        ("bodyPart", [spec.body_part_code]),
        ("equipment", [spec.equipment_code]),
        ("muscle", [spec.target_muscle_code, *spec.secondary_muscle_codes]),
    ):
        allowed = {t["code"] for t in catalog.terms if t["type"] == kind} | {"unknown"}
        if any(v not in allowed for v in values):
            raise ValueError(f"{kind} 必须使用动作库术语；不确定时填写 unknown。")


def exact_matches(catalog, name, exclude=None):
    needle = normalized_name(name)
    return [
        e
        for e in catalog.exercises
        if e.id != exclude
        and needle
        in {normalized_name(v) for v in [e.display_name_zh_hans, e.source_name, *e.aliases_zh_hans]}
    ]


def make_exercise(spec, exercise_id, *, created_at=None, active=True):
    now = utcnow().isoformat() + "Z"
    return Exercise(
        id=exercise_id,
        source_name=spec.name,
        display_name_zh_hans=spec.name,
        aliases_zh_hans=spec.aliases,
        body_part_code=spec.body_part_code,
        equipment_code=spec.equipment_code,
        target_muscle_code=spec.target_muscle_code,
        secondary_muscle_codes=spec.secondary_muscle_codes,
        instruction_steps_zh_hans=spec.instructions,
        media_id="",
        detail_type=spec.detail_type,
        default_weight_unit=(spec.default_weight_unit or "kg")
        if spec.detail_type == "weightedReps"
        else None,
        is_active=active,
        catalog_version="personal-v1",
        created_at=created_at or now,
        updated_at=now,
        source_kind="custom",
        training_type=spec.training_type,
        avatar={
            "kind": "builtin",
            "key": spec.training_type,
            "url": f"/v1/exercises/{exercise_id}/thumbnail",
        },
    )


def receipt(row, status):
    return {
        "status": status,
        "exercise": row.exercise,
        "definition": row.definition,
        "version": row.version,
    }


async def save_custom(session, user_id, spec, base=None):
    # Serialize a user's name/alias checks and writes across conversations on MySQL.
    await session.scalar(select(User).where(User.id == user_id).with_for_update())
    catalog = await personal_catalog(session, user_id, base)
    validate_definition(spec, catalog)
    matches = exact_matches(catalog, spec.name)
    if len(matches) > 1:
        raise ValueError("有多个同名动作，请补充名称以区分。")
    if matches:
        exercise = matches[0]
        if not exercise.is_active:
            raise ValueError("同名动作已归档，请在个人动作库恢复后使用。")
        row = await session.get(CustomExerciseRow, (user_id, exercise.id))
        return (
            receipt(row, "existing")
            if row
            else {
                "status": "existing",
                "exercise": exercise.model_dump(mode="json", by_alias=True),
            }
        )
    for alias in spec.aliases:
        if exact_matches(catalog, alias):
            raise ValueError("别名已属于其他动作，请明确动作名称或移除重复别名。")
    exercise_id = "cx_" + uuid4().hex[:8]
    exercise = make_exercise(spec, exercise_id)
    row = CustomExerciseRow(
        user_id=user_id,
        id=exercise_id,
        name_key=name_key(spec.name),
        definition=spec.model_dump(mode="json"),
        exercise=exercise.model_dump(mode="json", by_alias=True),
        version=1,
    )
    session.add(row)
    await session.flush()
    return receipt(row, "created")


def draft_metadata(spec, catalog):
    return {
        "definition": spec.model_dump(mode="json"),
        "avatar": {
            "kind": "builtin",
            "key": spec.training_type,
            "url": f"/v1/custom-exercises/avatars/{spec.training_type}",
        },
        "labels": {
            "body_part": catalog.label("bodyPart", spec.body_part_code),
            "equipment": catalog.label("equipment", spec.equipment_code),
            "target_muscle": catalog.label("muscle", spec.target_muscle_code),
            "secondary_muscles": [
                catalog.label("muscle", code) for code in spec.secondary_muscle_codes
            ],
        },
    }


class CustomExerciseStore:
    def __init__(self, store, catalog):
        self.store, self.catalog = store, catalog

    async def _locked_draft(self, session, user_id, draft_id):
        from fitness_bot.identity import ConversationOwner
        from fitness_bot.storage import ChatTurn

        turn = await session.scalar(
            select(ChatTurn).where(ChatTurn.request_id == str(draft_id)).with_for_update()
        )
        owner = await session.get(ConversationOwner, turn.conversation_id) if turn else None
        if owner is None or owner.user_id != user_id:
            raise ObjectNotFound("Exercise draft not found")
        draft = (turn.response or {}).get("custom_exercise") or {}
        if turn.status != "completed" or draft.get("status") != "draft":
            raise TurnConflict("此消息没有可保存的动作草稿。")
        return turn, draft

    @staticmethod
    def _check_draft_version(draft, version):
        if draft.get("version", 1) != version:
            raise TurnConflict("草稿已修改，请刷新会话后检查最新内容再保存。")

    async def update_draft(self, user_id, draft_id, version, spec):
        async with self.store.sessions.begin() as session:
            turn, draft = await self._locked_draft(session, user_id, draft_id)
            self._check_draft_version(draft, version)
            if draft.get("saved_result"):
                raise TurnConflict("草稿已保存，请在个人动作详情中修改。")
            validate_definition(spec, self.catalog)
            # Confirm only changed fields; unchanged expert inferences stay marked.
            inferred = [
                field
                for field in draft["definition"].get("inferred_fields", [])
                if draft["definition"].get(field) == spec.model_dump().get(field)
            ]
            spec = spec.model_copy(update={"inferred_fields": inferred})
            result = {**draft, **draft_metadata(spec, self.catalog), "version": version + 1}
            turn.response = {**turn.response, "custom_exercise": result}
            turn.updated_at = utcnow()
            return result

    async def save_draft(self, user_id, draft_id, version=1):
        async with self.store.sessions.begin() as session:
            turn, draft = await self._locked_draft(session, user_id, draft_id)
            self._check_draft_version(draft, version)
            if draft.get("saved_result"):
                return draft["saved_result"]
            spec = CustomExerciseSpec.model_validate(draft["definition"])
            result = await save_custom(session, user_id, spec, self.catalog)
            turn.response = {**turn.response, "custom_exercise": {**draft, "saved_result": result}}
            turn.updated_at = utcnow()
            return result

    async def catalog_for(self, user_id):
        async with self.store.sessions() as session:
            return await personal_catalog(session, user_id, self.catalog)

    async def list(self, user_id, include_archived=False):
        async with self.store.sessions() as session:
            rows = await session.scalars(
                select(CustomExerciseRow)
                .where(CustomExerciseRow.user_id == user_id)
                .order_by(CustomExerciseRow.id)
            )
            return [
                receipt(r, "existing") for r in rows if include_archived or r.exercise["isActive"]
            ]

    async def create(self, user_id, spec):
        async with self.store.sessions.begin() as session:
            return await save_custom(session, user_id, spec, self.catalog)

    async def update(self, user_id, exercise_id, version, spec=None, *, archive=False):
        async with self.store.sessions.begin() as session:
            await session.scalar(select(User).where(User.id == user_id).with_for_update())
            row = await session.get(CustomExerciseRow, (user_id, exercise_id))
            if row is None:
                raise ObjectNotFound("Custom exercise not found")
            if row.version != version:
                raise TurnConflict("动作已修改，请刷新后重试。")
            if archive:
                row.exercise = {
                    **row.exercise,
                    "isActive": False,
                    "updatedAt": utcnow().isoformat() + "Z",
                }
            else:
                catalog = await personal_catalog(session, user_id, self.catalog)
                validate_definition(spec, catalog)
                if spec.detail_type != row.exercise["detailType"]:
                    raise ValueError("不能改变已有动作的记录方式，请另建动作。")
                if any(
                    exact_matches(catalog, n, exclude=exercise_id)
                    for n in [spec.name, *spec.aliases]
                ):
                    raise TurnConflict("名称或别名已属于其他动作。")
                row.exercise = make_exercise(
                    spec, exercise_id, created_at=row.exercise["createdAt"]
                ).model_dump(mode="json", by_alias=True)
                row.definition, row.name_key = spec.model_dump(mode="json"), name_key(spec.name)
            row.version += 1
            return receipt(row, "archived" if archive else "updated")
