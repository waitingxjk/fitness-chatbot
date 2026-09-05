import json

import pytest
from fastapi.testclient import TestClient

from fitness_bot.agent import CompanionAgent
from fitness_bot.app import create_app
from fitness_bot.catalog import Catalog
from fitness_bot.config import Settings
from fitness_bot.schemas import ChatRequest, SearchPlan


class FakeModel:
    def __init__(self, kind="search", fail=False):
        self.kind = kind
        self.fail = fail

    async def plan(self, request, terms):
        return SearchPlan(kind=self.kind, query="卧推", equipment="dumbbell")

    async def stream_reply(self, request, plan, cards):
        yield "找到以下动作。\n"
        if self.fail:
            raise RuntimeError("secret-provider-detail")
        yield "可以查看动作详情。"


@pytest.fixture
def catalog():
    return Catalog.load()


def test_full_catalog_and_search(catalog):
    assert len(catalog.exercises) == 1324
    assert len({e.id for e in catalog.exercises}) == 1324
    assert catalog.search(SearchPlan(query="不存在的动作123")) == []
    matches = catalog.search(SearchPlan(query="卧推", equipment="dumbbell"))
    assert matches and all(e.equipment_code == "dumbbell" for e in matches)
    assert catalog.search(SearchPlan(query="罗马尼亚硬拉"))
    assert catalog.search(SearchPlan(body_part="背部", equipment="哑铃"))
    assert catalog.search(SearchPlan(equipment="不存在器械")) == []


@pytest.mark.parametrize("kind,limit", [("search", 2), ("recommendation", 3)])
@pytest.mark.parametrize(
    "status,can_add", [("active", True), ("paused", True), ("completed", False)]
)
async def test_graph_stream_cards_and_context(catalog, kind, limit, status, can_add):
    agent = CompanionAgent(catalog, FakeModel(kind))
    request = ChatRequest(
        message="找哑铃卧推", context={"active_workout": {"id": "w1", "status": status}}
    )
    events = [event async for event in agent.stream(request)]
    assert [e["type"] for e in events].count("text_delta") == 2
    done = events[-1]
    assert done["type"] == "done"
    cards = done["response"]["cards"]
    assert len(cards) == limit
    assert all(c["can_add_to_active_workout"] is can_add for c in cards)
    assert all(catalog.get(c["exercise"]["id"]) for c in cards)
    assert all("训练历史不足" in c["context"] for c in cards) if kind == "recommendation" else True
    assert done["response"]["text"] == "找到以下动作。\n可以查看动作详情。"


def test_http_stream_and_validation(catalog):
    app = create_app(Settings(_env_file=None), CompanionAgent(catalog, FakeModel()))
    with TestClient(app) as client:
        assert client.get("/health").json()["catalog_count"] == 1324
        response = client.post("/v1/chat", json={"message": "找哑铃卧推"})
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/x-ndjson")
        events = [json.loads(line) for line in response.iter_lines()]
        assert events[-1]["type"] == "done"
        assert len({e["request_id"] for e in events}) == 1
        assert [e["sequence"] for e in events] == list(range(1, len(events) + 1))
        assert client.post("/v1/chat", json={"message": "  "}).status_code == 422
        assert (
            client.post(
                "/v1/chat", json={"message": "x", "history": [{"role": "system", "text": "x"}]}
            ).status_code
            == 422
        )
        assert client.get("/v1/exercises/0001").json()["id"] == "0001"
        assert client.get("/v1/exercises/missing").status_code == 404


def test_stream_failure_is_terminal_and_sanitized(catalog):
    app = create_app(Settings(_env_file=None), CompanionAgent(catalog, FakeModel(fail=True)))
    with TestClient(app) as client:
        response = client.post("/v1/chat", json={"message": "卧推"})
        events = [json.loads(line) for line in response.iter_lines()]
        assert events[-1]["type"] == "error"
        assert not any(e["type"] == "done" for e in events)
        assert "secret-provider-detail" not in response.text


def test_missing_model_config_returns_503(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with TestClient(create_app(Settings(_env_file=None, openai_api_key=None))) as client:
        assert client.get("/health").status_code == 200
        assert client.post("/v1/chat", json={"message": "你好"}).status_code == 503
