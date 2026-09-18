"""Authenticated personal exercise management; no public catalog writes."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy.exc import IntegrityError

from fitness_bot.custom_exercise_schemas import (
    CreateCustomExercise,
    SaveCustomExerciseDraft,
    UpdateCustomExercise,
)
from fitness_bot.storage import ObjectNotFound, TurnConflict

router = APIRouter(prefix="/v1/custom-exercises")


async def checked(operation):
    try:
        return await operation
    except ObjectNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    except TurnConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except IntegrityError:
        raise HTTPException(409, "动作已存在或同时发生修改，请刷新后重试。") from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.get("")
async def list_custom(request: Request, include_archived: bool = False):
    return await request.app.state.custom_exercises.list(request.state.user_id, include_archived)


@router.get("/options")
async def custom_options(request: Request):
    return {
        "terms": request.app.state.catalog.terms,
        "training_types": ["strength", "cardio", "mobility", "stretching", "other"],
    }


@router.get("/avatars/{key}")
async def draft_avatar(key: str):
    from fastapi.responses import Response

    from fitness_bot.custom_exercise_avatars import AVATAR_PATHS, avatar_svg

    if key not in AVATAR_PATHS:
        raise HTTPException(404, "Avatar not found")
    return Response(
        avatar_svg(key),
        media_type="image/svg+xml",
        headers={
            "Cache-Control": "private, max-age=86400",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )


@router.put("/drafts/{draft_id}")
async def update_draft(request: Request, draft_id: UUID, payload: UpdateCustomExercise):
    return await checked(
        request.app.state.custom_exercises.update_draft(
            request.state.user_id, draft_id, payload.version, payload.exercise
        )
    )


@router.post("/drafts/{draft_id}/save")
async def save_draft(
    request: Request, draft_id: UUID, payload: SaveCustomExerciseDraft | None = None
):
    return await checked(
        request.app.state.custom_exercises.save_draft(
            request.state.user_id, draft_id, payload.version if payload else 1
        )
    )


@router.get("/{exercise_id}")
async def custom_detail(request: Request, exercise_id: str):
    from fitness_bot.custom_exercise_storage import CustomExerciseRow, receipt

    async with request.app.state.store.sessions() as session:
        row = await session.get(CustomExerciseRow, (request.state.user_id, exercise_id))
        if row is None:
            raise HTTPException(404, "Custom exercise not found")
        return receipt(row, "existing")


@router.post("")
async def create_custom(request: Request, payload: CreateCustomExercise):
    return await checked(
        request.app.state.custom_exercises.create(request.state.user_id, payload.exercise)
    )


@router.put("/{exercise_id}")
async def update_custom(request: Request, exercise_id: str, payload: UpdateCustomExercise):
    return await checked(
        request.app.state.custom_exercises.update(
            request.state.user_id, exercise_id, payload.version, payload.exercise
        )
    )


@router.delete("/{exercise_id}")
async def archive_custom(request: Request, exercise_id: str, version: int = Query(ge=1)):
    return await checked(
        request.app.state.custom_exercises.update(
            request.state.user_id, exercise_id, version, archive=True
        )
    )
