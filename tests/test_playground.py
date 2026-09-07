import json

from fastapi.testclient import TestClient

from fitness_bot.agent import CompanionAgent
from fitness_bot.app import create_app
from fitness_bot.catalog import Catalog
from fitness_bot.config import Settings
from fitness_bot.schemas import SearchPlan


class Model:
    async def plan(self, request, reference):
        return SearchPlan(query="哑铃卧推")

    async def stream_reply(self, request, plan, cards, previous_results):
        yield "找到动作。"


def test_static_playground_and_persisted_observable_trace():
    with TestClient(
        create_app(Settings(_env_file=None), CompanionAgent(Catalog.load(), Model()))
    ) as client:
        page = client.get("/playground/")
        assert page.status_code == 200
        assert "练伴" in page.text
        assert client.get("/playground/app.js").status_code == 200
        events = [
            json.loads(line)
            for line in client.post("/v1/chat", json={"message": "哑铃卧推"}).iter_lines()
        ]
        trace = [e["step"] for e in events if e["type"] == "trace"]
        assert [s["stage"] for s in trace] == ["understanding", "retrieving", "responding"]
        assert trace[0]["data"]["query"] == "哑铃卧推"
        assert trace[1]["data"]["exercise_ids"]
        response = events[-1]["response"]
        assert response["trace"] == trace
        rows = client.get(f"/v1/conversations/{response['conversation_id']}/turns").json()
        assert rows[0]["response"]["trace"] == trace
