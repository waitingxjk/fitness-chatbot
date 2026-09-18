"""Two bounded decisions: identify the user's exercise, then fill its definition."""

from fitness_bot.custom_exercise_schemas import (
    CustomExerciseDraft,
    ExerciseNameCorrection,
    ExerciseNameResolution,
    ExistingExerciseMatch,
)
from fitness_bot.custom_exercise_storage import normalized_name


async def author_exercise(model, request, catalog):
    identity = await model._decision(
        request,
        ExerciseNameResolution,
        "本步只辨认动作名称，不生成动作元信息，不查目录，不分配任何 ID。"
        "先检查用户名字是否含错别字/同音误写；独立于标准库是否收录。"
        "不能把常见动作的正确说明套在错误名字上。"
        "有唯一合理候选、读音/字形与动作语境一致且无冲突描述时，直接 resolved 推进草稿，"
        "is_correction=true，input_name 保留误写，name 使用理解后的名称。"
        "例如健身建档语境中的强天使/抢天使通常可高把握理解为墙天使（Wall Angel），"
        "没有相反信息就直接继续，不为明显错字单独打断用户。"
        "有多个合理候选、只有弱猜测、或用户描述与候选冲突时才 needs_input 问关键区别。"
        "用户只给出动作大类、缺少会影响动作身份/部位/记录方式的信息，也属于不确定。"
        "例如只说飞鸟，无法区分胸部飞鸟或反向飞鸟时，应问是哪一种；"
        "不能默认选胸部，不能把不同部位或做法的变式全当同一动作的别名。"
        "用户明确说‘是墙天使’属于纠正，直接 resolved，name=墙天使，不重复确认。"
        "以用户最近明确的命名/纠正为准，历史助手的错误名字没有优先权。"
        "用户否认候选时不要继续套用；用户有意自定义名字且说明明确则尊重该名字。"
        "当用户只修改部位等字段时，沿用最近草稿已确认的名字。"
        "discarded_names 列出本轮及历史草稿中应移除的误写名称和错误别名（包括带‘动作’后缀）；"
        "用户明确要求保留的自定义名称或别名不能列入 discarded_names，也不能擅自纠正。"
        "resolved 表示动作身份已明确，name 是下一步必须采用的名称；"
        "needs_input 的 question 只问一个关键问题，name 可给出待确认候选。",
        skill="exercise-authoring",
    )
    if identity.status == "needs_input":
        return CustomExerciseDraft(status="needs_input", question=identity.question)
    extra = {
        "resolved_name": identity.name,
        "discarded_names": identity.discarded_names,
        "terms": catalog.terms,
        "existing_exercises": [
            {
                "id": e.id,
                "name": e.display_name_zh_hans,
                "aliases": e.aliases_zh_hans,
                "source_name": e.source_name,
                "detail_type": e.detail_type,
                "source_kind": e.source_kind,
                "is_active": e.is_active,
            }
            for e in catalog.exercises
        ],
    }
    for _ in range(2):
        draft = await model._decision(
            request,
            CustomExerciseDraft,
            "按技能完成个人动作建档；只返回结构化草稿，不声称已经保存。"
            "名称已通过独立识别：使用 resolved_name，不重复询问已解决的名称。"
            "修正旧草稿的错误名字与别名；discarded_names 及其带‘动作’后缀变体不可作为别名。"
            "context.custom_exercise_drafts 只是未保存草稿，不是已有动作库；"
            "draft_id、request_id、conversation_id 都不是 exercise ID。"
            "existing_exercise_id 只能来自 existing_exercises 中的真实、启用且语义相同动作。"
            "目录没有匹配动作时返回 ready+exercise，不能把草稿当 existing。"
            "分类只能使用 terms 的 code 或 unknown。"
            "creation_requested 根据用户本轮与此前的创建意图判定；"
            "用户纠正旧草稿时延续创建意图，不把模型自己的提议当作授权。"
            "如有 validation_error，修复该问题并返回完整结果。",
            extra,
            skill="exercise-authoring",
        )
        error = None
        if draft.status == "existing":
            exercise = catalog.get(draft.existing_exercise_id)
            if exercise is None or not exercise.is_active:
                error = "unavailable existing_exercise_id; use an active catalog ID or ready draft"
            elif normalized_name(identity.name) not in {
                normalized_name(n)
                for n in [
                    exercise.display_name_zh_hans,
                    exercise.source_name,
                    *exercise.aliases_zh_hans,
                ]
            }:
                match = await model._decision(
                    request,
                    ExistingExerciseMatch,
                    "独立核对候选是否与 resolved_name 是同一个动作。"
                    "比较实际动作方式、器械和训练性质；同部位、相近目的或名称相似不等于同一动作。"
                    "证据不足时 same_exercise=false，不用近似动作代替要创建的动作。"
                    "例如上部背部拉伸不能代替墙天使。仅做身份核对，不请求用户确认。",
                    {
                        "resolved_name": identity.name,
                        "candidate": exercise.model_dump(mode="json", by_alias=True),
                    },
                    skill="exercise-authoring",
                )
                if not match.same_exercise:
                    error = (
                        f"different exercise: {exercise.id} is {exercise.display_name_zh_hans}, "
                        f"not {identity.name}; return ready if no identical catalog record exists"
                    )
        elif draft.status == "ready" and normalized_name(draft.exercise.name) != normalized_name(
            identity.name
        ):
            error = "exercise.name must equal resolved_name; discard the old erroneous name"
        if error is None:
            # Presentation metadata comes from the identification step, not completion.
            correction = None
            if identity.is_correction:
                correction = ExerciseNameCorrection(
                    input_name=identity.input_name, resolved_name=identity.name
                )
            draft = draft.model_copy(update={"name_correction": correction})
            if draft.status == "ready":
                discarded = list(identity.discarded_names)
                if identity.is_correction:
                    discarded.append(identity.input_name)
                rejected = {alias_key(name) for name in discarded}
                aliases = [a for a in draft.exercise.aliases if alias_key(a) not in rejected]
                draft = draft.model_copy(
                    update={"exercise": draft.exercise.model_copy(update={"aliases": aliases})}
                )
            return draft
        extra = {**extra, "validation_error": error}
    return CustomExerciseDraft(
        status="needs_input",
        question=f"「{identity.name}」的信息暂未整理完整，可以再试一次，或编辑上方草稿后确认保存。",
    )


def alias_key(name):
    # Treat an erroneous name and the same name suffixed with 动作 alike.
    return normalized_name(name).removesuffix("动作")
