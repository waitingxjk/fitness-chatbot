"""Qualitative catalog previews; never prescriptions or accepted workout plans."""

from fitness_bot.planning import current_exercise_ids, planning_view
from fitness_bot.prescriptions import validate_prescription_reason
from fitness_bot.schemas import ExerciseCard
from fitness_bot.training_decision import effective_profile
from fitness_bot.training_schemas import SessionPreviewDraft


def qualitative_preview(payload):
    """Replace invalid dose prose without inventing or weakening exercise data."""

    def prose(value, fallback):
        if isinstance(value, str):
            try:
                validate_prescription_reason(value)
            except ValueError:
                return fallback
        return value

    draft = dict(payload)
    for field, fallback in (
        ("theme", "训练动作候选"),
        ("rationale", "先讨论动作候选，具体训练安排待条件确认。"),
    ):
        if field in draft:
            draft[field] = prose(draft[field], fallback)
    if isinstance(draft.get("items"), list):
        draft["items"] = [
            {**item, "purpose": prose(item["purpose"], "动作候选，具体安排待确认。")}
            if isinstance(item, dict) and "purpose" in item
            else item
            for item in draft["items"]
        ]
    return SessionPreviewDraft.model_validate(draft)


def preview_candidates(request, catalog):
    request = planning_view(request)
    existing = current_exercise_ids(request)
    available = [e for e in catalog.exercises if e.is_active and e.id not in existing]
    equipment = set(effective_profile(request).equipment)
    equipment.update(request.context.available_equipment)
    if equipment:
        return [e for e in available if e.equipment_code in equipment]
    known = {item.exercise_id for item in request.context.recent_exercises}
    if history := request.context.workout_history:
        known.update(
            record.get("exercise_id")
            for workout in history.workouts
            for record in workout.get("records", [])
        )
    return [e for e in available if e.id in known]


def preview_cards(draft, candidates, catalog):
    draft = SessionPreviewDraft.model_validate(draft)
    by_id = {exercise.id: exercise for exercise in candidates}
    seen = set()
    cards = []
    selected = []
    for item in draft.items:
        if item.exercise_id not in by_id or item.exercise_id in seen:
            continue
        seen.add(item.exercise_id)
        selected.append(item)
        exercise = by_id[item.exercise_id]
        cards.append(
            ExerciseCard(
                exercise=exercise,
                equipment_label=catalog.label("equipment", exercise.equipment_code),
                body_part_label=catalog.label("bodyPart", exercise.body_part_code),
                context=item.purpose,
                can_add_to_active_workout=False,
            )
        )
    if len(cards) != len(draft.items):
        draft = draft.model_copy(
            update={
                "theme": "训练动作候选",
                "rationale": "只展示已匹配当前可用动作库的候选，具体安排待条件确认。"
                if cards
                else "这次未能匹配可用的动作候选，请补充训练器械后重新生成。",
                "items": selected,
            }
        )
    return draft, cards
