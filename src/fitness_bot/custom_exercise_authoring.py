"""Read-only expert authoring: metadata previews never create catalog records."""

from pydantic import ValidationError

from fitness_bot.custom_exercise_schemas import CustomExerciseDraft
from fitness_bot.custom_exercise_storage import draft_metadata, exact_matches, validate_definition
from fitness_bot.schemas import ChatResponse, ExerciseCard, ProcessingStep


def saved_card(exercise, catalog, *, can_add=False):
    return ExerciseCard(
        exercise=exercise,
        equipment_label=catalog.label("equipment", exercise.equipment_code),
        body_part_label=catalog.label("bodyPart", exercise.body_part_code),
        context="个人自定义动作，可在动作详情中查看说明。"
        if exercise.source_kind == "custom"
        else "已有标准动作，无需重复创建。",
        can_add_to_active_workout=can_add,
    )


async def author_response(agent, state, writer):
    writer({"type": "status", "stage": "responding"})
    request = state["request"]
    try:
        draft = CustomExerciseDraft.model_validate(
            await agent.model.author_exercise(request, agent.catalog)
        )
    except (ValidationError, ValueError):
        draft = CustomExerciseDraft(
            status="needs_input", question="请描述一下这个动作的做法，以及按次数还是时长记录。"
        )
    active = request.context.active_workout
    can_add = bool(active and active.status in {"active", "paused"})
    cards = []
    if draft.status == "existing":
        existing = agent.catalog.get(draft.existing_exercise_id)
        if existing is None or not existing.is_active:
            draft = CustomExerciseDraft(
                status="needs_input",
                question="未找到可用的已保存动作。可以编辑上方草稿，或告诉我正确的动作名称继续建档。",
            )
    if draft.status == "existing":
        exercise = agent.catalog.get(draft.existing_exercise_id)
        text = f"已找到「{exercise.display_name_zh_hans}」，可以直接使用。"
        cards = [saved_card(exercise, agent.catalog, can_add=can_add)]
        result = {"status": "existing", "exercise": exercise.model_dump(mode="json", by_alias=True)}
    elif draft.status == "ready" and draft.creation_requested:
        spec = draft.exercise
        try:
            validate_definition(spec, agent.catalog)
            matches = exact_matches(agent.catalog, spec.name)
            if len(matches) > 1:
                raise ValueError("有多个同名动作，请补充名称以区分。")
            if matches:
                exercise = matches[0]
                if not exercise.is_active:
                    raise ValueError("同名动作已归档，可在个人动作详情中恢复。")
                cards = [saved_card(exercise, agent.catalog, can_add=can_add)]
                result = {
                    "status": "existing",
                    "exercise": exercise.model_dump(mode="json", by_alias=True),
                }
                text = f"已有「{exercise.display_name_zh_hans}」，可以直接使用。"
            else:
                result = {
                    "status": "draft",
                    "draft_id": str(request.request_id),
                    "version": 1,
                    **draft_metadata(spec, agent.catalog),
                }

                text = (
                    f"已整理「{spec.name}」的动作信息，目前未保存。"
                    "请查看下方草稿，确认后保存到个人动作库。"
                )
        except ValueError as exc:
            text = str(exc)
            result = {"status": "needs_input", "question": text}
    else:
        text = draft.question or "需要把这个动作保存到你的个人动作库吗？"
        result = {"status": "needs_input", "question": text}
    if draft.name_correction and result["status"] in {"draft", "existing"}:
        result["name_correction"] = draft.name_correction.model_dump(mode="json")
        text = f"我按「{draft.name_correction.resolved_name}」理解这个动作。" + text
    step = ProcessingStep(
        stage="responding",
        title="动作建档信息已整理",
        summary="已完成动作识别与信息检查；新动作只返回草稿，等待用户确认保存。",
        data={"status": result["status"]},
    )
    writer({"type": "trace", "step": step.model_dump(mode="json")})
    response = ChatResponse(
        conversation_id=request.conversation_id,
        kind="custom_exercise",
        text=text,
        cards=cards,
        catalog_version=agent.catalog.version,
        custom_exercise=result,
        trace=[*state["trace"], step],
    )
    # Persist only the chat/preview. Saving an exercise requires a separate HTTP action.
    writer({"type": "done", "response": response.model_dump(mode="json", by_alias=True)})
    return {"response": response}
