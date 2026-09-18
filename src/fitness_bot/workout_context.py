"""Read-only, bounded projection of synced training for the model context."""

import json

from sqlalchemy import func, select

from fitness_bot.schemas import StoredWorkoutHistory
from fitness_bot.workout_storage import WorkoutRow

MAX_WORKOUTS = 30
MAX_RECORDS = 100
MAX_SETS = 50
MAX_HISTORY_CHARS = 100_000
SET_FIELDS = (
    "loadType",
    "loadValue",
    "loadUnit",
    "repetitions",
    "durationSeconds",
    "difficulty",
    "rpe",
)


def _json_size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def _project(workout):
    records = [r for r in workout.get("records", []) if not r.get("isDraft", False)]
    result = {
        "title": workout.get("title", "")[:160],
        "started_at": workout.get("startedAt"),
        "ended_at": workout.get("endedAt"),
        "paused_duration_seconds": workout.get("pausedDurationSeconds", 0),
        "record_count": len(records),
        "records": [],
        "truncated": len(records) > MAX_RECORDS,
    }
    for record in sorted(records, key=lambda r: r.get("sequence", 0))[:MAX_RECORDS]:
        completed = [s for s in record.get("sets", []) if s.get("isCompleted", False)]
        result["truncated"] |= len(completed) > MAX_SETS
        result["records"].append(
            {
                "exercise_id": record.get("exerciseId", "")[:191],
                "exercise_name": record.get("exerciseNameSnapshot", "")[:160],
                "performed_at": record.get("occurredAt"),
                "completed_sets": len(completed),
                "sets": [
                    {key: item[key] for key in SET_FIELDS if item.get(key) is not None}
                    for item in sorted(completed, key=lambda s: s.get("sequence", 0))[:MAX_SETS]
                ],
            }
        )
    return result


async def load_workout_history(session, user_id):
    """Use the caller's transaction and already resolved internal identity.

    Order by training date, not upload date: a historical backfill must not push
    recent training out of the window. Never fetch another user's snapshots.
    """
    filters = (
        WorkoutRow.user_id == user_id,
        WorkoutRow.deleted.is_(False),
        WorkoutRow.workout["status"].as_string() == "completed",
    )
    total = await session.scalar(select(func.count()).select_from(WorkoutRow).where(*filters))
    rows = await session.scalars(
        select(WorkoutRow)
        .where(*filters)
        .order_by(
            WorkoutRow.workout["startedAt"].as_string().desc(),
            WorkoutRow.source_id,
            WorkoutRow.workout_id,
        )
        .limit(MAX_WORKOUTS)
    )
    history = StoredWorkoutHistory(total_workouts=total, truncated=total > MAX_WORKOUTS)
    for row in rows:
        projected = _project(row.workout)
        history.workouts.append(projected)
        # Bound the complete serialized context, not just the number of workouts.
        while projected["records"] and _json_size(history.model_dump()) > MAX_HISTORY_CHARS:
            projected["records"].pop()
            projected["truncated"] = True
        history.truncated |= projected["truncated"]
        if _json_size(history.model_dump()) > MAX_HISTORY_CHARS:
            history.workouts.pop()
            history.truncated = True
            break
    return history
