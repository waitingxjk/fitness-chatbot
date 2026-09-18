"""A bounded LangGraph: understand -> retrieve -> compose -> respond."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import aclosing, suppress
from typing import TypedDict

import anyio
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from fitness_bot.catalog import Catalog
from fitness_bot.model import CompanionModel
from fitness_bot.schemas import (
    ChatRequest,
    ChatResponse,
    Exercise,
    ExerciseCard,
    ProcessingStep,
    SearchPlan,
)
from fitness_bot.session_preview import preview_candidates, preview_cards
from fitness_bot.training_decision import (
    effective_profile,
    plan_question,
    render_review,
    render_session,
    validate_review,
    validate_session_plan,
)


class AgentState(TypedDict, total=False):
    request: ChatRequest
    plan: SearchPlan
    cards: list[ExerciseCard]
    response: ChatResponse
    trace: list[ProcessingStep]
    previous_results: list[Exercise]
    presentation: dict


class CompanionAgent:
    def __init__(self, catalog: Catalog, model: CompanionModel):
        self.catalog = catalog
        self.model = model
        self.training_store = None
        self.custom_store = None
        graph = StateGraph(AgentState)
        graph.add_node("understand", self._understand)
        graph.add_node("retrieve", self._retrieve)
        graph.add_node("compose", self._compose)
        graph.add_node("respond", self._respond)
        graph.add_edge(START, "understand")
        graph.add_edge("understand", "retrieve")
        graph.add_edge("retrieve", "compose")
        graph.add_edge("compose", "respond")
        graph.add_edge("respond", END)
        self.graph = graph.compile()

    async def _understand(self, state: AgentState):
        get_stream_writer()({"type": "status", "stage": "understanding"})
        request = state["request"]
        ids = [r.exercise_id for r in request.context.recent_exercises]
        previous_results = [
            exercise for item in request.previous_result_ids if (exercise := self.catalog.get(item))
        ]
        reference = {
            "terms": self.catalog.search_terms,
            "previous_results": [
                e.model_dump(mode="json", by_alias=True) for e in previous_results
            ],
            "history_exercise_names": {
                item: e.display_name_zh_hans for item in ids if (e := self.catalog.get(item))
            },
        }
        plan = await self.model.plan(request, reference)
        if plan.kind == "session_plan" and request.context.plan_to_modify:
            plan.parent_plan_id = request.context.plan_to_modify
        if plan.kind == "review" and plan.review_options and self.training_store:
            explicit = request.context.review_options.model_dump(exclude_unset=True)
            if explicit:
                merged = {**plan.review_options.model_dump(exclude_unset=True), **explicit}
                if merged.get("scope", "multiple") == "multiple":
                    merged.update(source_id=None, workout_id=None)
                plan.review_options = type(plan.review_options).model_validate(merged)
            if "time_zone" not in plan.review_options.model_fields_set:
                saved = (request.context.training_profile or {}).get("profile", {})
                current_zone = (request.context.training_review or {}).get(
                    "time_zone", saved.get("time_zone", "UTC")
                )
                plan.review_options = plan.review_options.model_copy(
                    update={"time_zone": current_zone}
                )
            request.context.training_review = await self.training_store.review(
                request._user_id, plan.review_options, self.catalog
            )
        if plan.kind == "session_plan" and plan.session_inputs:
            saved = request.context.training_profile or {"version": 0, "profile": {}}
            overrides = plan.session_inputs.model_dump(
                mode="json", exclude_none=True, exclude={"target_date"}
            )
            request.context.training_profile = {
                **saved,
                "profile": {**saved["profile"], **overrides},
                "turn_overrides": overrides,
            }
        step = ProcessingStep(
            stage="understanding",
            title="请求解析完成",
            summary={
                "custom_exercise": "识别为自定义动作建档",
                "chat": "识别为普通对话",
                "search": "识别为动作搜索",
                "recommendation": "识别为动作推荐",
                "review": "识别为训练复盘",
                "session_plan": "识别为整次训练方案",
            }[plan.kind],
            data={**plan.model_dump(mode="json"), "image_count": len(request.images)},
        )
        get_stream_writer()({"type": "trace", "step": step.model_dump(mode="json")})
        return {"plan": plan, "trace": [step], "previous_results": previous_results}

    async def _retrieve(self, state: AgentState):
        plan, request = state["plan"], state["request"]
        get_stream_writer()({"type": "status", "stage": "retrieving"})
        if plan.kind == "custom_exercise":
            step = ProcessingStep(
                stage="retrieving",
                title="动作目录已准备",
                summary="对照标准库和当前账号个人库查重，并准备建档术语。",
            )
            get_stream_writer()({"type": "trace", "step": step.model_dump(mode="json")})
            return {"cards": [], "trace": [*state["trace"], step]}
        if plan.kind in {"review", "session_plan"}:
            step = ProcessingStep(
                stage="retrieving",
                title="训练决策资料已准备",
                summary="使用完整日期窗口统计、用户目标与状态；完整方案独立校验，不使用动作卡片数量上限。",
                data={
                    "snapshot_count": (request.context.training_review or {}).get(
                        "snapshot_count", 0
                    )
                },
            )
            get_stream_writer()({"type": "trace", "step": step.model_dump(mode="json")})
            return {"cards": [], "trace": [*state["trace"], step]}
        limit = 2 if plan.kind == "search" else plan.recommendation_count
        exercises = (
            self.catalog.search(plan, limit=len(self.catalog.exercises))
            if plan.kind != "chat"
            else []
        )
        active = request.context.active_workout
        can_add = bool(active and active.status in {"active", "paused"})
        if plan.kind == "recommendation":
            if can_add:
                exercises = [e for e in exercises if e.id not in active.exercise_ids]
            equipment = request.context.available_equipment
            if equipment:
                codes = {self.catalog.code("equipment", item) for item in equipment}
                exercises = [e for e in exercises if e.equipment_code in codes]
        cards = []
        for exercise in exercises[:limit]:
            equipment_label = self.catalog.label("equipment", exercise.equipment_code)
            body_part_label = self.catalog.label("bodyPart", exercise.body_part_code)
            reason = f"匹配：{body_part_label} · {equipment_label}"
            if plan.query:
                reason += f" · {plan.query}"
            if plan.kind == "recommendation":
                count = len(request.context.recent_exercises)
                saved = request.context.workout_history
                if saved and saved.workouts:
                    count = sum(len(workout["records"]) for workout in saved.workouts)
                basis = (
                    f"已参考 {count} 条训练动作记录" if count else "训练历史不足，主要依据本轮条件"
                )
                reason = f"{basis}；{reason}"
            cards.append(
                ExerciseCard(
                    exercise=exercise,
                    equipment_label=equipment_label,
                    body_part_label=body_part_label,
                    context=reason,
                    can_add_to_active_workout=can_add,
                )
            )
        if plan.kind == "recommendation" and cards and hasattr(self.model, "recommendation"):
            from fitness_bot.prescriptions import SetPrescription, trial_load, validate_dose_type

            draft = await self.model.recommendation(request, cards)
            by_id = {card.exercise.id: card for card in cards}
            selected = []
            seen = set()
            for item in draft.items:
                if item.exercise_id not in by_id or item.exercise_id in seen:
                    raise ValueError(
                        "Recommendation must select unique retrieved catalog exercises"
                    )
                seen.add(item.exercise_id)
                card = by_id[item.exercise_id]
                rows = [
                    {**row.model_dump(), "load": trial_load(card.exercise)}
                    for row in item.set_prescriptions
                ]
                validate_dose_type(rows, card.exercise)
                if not any(row["kind"] == "working" for row in rows):
                    raise ValueError("Recommendation requires working sets")
                selected.append(
                    card.model_copy(
                        update={
                            "context": item.purpose,
                            "set_prescriptions": [
                                SetPrescription.model_validate(row) for row in rows
                            ],
                        }
                    )
                )
            cards = selected
        step = ProcessingStep(
            stage="retrieving",
            title="本地动作库检索完成" if plan.kind != "chat" else "跳过动作检索",
            summary=f"筛选后匹配 {len(exercises)} 个动作，返回 {len(cards)} 张动作卡"
            if plan.kind != "chat"
            else "本轮无需动作卡",
            data={
                "matched_count": len(exercises),
                "returned_count": len(cards),
                "exercise_ids": [card.exercise.id for card in cards],
                "catalog_version": self.catalog.version,
                "available_equipment": request.context.available_equipment,
                "excluded_active_exercise_ids": active.exercise_ids
                if can_add and plan.kind == "recommendation"
                else [],
            },
        )
        get_stream_writer()({"type": "trace", "step": step.model_dump(mode="json")})
        return {"cards": cards, "trace": [*state["trace"], step]}

    async def _compose(self, state):
        if state["plan"].kind != "review" or not (
            hasattr(self.model, "compose_review") and hasattr(self.model, "stream_composed_review")
        ):
            return {}
        from fitness_bot.presentation import compile_outline, fallback_outline, validation_reason

        writer = get_stream_writer()
        writer({"type": "status", "stage": "responding"})
        request = state["request"]
        facts = request.context.training_review or {}
        fallback = False
        fallback_reason = None
        try:
            raw = await self.model.compose_review(request)
            presentation = compile_outline(raw, facts)
        except ValueError as exc:
            fallback_reason = validation_reason(exc)
            presentation = compile_outline(fallback_outline(facts), facts)
            fallback = True
        step = ProcessingStep(
            stage="responding",
            title="报告结构已编排",
            summary="已确定文字与图表顺序，图表数据已与本次记录绑定。",
            data={
                "block_count": len(presentation["outline"]),
                "chart_count": len(presentation["artifacts"]),
                "layout_fallback": fallback,
                "fallback_reason": fallback_reason,
            },
        )
        writer({"type": "trace", "step": step.model_dump(mode="json")})
        return {"presentation": presentation, "trace": [*state["trace"], step]}

    async def _respond(self, state: AgentState, *, training_discussion=False):
        if state["plan"].kind == "custom_exercise":
            from fitness_bot.custom_exercise_authoring import author_response

            return await author_response(self, state, get_stream_writer())
        if not training_discussion and state["plan"].kind in {"review", "session_plan"}:
            return await self._training_response(state)
        writer = get_stream_writer()
        saved = state["request"].context.workout_history
        saved_count = len(saved.workouts) if saved else 0
        step = ProcessingStep(
            stage="responding",
            title="开始生成回复",
            summary=(
                "先结合已同步历史讨论训练思路，具体方案待必要条件明确后生成。"
                if training_discussion
                else (
                    f"结合会话历史、{saved_count} 次已同步训练"
                    f"和 {len(state['cards'])} 张动作卡生成文本"
                )
            ),
            data={
                "plan_generation_deferred": training_discussion,
                "saved_workout_count": saved_count,
                "saved_workout_total": saved.total_workouts if saved else 0,
                "saved_workout_history_truncated": saved.truncated if saved else False,
                "history_message_count": len(state["request"].history),
                "image_count": len(state["request"].images),
                "history_image_count": min(
                    6, sum(len(message.images) for message in state["request"].history)
                ),
                "previous_result_ids": [exercise.id for exercise in state["previous_results"]],
            },
        )
        trace = [*state["trace"], step]
        writer({"type": "status", "stage": "responding"})
        writer({"type": "trace", "step": step.model_dump(mode="json")})
        text = ""
        async for delta in self.model.stream_reply(
            state["request"], state["plan"], state["cards"], state["previous_results"]
        ):
            text += delta
            writer({"type": "text_delta", "delta": delta})
        response = ChatResponse(
            conversation_id=state["request"].conversation_id,
            kind=state["plan"].kind,
            text=text,
            cards=state["cards"],
            suggestion_kind=("single_exercise" if len(state["cards"]) == 1 else "alternatives")
            if state["cards"] and state["plan"].kind == "recommendation"
            else None,
            catalog_version=self.catalog.version,
            trace=trace,
        )
        if state["cards"]:
            writer(
                {
                    "type": "exercise_cards",
                    "kind": response.kind,
                    "result_id": str(response.result_id),
                    "cards": [
                        card.model_dump(mode="json", by_alias=True) for card in state["cards"]
                    ],
                }
            )
        writer({"type": "done", "response": response.model_dump(mode="json", by_alias=True)})
        return {"response": response}

    async def _preview_response(self, state):
        request = state["request"]
        readiness = effective_profile(request).readiness
        blocked = readiness is not None and readiness.pain == "present"
        candidates = [] if blocked else preview_candidates(request, self.catalog)
        cards = []
        theme = "训练安排"
        rationale = "先确认训练条件，再选择适合本次安排的动作。"
        question = plan_question(request)
        if blocked:
            theme = "暂不安排训练动作"
            rationale = "当前已报告疼痛，先停止引发疼痛的动作。"
            question = "请说明不适情况；持续或明显不适时寻求专业评估。"
        elif candidates:
            if hasattr(self.model, "session_preview_with_progress"):
                preview_draft = await self.model.session_preview_with_progress(
                    request, candidates, on_progress=self._plan_progress_writer()
                )
            else:
                preview_draft = await self.model.session_preview(request, candidates)
            draft, cards = preview_cards(preview_draft, candidates, self.catalog)
            theme, rationale = draft.theme, draft.rationale
        preview = {
            "status": "blocked" if blocked else "needs_input",
            "theme": theme,
            "next_question": question,
            "target_date": state["plan"].session_inputs.target_date.isoformat()
            if state["plan"].session_inputs and state["plan"].session_inputs.target_date
            else None,
        }
        text = f"**{theme} · 待完善**\n\n{rationale}"
        if cards:
            text += (
                "\n\n下方按候选顺序展示动作卡。组次、重量和休息待条件明确后安排；"
                "当前不能确认或开始这份训练。"
            )
        text += f"\n\n{question}"
        step = ProcessingStep(
            stage="responding",
            title="训练候选卡已准备" if cards else "训练条件待明确",
            summary=f"返回 {len(cards)} 张非执行动作候选卡；未创建训练计划。",
            data={"plan_generation_deferred": True, "preview_card_count": len(cards)},
        )
        response = ChatResponse(
            conversation_id=request.conversation_id,
            kind="session_plan",
            text=text,
            cards=cards,
            catalog_version=self.catalog.version,
            trace=[*state["trace"], step],
            plan_preview=preview,
        )
        writer = get_stream_writer()
        writer({"type": "trace", "step": step.model_dump(mode="json")})
        writer({"type": "text_delta", "delta": text})
        if cards:
            writer(
                {
                    "type": "exercise_cards",
                    "kind": response.kind,
                    "result_id": str(response.result_id),
                    "cards": [card.model_dump(mode="json", by_alias=True) for card in cards],
                }
            )
        writer({"type": "done", "response": response.model_dump(mode="json", by_alias=True)})
        return {"response": response}

    def _plan_progress_writer(self):
        writer = get_stream_writer()
        started = False

        def emit(text):
            nonlocal started
            prefix = "\n\n" if started else "**方案构思 · 生成中，尚未确认**\n\n"
            writer({"type": "text_delta", "delta": prefix + text})
            started = True

        return emit

    async def _stream_review_response(self, request, writer):
        from fitness_bot.review_streaming import DEFAULT_KEEP, DEFAULT_NEXT, recover_review
        from fitness_bot.training_schemas import ReviewDraft

        facts = request.context.training_review or {}
        pending = {
            "facts": facts,
            "findings": [],
            "keep": [],
            "next_adjustments": [],
            "question": None,
        }
        text = render_review(pending)
        writer({"type": "text_delta", "delta": text})
        review = None
        if hasattr(self.model, "stream_review"):
            findings = []
            async with aclosing(self.model.stream_review(request)) as events:
                async for event in events:
                    if event["type"] == "finding":
                        findings.append(event["finding"])
                        partial = validate_review(
                            ReviewDraft(
                                findings=findings, keep=DEFAULT_KEEP, next_adjustments=DEFAULT_NEXT
                            ),
                            facts,
                        )
                        partial.update(keep=[], next_adjustments=[])
                        updated = render_review(partial)
                        if not updated.startswith(text):
                            raise ValueError("Review stream changed previously shown text")
                        writer({"type": "text_delta", "delta": updated[len(text) :]})
                        text = updated
                    elif event["type"] == "result":
                        review = validate_review(event["draft"], facts)
                        review["omitted_findings"] = event.get("omitted_findings", 0)
        else:
            draft, omitted = recover_review(await self.model.review(request), facts)
            review = validate_review(draft, facts)
            review["omitted_findings"] = omitted
        if review is None:
            raise ValueError("Review stream ended without a result")
        updated = render_review(review)
        if not updated.startswith(text):
            raise ValueError("Final review changed previously shown text")
        if updated[len(text) :]:
            writer({"type": "text_delta", "delta": updated[len(text) :]})
        return review, updated

    async def _training_response(self, state):
        writer = get_stream_writer()
        writer({"type": "status", "stage": "responding"})
        request, plan = state["request"], state["plan"]
        review = session_plan = None
        presentation = {}
        if plan.kind == "review":
            if state.get("presentation"):
                from fitness_bot.report_delivery import deliver_review

                review, text, presentation = await deliver_review(
                    self.model, request, state["presentation"], writer
                )
            else:
                review, text = await self._stream_review_response(request, writer)
        elif plan_question(request):
            if hasattr(self.model, "session_preview"):
                return await self._preview_response(state)
            return await self._respond(state, training_discussion=True)
        else:
            parent = None
            if plan.parent_plan_id:
                if self.training_store is None:
                    raise ValueError("Plan storage required for a successor")
                parent = await self.training_store.plan(request._user_id, plan.parent_plan_id)
                request.context.session_plans = [
                    parent,
                    *[
                        p
                        for p in request.context.session_plans
                        if p["plan"]["id"] != parent["plan"]["id"]
                    ],
                ][:5]
            if hasattr(self.model, "session_plan_with_progress"):
                draft = await self.model.session_plan_with_progress(
                    request, self.catalog, on_progress=self._plan_progress_writer()
                )
            else:
                draft = await self.model.session_plan(request, self.catalog)
            target_date = plan.session_inputs.target_date if plan.session_inputs else None
            session_plan = validate_session_plan(
                draft,
                request,
                self.catalog,
                target_date=target_date,
                original_plan=parent["plan"] if parent else None,
            )
            session_plan["parent_plan_id"] = None
            if parent:
                session_plan["parent_plan_id"] = str(plan.parent_plan_id)
                session_plan["version"] = parent["plan"]["version"] + 1
                session_plan["parent_revision"] = parent["revision"]
            session_plan["target_date"] = (
                plan.session_inputs.target_date.isoformat()
                if plan.session_inputs and plan.session_inputs.target_date
                else None
            )
            text = render_session(session_plan)
        step = ProcessingStep(
            stage="responding",
            title="训练决策已生成",
            summary="统计事实与推断分别呈现；完整方案通过目录、顺序和时间检查。"
            if review or session_plan
            else "需要补充关键条件后再生成完整方案。",
            data={"review": review is not None, "session_plan": session_plan is not None},
        )
        trace = [*state["trace"], step]
        writer({"type": "trace", "step": step.model_dump(mode="json")})
        # Full plans must commit against current load evidence before presentation.
        if session_plan:
            writer({"type": "decision_text", "delta": text})
        response = ChatResponse(
            conversation_id=request.conversation_id,
            kind=plan.kind,
            text=text,
            cards=[],
            catalog_version=self.catalog.version,
            trace=trace,
            review=review,
            **presentation,
            session_plan=session_plan,
            suggestion_kind="session_plan" if session_plan else None,
        )
        writer({"type": "done", "response": response.model_dump(mode="json", by_alias=True)})
        return {"response": response}

    async def stream(self, request: ChatRequest) -> AsyncIterator[dict]:
        runner = self
        if self.custom_store and request._user_id:
            runner = CompanionAgent(
                await self.custom_store.catalog_for(request._user_id), self.model
            )
            runner.training_store = self.training_store
        queue: asyncio.Queue[dict | Exception | None] = asyncio.Queue(maxsize=32)

        async def produce():
            try:
                async with aclosing(
                    runner.graph.astream({"request": request}, stream_mode="custom")
                ) as events:
                    async for event in events:
                        await queue.put(event)
            except Exception as exc:
                await queue.put(exc)
            else:
                await queue.put(None)

        # Starlette's AnyIO cancellation repeats at every await. Give LangGraph a
        # single asyncio cancellation so it can finish cancelling its model tasks.
        producer = asyncio.create_task(produce())
        try:
            while (event := await queue.get()) is not None:
                if isinstance(event, Exception):
                    raise event
                yield event
        finally:
            producer.cancel()
            with anyio.CancelScope(shield=True), suppress(asyncio.CancelledError):
                await producer
