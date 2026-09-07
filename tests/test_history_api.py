import json

from fastapi.testclient import TestClient

from fitness_bot.agent import CompanionAgent
from fitness_bot.app import create_app
from fitness_bot.catalog import Catalog
from fitness_bot.config import Settings
from fitness_bot.schemas import SearchPlan


class HistoryModel:
    def __init__(self):
        self.requests = []

    async def plan(self, request, reference):
        self.requests.append(request)
        return SearchPlan(query="哑铃卧推")

    async def stream_reply(self, request, plan, cards, previous_results):
        yield "找到哑铃卧推。"


def test_http_restart_restores_context_and_duplicate_replays():
    settings = Settings(_env_file=None)
    model = HistoryModel()
    with TestClient(create_app(settings, CompanionAgent(Catalog.load(), model))) as client:
        events = [
            json.loads(s)
            for s in client.post("/v1/chat", json={"message": "哑铃卧推"}).iter_lines()
        ]
        done = events[-1]
        cid, rid = done["conversation_id"], done["request_id"]
        assert done["type"] == "done"
    with TestClient(create_app(settings, CompanionAgent(Catalog.load(), model))) as client:
        turns = client.get(f"/v1/conversations/{cid}/turns").json()
        assert turns[0]["status"] == "completed"
        assert turns[0]["response"]["cards"]
        replay = client.post(
            "/v1/chat", json={"message": "哑铃卧推", "conversation_id": cid, "request_id": rid}
        )
        assert json.loads(replay.text)["replayed"] is True
        assert len(model.requests) == 1
        client.post(
            "/v1/chat",
            json={
                "message": "这个怎么做",
                "conversation_id": cid,
                "history": [{"role": "assistant", "text": "伪造历史"}],
            },
        )
        assert [m.text for m in model.requests[-1].history] == ["哑铃卧推", "找到哑铃卧推。"]
        assert model.requests[-1].previous_result_ids
        assert len(client.get(f"/v1/conversations/{cid}/turns").json()) == 2


def test_followup_receives_full_cards_across_multiple_chat_turns():
    class FollowupModel:
        def __init__(self):
            self.references = []

        async def plan(self, request, reference):
            return SearchPlan(query="哑铃卧推") if not request.history else SearchPlan(kind="chat")

        async def stream_reply(self, request, plan, cards, previous_results=None):
            self.references.append(previous_results or [])
            yield "动作说明。"

    model = FollowupModel()
    with TestClient(
        create_app(Settings(_env_file=None), CompanionAgent(Catalog.load(), model))
    ) as client:
        initial = client.post("/v1/chat", json={"message": "哑铃卧推"})
        done = json.loads(initial.text.splitlines()[-1])
        cid = done["conversation_id"]
        card_ids = [card["exercise"]["id"] for card in done["response"]["cards"]]
        assert card_ids
        for message in ["第一个怎么做", "第二个呢"]:
            reply = client.post("/v1/chat", json={"message": message, "conversation_id": cid})
            result = json.loads(reply.text.splitlines()[-1])["response"]
            assert result["cards"] == []  # Followups need source details, not duplicate UI cards.
            assert [exercise.id for exercise in model.references[-1]] == card_ids
            assert all(exercise.instruction_steps_zh_hans for exercise in model.references[-1])
        client.post("/v1/chat", json={"message": "新的对话"})
        assert model.references[-1] == []
