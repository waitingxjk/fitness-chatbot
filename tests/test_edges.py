import asyncio
import shutil

import pytest
from fastapi.testclient import TestClient

from fitness_bot.agent import CompanionAgent
from fitness_bot.app import create_app
from fitness_bot.catalog import DATA_DIR, Catalog
from fitness_bot.config import Settings
from fitness_bot.schemas import ChatRequest, SearchPlan


class PlannedModel:
    def __init__(self, plan, delay=0):
        self.search_plan = plan
        self.delay = delay
        self.reference = None
        self.request = None

    async def plan(self, request, reference):
        self.reference = reference
        self.request = request
        return self.search_plan

    async def stream_reply(self, request, plan, cards):
        await asyncio.sleep(self.delay)
        yield "请告诉我你想训练的部位。"


async def test_no_results_and_chat_do_not_fabricate_cards():
    for plan in [SearchPlan(query="未知动作xyz"), SearchPlan(kind="chat", query="卧推")]:
        agent = CompanionAgent(Catalog.load(), PlannedModel(plan))
        events = [e async for e in agent.stream(ChatRequest(message="测试"))]
        assert events[-1]["response"]["cards"] == []
        assert not any(e["type"] == "exercise_cards" for e in events)


async def test_recommendation_respects_current_workout_and_available_equipment():
    catalog = Catalog.load()
    plan = SearchPlan(kind="recommendation", body_part="chest")
    existing = catalog.search(SearchPlan(equipment="dumbbell", body_part="chest"))[0].id
    request = ChatRequest(
        message="推荐胸部动作",
        context={
            "active_workout": {"id": "w1", "exercise_ids": [existing]},
            "available_equipment": ["哑铃"],
        },
    )
    events = [e async for e in CompanionAgent(catalog, PlannedModel(plan)).stream(request)]
    cards = events[-1]["response"]["cards"]
    assert cards and all(c["exercise"]["equipmentCode"] == "dumbbell" for c in cards)
    assert all(c["exercise"]["id"] != existing for c in cards)


async def test_followup_context_is_forwarded_without_cross_request_memory():
    model = PlannedModel(SearchPlan(kind="chat"))
    agent = CompanionAgent(Catalog.load(), model)
    request = ChatRequest(
        message="第二个换成无器械",
        previous_result_ids=["0001", "0002"],
        history=[{"role": "user", "text": "找核心动作"}],
    )
    _ = [e async for e in agent.stream(request)]
    assert [e["id"] for e in model.reference["previous_results"]] == ["0001", "0002"]
    assert model.request.history[0].text == "找核心动作"
    _ = [e async for e in agent.stream(ChatRequest(message="新对话"))]
    assert model.request.history == []
    assert model.reference["previous_results"] == []


async def test_planner_only_receives_terms_usable_as_primary_filters():
    catalog = Catalog.load()
    model = PlannedModel(SearchPlan(kind="chat"))
    _ = [e async for e in CompanionAgent(catalog, model).stream(ChatRequest(message="核心动作"))]
    muscles = {t["code"] for t in model.reference["terms"] if t["type"] == "muscle"}
    actual_primary = {e.target_muscle_code for e in catalog.exercises if e.is_active}
    assert muscles == actual_primary
    assert "core" not in muscles
    assert "abs" in muscles


def test_timeout_is_terminal():
    agent = CompanionAgent(Catalog.load(), PlannedModel(SearchPlan(kind="chat"), delay=1))
    settings = Settings(_env_file=None, chat_timeout_seconds=0.03)
    with TestClient(create_app(settings, agent)) as client:
        response = client.post("/v1/chat", json={"message": "你好"})
        assert '"code": "timeout"' in response.text
        assert '"type": "done"' not in response.text


def test_catalog_integrity(tmp_path):
    shutil.copytree(DATA_DIR, tmp_path, dirs_exist_ok=True)
    (tmp_path / "exercises_zh_hans.json").write_text("[]")
    with pytest.raises(ValueError, match="checksum"):
        Catalog.load(tmp_path)


def test_legacy_base_url_and_secret_redaction(monkeypatch):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setenv("OPENAI_API_BASE", "https://example.test/v1")
    settings = Settings(_env_file=None, openai_api_key="secret-value")
    assert settings.openai_base_url == "https://example.test/v1"
    assert "secret-value" not in repr(settings)
