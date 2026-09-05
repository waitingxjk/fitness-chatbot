"""A bounded LangGraph: understand -> retrieve -> respond."""

from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from fitness_bot.catalog import Catalog
from fitness_bot.model import CompanionModel
from fitness_bot.schemas import ChatRequest, ChatResponse, ExerciseCard, SearchPlan


class AgentState(TypedDict, total=False):
    request: ChatRequest
    plan: SearchPlan
    cards: list[ExerciseCard]
    response: ChatResponse


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
        reference = {
            "terms": self.catalog.terms,
            "previous_results": [
                e.model_dump(mode="json", by_alias=True)
                for item in request.previous_result_ids
                if (e := self.catalog.get(item))
            ],
            "history_exercise_names": {
                item: e.display_name_zh_hans for item in ids if (e := self.catalog.get(item))
            },
        }
        return {"plan": await self.model.plan(request, reference)}

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
        return {"cards": cards}

    async def _respond(self, state: AgentState):
        writer = get_stream_writer()
        text = ""
        async for delta in self.model.stream_reply(state["request"], state["plan"], state["cards"]):
            text += delta
            writer({"type": "text_delta", "delta": delta})
        response = ChatResponse(
            conversation_id=state["request"].conversation_id,
            kind=state["plan"].kind,
            text=text,
            cards=state["cards"],
            catalog_version=self.catalog.version,
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
        async with aclosing(
            self.graph.astream({"request": request}, stream_mode="custom")
        ) as events:
            async for event in events:
                yield event
