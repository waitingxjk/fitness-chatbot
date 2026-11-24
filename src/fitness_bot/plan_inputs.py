"""Resolve proposal defaults without turning assumptions into saved user facts."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from statistics import median

from fitness_bot.training_schemas import TrainingProfile

DEFAULT_EQUIPMENT = (
    "body weight",
    "dumbbell",
    "barbell",
    "cable",
    "leverage machine",
    "smith machine",
    "sled machine",
    "assisted",
    "kettlebell",
    "band",
    "resistance band",
)


def fresh_readiness(profile):
    readiness = profile.readiness
    if readiness and timedelta(0) <= datetime.now(UTC) - readiness.observed_at <= timedelta(
        hours=36
    ):
        return readiness
    return None


def restore_conversation_inputs(profile, previous):
    """Only restore server-extracted explicit inputs, never last proposal's defaults."""
    result = deepcopy(profile)
    for turn in previous:  # newest first, already scoped to the current account/conversation
        prior = (turn.context or {}).get("training_profile") or {}
        if prior.get("turn_overrides"):
            overrides = prior["turn_overrides"]
            result["profile"].update(overrides)
            result["turn_overrides"] = deepcopy(overrides)
            break
    return result


def resolve_candidate_inputs(request, catalog):
    saved = deepcopy(request.context.training_profile or {"version": 0, "profile": {}})
    profile = TrainingProfile.model_validate(saved.get("profile", {})).model_dump(mode="json")
    assumptions = []
    history = request.context.workout_history
    workouts = history.workouts[:7] if history else []
    equipment, minutes, action_counts, set_counts = set(), [], [], []
    for workout in workouts:
        records = [r for r in workout.get("records", []) if r.get("completed_sets", 0) > 0]
        if records:
            action_counts.append(len(records))
            set_counts.append(sum(r["completed_sets"] for r in records))
        for record in records:
            exercise = catalog.get(record.get("exercise_id"))
            if exercise and exercise.is_active:
                equipment.add(exercise.equipment_code)
        try:
            start = datetime.fromisoformat(workout["started_at"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(workout["ended_at"].replace("Z", "+00:00"))
            duration = (
                (end - start).total_seconds() - workout.get("paused_duration_seconds", 0)
            ) / 60
            if records and 10 <= duration <= 180:
                minutes.append(duration)
        except (KeyError, TypeError, ValueError, AttributeError):
            pass

    def assume(field, value, source, explanation):
        profile[field] = value
        assumptions.append(
            {"field": field, "source": source, "value": value, "explanation": explanation}
        )

    if profile["goal"] is None:
        assume(
            "goal",
            "general_fitness",
            "default",
            "未指定目标，先按综合体能安排；可直接告诉我改为增肌或力量。",
        )
    if profile["experience"] is None:
        # Recorded frequency is not proof of skill; use a conservative dose policy.
        assume(
            "experience",
            "beginner",
            "default",
            "未指定经验，先采用保守训练量；不将历史次数当作动作熟练度。",
        )
    if profile["session_minutes"] is None:
        estimate = min(180, max(10, round(median(minutes)))) if minutes else 60
        assume(
            "session_minutes",
            estimate,
            "history" if minutes else "default",
            f"按近期训练时长暂定 {estimate} 分钟，可直接调整。"
            if minutes
            else "未指定时长，先按 60 分钟安排，可直接调整。",
        )
    if not profile["equipment"]:
        available = {e.equipment_code for e in catalog.exercises if e.is_active}
        # History is a preference, not a restriction. Assume normal gym equipment
        # until the user explicitly limits it; this avoids another questionnaire.
        codes = sorted((equipment | set(DEFAULT_EQUIPMENT)) & available)
        assume(
            "equipment",
            codes,
            "history" if equipment else "default",
            (
                "优先参考历史使用的"
                + "、".join(catalog.label("equipment", e) for e in sorted(equipment))
                + "；其余器械暂按可用安排，没有时告诉我替换。"
            )
            if equipment
            else "器械暂按常见健身房配置可用安排，没有对应器械时告诉我替换。",
        )
    parsed = TrainingProfile.model_validate(profile)
    if fresh_readiness(parsed) is None or any(
        getattr(parsed.readiness, key, "unknown") == "unknown"
        for key in ("pain", "fatigue", "sleep")
    ):
        assumptions.append(
            {
                "field": "readiness",
                "source": "unknown",
                "value": None,
                "explanation": (
                    "未报告本次状态，不假设恢复良好，也不据此增加负荷；执行时按实际感受调整。"
                ),
            }
        )
    saved.update(
        profile=profile,
        planning_assumptions=assumptions,
        planning_history_summary={
            "sample_workouts": len(workouts),
            "typical_exercise_count": median(action_counts) if action_counts else None,
            "typical_completed_sets": median(set_counts) if set_counts else None,
            "familiar_equipment": sorted(equipment),
        },
    )
    request.context.training_profile = saved
    return request
