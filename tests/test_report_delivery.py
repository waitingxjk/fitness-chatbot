import json
from uuid import uuid4

from fastapi.testclient import TestClient
from test_presentation import outline
from test_training_review import upload

from fitness_bot.agent import CompanionAgent
from fitness_bot.app import create_app
from fitness_bot.catalog import Catalog
from fitness_bot.config import Settings
from fitness_bot.schemas import SearchPlan


class ComposedModel:
    def __init__(self, invalid=False, broken=False, bad_outline=False):
        self.calls = 0
        self.invalid, self.broken, self.bad_outline = invalid, broken, bad_outline

    async def plan(self, request, reference):
        return SearchPlan(kind="review")

    async def compose_review(self, request):
        self.calls += 1
        return {"outline": []} if self.bad_outline else outline()

    async def stream_composed_review(self, request, presentation):
        items = [b for b in presentation["outline"] if b["type"] == "markdown"]
        for index, item in enumerate(items):
            if self.broken and index:
                raise ValueError("Test interrupted generation")
            fact_id = next(
                key
                for key, slot in presentation["facts"].items()
                if slot["source_key"] == "recent.workouts"
            )
            content = (
                f"记录包含 {{{{fact:{fact_id}}}}}，可以先观察训练安排。"
                if index == 0
                else "## 部位分布\n\n结合目标关注重点安排。"
            )
            if self.invalid and index == 0:
                content = "记录包含 99999 次训练。"
            yield {"type": "block", "block": {"id": item["id"], "content": content}}
        yield {"type": "complete"}


def client_for(model):
    return TestClient(create_app(Settings(_env_file=None), CompanionAgent(Catalog.load(), model)))


def seed(client):
    upload(client, "one", "2025-01-01T01:00:00Z")
    upload(client, "two", "2025-01-03T01:00:00Z")


def events(client, payload):
    return [json.loads(line) for line in client.post("/v1/chat", json=payload).iter_lines()]


def test_http_composition_interleaves_blocks_and_persists_exact_ids_for_retry_and_history():
    model = ComposedModel()
    with client_for(model) as client:
        seed(client)
        payload = {
            "message": "分析历史",
            "request_id": str(uuid4()),
            "conversation_id": str(uuid4()),
        }
        received = events(client, payload)
        assert received[-1]["type"] == "done"
        result = received[-1]["response"]
        assert result["schema_version"] == 1
        assert [b["type"] for b in result["blocks"]] == [
            "markdown",
            "chart_ref",
            "markdown",
            "chart_ref",
        ]
        assert [b["id"] for b in result["blocks"]] == [b["id"] for b in result["outline"]]
        assert "".join(e["delta"] for e in received if e["type"] == "text_delta") == result["text"]
        assert "{{fact:" not in result["text"]
        assert "2 次训练" in result["text"]
        from fitness_bot.report_delivery import PartialReport

        partial = PartialReport()
        for event in received:
            partial.record(event)
        assert partial.report["facts"] == result["facts"]
        assert partial.report["blocks"] == result["blocks"]
        references = [
            part["fact_id"]
            for block in result["blocks"]
            for part in block.get("parts", [])
            if part["type"] == "fact_ref"
        ]
        assert references and all(key in result["facts"] for key in references)
        layout_at = next(i for i, e in enumerate(received) if e["type"] == "report_layout")
        block_at = next(i for i, e in enumerate(received) if e["type"] == "block_start")
        assert layout_at < block_at
        assert [
            e["type"] for e in received if e["type"] in {"block_start", "block_end", "block"}
        ] == ["block_start", "block_end", "block", "block_start", "block_end", "block"]
        reconstructed = {}
        for event in received:
            if event["type"] == "block_start":
                reconstructed[event["block"]["id"]] = ""
            if event["type"] == "block_delta":
                reconstructed[event["block_id"]] += event["delta"]
        assert reconstructed == {
            b["id"]: b["content"] for b in result["blocks"] if b["type"] == "markdown"
        }
        history = client.get(f"/v1/conversations/{result['conversation_id']}/turns").json()
        assert history[0]["response"] == result
        cached = events(client, payload)
        assert len(cached) == 1 and cached[0]["replayed"]
        assert cached[0]["response"] == result and model.calls == 1
        foreign = client.get(
            f"/v1/conversations/{result['conversation_id']}/turns",
            headers={"X-User-Id": "other-user"},
        )
        assert foreign.status_code == 404


