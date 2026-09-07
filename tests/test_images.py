import json
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessageChunk
from PIL import Image

from fitness_bot.agent import CompanionAgent
from fitness_bot.app import create_app
from fitness_bot.catalog import Catalog
from fitness_bot.config import Settings
from fitness_bot.model import OpenAICompanionModel
from fitness_bot.schemas import ChatRequest, SearchPlan


def picture(fmt="PNG"):
    out = BytesIO()
    Image.new("RGB", (40, 30), "red").save(out, format=fmt)
    return out.getvalue()


class ImageModel:
    def __init__(self):
        self.requests = []

    async def plan(self, request, reference):
        self.requests.append(request)
        return SearchPlan(kind="chat")

    async def stream_reply(self, request, plan, cards, previous_results):
        yield "收到图片。"


def test_image_upload_chat_restart_and_followup():
    settings = Settings(_env_file=None)
    model = ImageModel()
    with TestClient(create_app(settings, CompanionAgent(Catalog.load(), model))) as client:
        uploaded = client.post(
            "/v1/images", content=picture(), headers={"Content-Type": "image/png"}
        )
        assert uploaded.status_code == 201
        asset = uploaded.json()
        assert client.get(asset["url"]).content == picture()
        response = client.post("/v1/chat", json={"images": [asset["id"]]})
        assert response.status_code == 200
        done = json.loads(response.text.splitlines()[-1])
        assert done["type"] == "done"
        assert model.requests[0].images == [asset["id"]]
        cid = done["conversation_id"]
        rid = done["request_id"]
    with TestClient(create_app(settings, CompanionAgent(Catalog.load(), model))) as client:
        rows = client.get(f"/v1/conversations/{cid}/turns").json()
        assert rows[0]["images"][0]["id"] == asset["id"]
        assert client.get(rows[0]["images"][0]["url"]).content == picture()
        replay = client.post(
            "/v1/chat", json={"images": [asset["id"]], "conversation_id": cid, "request_id": rid}
        )
        assert json.loads(replay.text)["replayed"] is True
        client.post("/v1/chat", json={"message": "图中是什么颜色", "conversation_id": cid})
        assert model.requests[-1].history[0].images == [asset["id"]]
        assert model.requests[-1].history[0].text == ""
        client.post("/v1/chat", json={"message": "新会话"})
        assert model.requests[-1].history == []


def test_upload_rejects_invalid_images_and_limits():
    with TestClient(
        create_app(Settings(_env_file=None), CompanionAgent(Catalog.load(), ImageModel()))
    ) as client:
        assert (
            client.post(
                "/v1/images", content=b"<svg/>", headers={"Content-Type": "image/svg+xml"}
            ).status_code
            == 415
        )
        assert (
            client.post(
                "/v1/images", content=b"not png", headers={"Content-Type": "image/png"}
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/v1/images", content=picture(), headers={"Content-Type": "image/jpeg"}
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/v1/images",
                content=b"x" * (5 * 1024 * 1024 + 1),
                headers={"Content-Type": "image/png"},
            ).status_code
            == 413
        )
        assert client.post("/v1/chat", json={"images": ["0" * 64]}).status_code == 422
        assert client.post("/v1/chat", json={"message": " ", "images": []}).status_code == 422
        assert client.post("/v1/chat", json={"images": ["0" * 64] * 4}).status_code == 422
        assert client.get("/v1/images/" + "0" * 64).status_code == 404
        assert client.post("/v1/chat", json={"images": ["../../.env"]}).status_code == 422


async def test_planner_and_reply_use_native_image_blocks(tmp_path):
    from fitness_bot.images import ImageStore
    from fitness_bot.schemas import Message

    images = ImageStore(tmp_path)
    asset = images.save(picture(), "image/png")
    model = OpenAICompanionModel(
        Settings(_env_file=None, openai_api_key="test", image_directory=tmp_path)
    )

    class Planner:
        async def ainvoke(self, messages):
            self.messages = messages
            return SearchPlan(kind="chat")

    class Reply:
        async def astream(self, messages):
            self.messages = messages
            yield AIMessageChunk(content="红色")

    model.planner, model.llm = Planner(), Reply()
    request = ChatRequest(
        message="比较两张图",
        images=[asset["id"]],
        history=[Message(role="user", text="上张图", images=[asset["id"]])],
    )
    plan = await model.plan(request, {})
    assert [chunk async for chunk in model.stream_reply(request, plan, [], [])] == ["红色"]
    for messages in [model.planner.messages, model.llm.messages]:
        blocks = [
            block
            for message in messages
            if isinstance(message.content, list)
            for block in message.content
        ]
        visual = [block for block in blocks if block["type"] == "image_url"]
        assert len(visual) == 2
        assert all(
            block["image_url"]["url"].startswith("data:image/png;base64,") for block in visual
        )
        assert all("base64" not in block["text"] for block in blocks if block["type"] == "text")


def test_supported_formats_deduplicate_and_reject_animation(tmp_path, monkeypatch):
    import fitness_bot.images as module

    store = module.ImageStore(tmp_path)
    for fmt, mime in [("JPEG", "image/jpeg"), ("PNG", "image/png"), ("WEBP", "image/webp")]:
        asset = store.save(picture(fmt), mime)
        assert store.save(picture(fmt), mime)["id"] == asset["id"]
        assert asset["mime_type"] == mime
    animated = BytesIO()
    Image.new("RGB", (4, 4), "red").save(
        animated, format="PNG", save_all=True, append_images=[Image.new("RGB", (4, 4), "blue")]
    )
    with pytest.raises(ValueError, match="静态"):
        store.save(animated.getvalue(), "image/png")
    monkeypatch.setattr(module, "MAX_IMAGE_PIXELS", 100)
    with pytest.raises(ValueError, match="像素"):
        store.save(picture(), "image/png")


async def test_model_limits_historical_images_without_mutating_history(tmp_path):
    from fitness_bot.images import ImageStore
    from fitness_bot.schemas import Message

    asset = ImageStore(tmp_path).save(picture(), "image/png")
    model = OpenAICompanionModel(
        Settings(_env_file=None, openai_api_key="test", image_directory=tmp_path)
    )
    request = ChatRequest(
        message="继续",
        history=[Message(role="user", text=str(i), images=[asset["id"]]) for i in range(8)],
    )
    history = await model._history(request)
    assert "已从模型上下文省略" in history[0].content
    assert "已从模型上下文省略" in history[1].content
    assert all(isinstance(message.content, list) for message in history[2:])
    assert sum(len(message.images) for message in request.history) == 8


def test_images_remain_in_failed_turn():
    class FailedModel(ImageModel):
        async def stream_reply(self, request, plan, cards, previous_results):
            yield "部分回复"
            raise RuntimeError("provider failure")

    with TestClient(
        create_app(Settings(_env_file=None), CompanionAgent(Catalog.load(), FailedModel()))
    ) as client:
        asset = client.post(
            "/v1/images", content=picture(), headers={"Content-Type": "image/png"}
        ).json()
        response = client.post("/v1/chat", json={"images": [asset["id"]]})
        error = json.loads(response.text.splitlines()[-1])
        assert error["type"] == "error"
        rows = client.get(f"/v1/conversations/{error['conversation_id']}/turns").json()
        assert rows[0]["status"] == "failed"
        assert rows[0]["images"][0]["id"] == asset["id"]
        assert rows[0]["assistant_text"] == "部分回复"
