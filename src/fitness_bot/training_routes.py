"""Read-only review facts and explicit training preferences/lifecycle endpoints."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import select

from fitness_bot.plan_revisions import PlanRevisionInput, save_revision
from fitness_bot.storage import ObjectNotFound, TurnConflict
from fitness_bot.training_review import load_training_review
from fitness_bot.training_schemas import (
    ObservationAttestation,
    PlanEventInput,
    ProfileUpdate,
    ReviewOptions,
)
from fitness_bot.workout_storage import WorkoutRow

router = APIRouter(prefix="/v1/training")


@router.put("/observations/{observation_id}/attestation")
async def attest_observation(
    request: Request, observation_id: str, payload: ObservationAttestation
):
    from fitness_bot.training_evidence import save_attestation

    try:
        return await save_attestation(
            request.app.state.store.sessions,
            request.state.user_id,
            observation_id,
            payload,
            await request.app.state.custom_exercises.catalog_for(request.state.user_id),
        )
    except ObjectNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    except TurnConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.get("/options")
async def training_options(request: Request):
    catalog = await request.app.state.custom_exercises.catalog_for(request.state.user_id)
    codes = sorted({e.equipment_code for e in catalog.exercises if e.is_active})
    return {
        "equipment": [{"code": code, "label": catalog.label("equipment", code)} for code in codes]
    }


@router.get("/completed-workouts")
async def completed_workouts(request: Request):
    async with request.app.state.store.sessions() as session:
        rows = await session.scalars(
            select(WorkoutRow)
            .where(
                WorkoutRow.user_id == request.state.user_id,
                WorkoutRow.deleted.is_(False),
                WorkoutRow.workout["status"].as_string() == "completed",
            )
            .order_by(WorkoutRow.workout["startedAt"].as_string().desc())
            .limit(50)
        )
        return [
            {
                "source_id": row.source_id,
                "workout_id": row.workout_id,
                "title": row.workout["title"],
                "started_at": row.workout["startedAt"],
            }
            for row in rows
        ]


@router.get("/plans")
async def training_plans(request: Request):
    return await request.app.state.training.plans(request.state.user_id)


@router.get("/plans/{plan_id}")
async def training_plan(request: Request, plan_id: UUID):
    try:
        return await request.app.state.training.plan(request.state.user_id, plan_id)
    except ObjectNotFound as exc:
        raise HTTPException(404, str(exc)) from None


@router.post("/plans/{plan_id}/revision")
async def revise_plan(request: Request, plan_id: UUID, payload: PlanRevisionInput):
    try:
        return await save_revision(
            request.app.state.training,
            request.state.user_id,
            plan_id,
            payload,
            await request.app.state.custom_exercises.catalog_for(request.state.user_id),
        )
    except ObjectNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    except TurnConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.post("/plans/{plan_id}/events")
async def plan_event(request: Request, plan_id: UUID, payload: PlanEventInput):
    try:
        return await request.app.state.training.event(request.state.user_id, plan_id, payload)
    except ObjectNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    except TurnConflict as exc:
        raise HTTPException(409, str(exc)) from None


@router.get("/profile")
async def training_profile(request: Request):
    return await request.app.state.training.profile(request.state.user_id)


@router.put("/profile")
async def update_profile(request: Request, payload: ProfileUpdate):
    catalog = await request.app.state.custom_exercises.catalog_for(request.state.user_id)
    if any(
        not (e := catalog.get(item)) or not e.is_active
        for item in payload.profile.priority_exercise_ids
    ):
        raise HTTPException(422, "Priority exercise must be an active catalog record")
    available = {e.equipment_code for e in catalog.exercises if e.is_active}
    if any(item not in available for item in payload.profile.equipment):
        raise HTTPException(422, "Equipment must use catalog codes")
    try:
        return await request.app.state.training.update_profile(request.state.user_id, payload)
    except TurnConflict as exc:
        raise HTTPException(409, str(exc)) from None


@router.get("/review")
async def training_review(request: Request, options: Annotated[ReviewOptions, Query()]):
    # FastAPI materializes query defaults; use raw keys to detect explicit legacy dates.
    if "selection_mode" not in request.query_params and any(
        key in request.query_params for key in ("recent_days", "baseline_days", "end_date")
    ):
        options = options.model_copy(update={"selection_mode": "date_window"})
    try:
        async with request.app.state.store.sessions() as session:
            return await load_training_review(
                session, request.state.user_id, options, request.app.state.catalog
            )
    except ObjectNotFound as exc:
        raise HTTPException(404, str(exc)) from None
