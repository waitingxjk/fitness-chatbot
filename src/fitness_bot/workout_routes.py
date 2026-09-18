"""Workout backup APIs, independent of chat and model orchestration."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.exc import SQLAlchemyError

from fitness_bot.workout_schemas import Identifier, StoredWorkout, WorkoutReceipt, WorkoutUpload
from fitness_bot.workout_storage import WorkoutConflict


class WorkoutRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def validate(request):
            try:
                return await handler(request)
            except RequestValidationError as exc:
                # Do not echo private workout input; NaN/Infinity input must also
                # produce a valid JSON 422 instead of failing error serialization.
                return JSONResponse(
                    status_code=422,
                    content={
                        "detail": [
                            {key: error[key] for key in ("loc", "msg", "type")}
                            for error in exc.errors()
                        ]
                    },
                )

        return validate


router = APIRouter(prefix="/v1/workouts", tags=["workouts"], route_class=WorkoutRoute)


@router.put("/{workout_id}", response_model=WorkoutReceipt)
async def upload_workout(workout_id: Identifier, payload: WorkoutUpload, request: Request):
    try:
        return await request.app.state.workouts.upload(
            workout_id, payload, user_id=request.state.user_id
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except WorkoutConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except SQLAlchemyError:
        raise HTTPException(503, "Workout storage unavailable") from None


@router.get("", response_model=list[StoredWorkout])
async def list_workouts(
    request: Request,
    sourceId: Annotated[Identifier, Query()],
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    try:
        return await request.app.state.workouts.list(
            sourceId, limit=limit, offset=offset, user_id=request.state.user_id
        )
    except SQLAlchemyError:
        raise HTTPException(503, "Workout storage unavailable") from None


@router.get("/{workout_id}", response_model=StoredWorkout)
async def read_workout(
    workout_id: Identifier, request: Request, sourceId: Annotated[Identifier, Query()]
):
    try:
        result = await request.app.state.workouts.read(
            sourceId, workout_id, user_id=request.state.user_id
        )
    except SQLAlchemyError:
        raise HTTPException(503, "Workout storage unavailable") from None
    if result is None:
        raise HTTPException(404, "Workout not found")
    return result
