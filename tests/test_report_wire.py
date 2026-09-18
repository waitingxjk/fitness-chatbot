import asyncio
import json
import socket
from uuid import uuid4

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient
from test_report_delivery import ComposedModel, seed

from fitness_bot.agent import CompanionAgent
from fitness_bot.app import create_app
from fitness_bot.catalog import Catalog
from fitness_bot.config import Settings


@pytest.mark.parametrize("cancel", [False, True])
async def test_report_blocks_arrive_before_completion_and_cancel_keeps_chart_history(cancel):
    release, closed = asyncio.Event(), asyncio.Event()

    class GatedModel(ComposedModel):
        async def stream_composed_review(self, request, presentation):
            items = [b for b in presentation["outline"] if b["type"] == "markdown"]
            try:
                yield {
                    "type": "block",
                    "block": {"id": items[0]["id"], "content": "先关注训练安排。"},
                }
                await release.wait()
                yield {
                    "type": "block",
                    "block": {"id": items[1]["id"], "content": "再结合部位分布观察。"},
                }
                yield {"type": "complete"}
            finally:
                closed.set()

    app = create_app(Settings(_env_file=None), CompanionAgent(Catalog.load(), GatedModel()))
    with TestClient(app) as client:
        seed(client)
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on"))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    task = asyncio.create_task(server.serve(sockets=[sock]))
    cid = str(uuid4())
    try:
        async with asyncio.timeout(8):
            while not server.started:
                await asyncio.sleep(0.01)
            async with httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{sock.getsockname()[1]}"
            ) as client:
                received = []
                async with client.stream(
                    "POST", "/v1/chat", json={"message": "分析历史", "conversation_id": cid}
                ) as response:
                    async for line in response.aiter_lines():
                        event = json.loads(line)
                        received.append(event)
                        if event["type"] == "block" and not release.is_set():
                            assert not closed.is_set()
                            if cancel:
                                break
                            release.set()
                if cancel:
                    await closed.wait()
                    while True:
                        history = (await client.get(f"/v1/conversations/{cid}/turns")).json()
                        if history[0]["status"] == "cancelled":
                            break
                        await asyncio.sleep(0.01)
                    report = history[0]["response"]
                    assert report["incomplete"]
                    assert any(b["type"] == "chart_ref" for b in report["blocks"])
                    assert "先关注训练安排" in history[0]["assistant_text"]
                else:
                    assert received[-1]["type"] == "done"
                    report = received[-1]["response"]
                    assert len(report["blocks"]) == 4
                    assert report["text"].endswith("再结合部位分布观察。")
    finally:
        release.set()
        server.should_exit = True
        await asyncio.wait_for(task, 5)
        sock.close()
