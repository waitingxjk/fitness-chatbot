"""Conversation can progress before a quantified session is ready to generate."""

import json

from fastapi.testclient import TestClient
from test_training_review import upload

from fitness_bot.agent import CompanionAgent
from fitness_bot.app import create_app
from fitness_bot.catalog import Catalog
from fitness_bot.config import Settings
from fitness_bot.schemas import SearchPlan


def test_missing_profile_uses_history_aware_dialogue_instead_of_repeating_gate():
    class DiscussionModel:
        def __init__(self):
            self.requests = []

        async def plan(self, request, reference):
            # A question about the method stays a discussion; an actual plan
            # request now generates a complete candidate with defaults.
            return SearchPlan(kind="chat")

        async def stream_reply(self, request, plan, cards, previous):
            self.requests.append(request)
            assert request.context.training_profile["profile"]["goal"] is None
            assert request.context.training_review["snapshot_count"] == 1
            assert cards == []
            if len(self.requests) == 1:
                yield "我会先看已同步训练的节奏、动作分布和近期表现，再讨论明天的主题。"
            else:
                assert request.history[-1].role == "assistant"
                yield (
                    "先看记录：当前窗口只有卧推记录，能看出近期有推类训练；不足以确定你的长期目标。"
                )

        async def session_plan(self, *args):
            raise AssertionError("Incomplete profile must not generate a quantified plan")

    model = DiscussionModel()
    with TestClient(
        create_app(Settings(_env_file=None), CompanionAgent(Catalog.load(), model))
    ) as client:
        upload(client, "synthetic-discussion", "2026-09-07T01:00:00Z")
        first = client.post(
            "/v1/chat", json={"message": "如果希望你计划明天训练，你会按什么思路推荐？"}
        )
        first_done = json.loads(first.text.splitlines()[-1])
        assert len(model.requests) == 1
        assert "我会先看" in first_done["response"]["text"]
        second = client.post(
            "/v1/chat",
            json={
                "conversation_id": first_done["conversation_id"],
                "message": "你最好不要直接问我，先根据历史训练分析，再决定要不要问。",
            },
        )
        second_done = json.loads(second.text.splitlines()[-1])
        assert len(model.requests) == 2
        assert "先看记录" in second_done["response"]["text"]
        assert second_done["response"]["session_plan"] is None
        assert client.get("/v1/training/plans").json() == []
        assert client.get("/v1/training/profile").json()["version"] == 0


def test_accepting_previous_offer_with_target_date_streams_successfully():
    from datetime import date

    from langchain_core.messages import AIMessageChunk

    from fitness_bot.model import OpenAICompanionModel

    class Planner:
        def __init__(self):
            self.calls = 0

        async def ainvoke(self, messages):
            self.calls += 1
            return (
                SearchPlan(kind="chat")
                if self.calls == 1
                else SearchPlan(
                    kind="session_plan", session_inputs={"target_date": date(2026, 9, 9)}
                )
            )

    class ReplyModel:
        async def astream(self, messages):
            payload = json.loads(messages[-1].content)
            if payload["retrieval"]["kind"] == "session_plan":
                assert payload["retrieval"]["session_inputs"]["target_date"] == "2026-09-09"
                yield AIMessageChunk(content="可以，先根据已有历史讨论明天的安排。")
            else:
                yield AIMessageChunk(content="我可以继续帮你分析明天的训练安排。")

    model = OpenAICompanionModel(Settings(_env_file=None, openai_api_key="test-key"))

    async def full_candidate(request, catalog, **kwargs):
        from test_training_decision import draft

        from fitness_bot.training_decision import evidence_index
        from fitness_bot.training_schemas import SessionDraft

        value = draft()
        value["evidence_ids"] = [
            k for k in value["evidence_ids"] if k in evidence_index(request.context.training_review)
        ]
        return SessionDraft.model_validate(value)

    model.session_plan = full_candidate
    model.planner = Planner()
    model.llm = ReplyModel()
    with TestClient(
        create_app(Settings(_env_file=None), CompanionAgent(Catalog.load(), model))
    ) as client:
        first = client.post("/v1/chat", json={"message": "你会怎样安排明天训练？"})
        done = json.loads(first.text.splitlines()[-1])
        response = client.post(
            "/v1/chat", json={"conversation_id": done["conversation_id"], "message": "可以"}
        )
        events = [json.loads(line) for line in response.text.splitlines()]
        assert events[-1]["type"] == "done"
        assert any(event["type"] == "text_delta" for event in events)
        assert events[-1]["response"]["session_plan"]["target_date"] == "2026-09-09"
        assert events[-1]["response"]["session_plan"]["status"] == "proposed"
        assert events[-1]["response"]["plan_preview"] is None
