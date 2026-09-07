"""A bounded LangGraph: understand -> retrieve -> respond."""

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


class AgentState(TypedDict, total=False):
    request: ChatRequest
    plan: SearchPlan
    cards: list[ExerciseCard]
    response: ChatResponse
    trace: list[ProcessingStep]
    previous_results: list[Exercise]


class CompanionAgent:
    def __init__(self, catalog: Catalog, model: CompanionModel):
        self.catalog = catalog
        self.model = model
        graph = StateGraph(AgentState)
        graph.add_node("understand", self._understand)
        graph.add_node("retrieve", self._retrieve)
        graph.add_node("respond", self._respond)
        graph.add_edge(START, "understand")
        graph.add_edge("understand", "retrieve")
        graph.add_edge("retrieve", "respond")
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
        step = ProcessingStep(
            stage="understanding",
            title="请求解析完成",
            summary={
                "chat": "识别为普通对话",
                "search": "识别为动作搜索",
                "recommendation": "识别为动作推荐",
            }[plan.kind],
            data={**plan.model_dump(mode="json"), "image_count": len(request.images)},
        )
        get_stream_writer()({"type": "trace", "step": step.model_dump(mode="json")})
        return {"plan": plan, "trace": [step], "previous_results": previous_results}

    async def _retrieve(self, state: AgentState):
        plan, request = state["plan"], state["request"]
        get_stream_writer()({"type": "status", "stage": "retrieving"})
        limit = 2 if plan.kind == "search" else 3
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

    async def _respond(self, state: AgentState):
        writer = get_stream_writer()
        step = ProcessingStep(
            stage="responding",
            title="开始生成回复",
            summary=f"结合会话历史和 {len(state['cards'])} 张动作卡生成文本",
            data={
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

    async def stream(self, request: ChatRequest) -> AsyncIterator[dict]:
        queue: asyncio.Queue[dict | Exception | None] = asyncio.Queue(maxsize=32)

        async def produce():
            try:
                async with aclosing(
                    self.graph.astream({"request": request}, stream_mode="custom")
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