def test_invalid_prose_is_replaced_without_losing_later_blocks_or_publishing_numbers():
    with client_for(ComposedModel(invalid=True)) as client:
        seed(client)
        received = events(client, {"message": "分析历史"})
        result = received[-1]["response"]
        assert "99999" not in json.dumps(received)
        assert result["review"]["omitted_findings"] == 1
        assert len(result["blocks"]) == 4
        assert "部位分布" in result["text"]


def test_broken_stream_preserves_partial_text_without_success():
    with client_for(ComposedModel(broken=True)) as client:
        seed(client)
        received = events(client, {"message": "分析历史"})
        assert received[-1]["type"] == "error"
        assert not any(e["type"] == "done" for e in received)
        cid = received[0]["conversation_id"]
        history = client.get(f"/v1/conversations/{cid}/turns").json()
        assert history[0]["status"] == "failed"
        assert "观察训练安排" in history[0]["assistant_text"]
        partial = history[0]["response"]
        assert partial["incomplete"] is True
        assert [b["type"] for b in partial["blocks"]] == ["markdown", "chart_ref", "markdown"]
        assert partial["blocks"][1]["artifact_id"] in partial["artifacts"]


def test_invalid_outline_falls_back_to_grounded_text_and_charts():
    with client_for(ComposedModel(bad_outline=True)) as client:
        seed(client)
        result = events(client, {"message": "分析历史"})[-1]["response"]
        assert len(result["blocks"]) == 6
        assert len(result["artifacts"]) == len(result["datasets"]) == 3
        assert any(step["data"].get("layout_fallback") for step in result["trace"])


def test_disconnect_between_block_delta_and_legacy_delta_keeps_text_consistent():
    from fitness_bot.report_delivery import PartialReport

    partial = PartialReport()
    partial.record(
        {
            "type": "report_layout",
            "schema_version": 1,
            "outline": [],
            "artifacts": {},
            "datasets": {},
        }
    )
    partial.record({"type": "block_start", "block": {"id": "a", "type": "markdown", "content": ""}})
    partial.record({"type": "block_delta", "block_id": "a", "delta": "已显示的文字"})
    assert partial.text("") == "已显示的文字"
    response = partial.response("", "conversation")
    assert response["text"] == response["blocks"][0]["content"] == "已显示的文字"


class RepairableModel(ComposedModel):
    def __init__(self, fails=False):
        super().__init__(invalid=True)
        self.repairs = []
        self.fails = fails

    async def repair_review_block(self, request, expected, raw, reason):
        self.repairs.append((expected["id"], reason))
        fact_id = next(
            key
            for key, slot in expected["facts"].items()
            if slot["source_key"] == "recent.workouts"
        )
        return {
            "id": expected["id"],
            "content": "仍有 99999 次。"
            if self.fails
            else f"记录包含 {{{{fact:{fact_id}}}}}，重点关注安排。",
        }


def test_invalid_block_is_repaired_once_before_publication():
    model = RepairableModel()
    with client_for(model) as client:
        seed(client)
        received = events(client, {"message": "分析历史"})
        result = received[-1]["response"]
        assert len(model.repairs) == 1
        assert result["review"]["omitted_findings"] == 0
        assert result["review"]["repaired_findings"] == 1
        assert "99999" not in json.dumps(received)
        assert "重点关注安排" in result["text"]


def test_every_block_invalid_is_failed_with_preserved_charts():
    class InvalidModel(ComposedModel):
        async def stream_composed_review(self, request, presentation):
            for item in presentation["outline"]:
                if item["type"] == "markdown":
                    yield {
                        "type": "block",
                        "block": {"id": item["id"], "content": "训练了 99999 次。"},
                    }
            yield {"type": "complete"}

    with client_for(InvalidModel()) as client:
        seed(client)
        received = events(client, {"message": "分析历史"})
        assert received[-1]["type"] == "error"
        assert not any(e["type"] == "done" for e in received)
        cid = received[0]["conversation_id"]
        row = client.get(f"/v1/conversations/{cid}/turns").json()[0]
        assert row["status"] == "failed"
        assert row["response"]["artifacts"]
        assert row["response"]["incomplete"]


def test_repair_failure_is_bounded_and_does_not_lose_other_sections():
    model = RepairableModel(fails=True)
    with client_for(model) as client:
        seed(client)
        result = events(client, {"message": "分析历史"})[-1]["response"]
        assert len(model.repairs) == 1
        assert result["review"]["omitted_findings"] == 1
        assert "99999" not in result["text"]
        assert "部位分布" in result["text"]
