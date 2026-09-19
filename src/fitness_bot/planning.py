"""Target-specific plan context; current snapshots are evidence, never mutation commands."""


def planning_target(request):
    return request.context.planning_target or "next_workout"


def current_exercise_ids(request):
    current = request.context.current_workout
    if planning_target(request) != "current_workout" or current is None:
        return set()
    return {record.exerciseId for record in current.workout.records}


def planning_metadata(request):
    current = (
        request.context.current_workout if planning_target(request) == "current_workout" else None
    )
    sets = [item for record in current.workout.records for item in record.sets] if current else []
    return {
        "planning_target": planning_target(request),
        "current_workout": current.model_dump(mode="json") if current else None,
        "remaining_minutes": request.context.remaining_minutes if current else None,
        # Snapshot flags are counted as recorded, including drafts. They are not
        # validated working sets and invalid raw doses remain untouched.
        "existing_completed_sets": sum(item.isCompleted for item in sets),
        "existing_pending_sets": sum(not item.isCompleted for item in sets),
    }


def planning_view(request):
    """Isolate next-workout decisions from App-owned, unfinished/client history."""
    if planning_target(request) == "current_workout":
        return request
    context = request.context.model_copy(
        update={
            "current_workout": None,
            "active_workout": None,
            "recent_exercises": [],
            "remaining_minutes": None,
            "session_plans": [
                saved
                for saved in request.context.session_plans
                if saved.get("plan", {}).get("planning_target", "next_workout") != "current_workout"
            ],
        }
    )
    return request.model_copy(update={"context": context})


def plan_outcome(adjustment, items):
    if adjustment == "rest":
        return "rest"
    return "add_exercises" if items else "nothing_to_add"
