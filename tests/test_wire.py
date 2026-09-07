"""Use a real local TCP connection to ensure the response is not buffered."""

import asyncio
import json
import socket

import httpx
import uvicorn

from fitness_bot.agent import CompanionAgent
from fitness_bot.app import create_app
from fitness_bot.catalog import Catalog
from fitness_bot.config import Settings
from fitness_bot.schemas import SearchPlan


async def test_http_emits_first_text_before_model_finishes():
    release = asyncio.Event()

    class GatedModel:
        async def plan(self, request, reference):
            return SearchPlan(kind="chat")

        async def stream_reply(self, request, plan, cards, previous_results):
            yield "第一段\n"
            await release.wait()
            yield "第二段"

    app = create_app(Settings(_env_file=None), CompanionAgent(Catalog.load(), GatedModel()))
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on"))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    port = sock.getsockname()[1]
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(5):
            while not server.started:
                await asyncio.sleep(0.01)
            async with httpx.AsyncClient(timeout=3) as client:
                async with client.stream(
                    "POST", f"http://127.0.0.1:{port}/v1/chat", json={"message": "你好"}
                ) as response:
                    assert response.status_code == 200
                    events = []
                    async for line in response.aiter_lines():
                        event = json.loads(line)
                        events.append(event)
                        if event["type"] == "text_delta" and event["delta"] == "第一段\n":
                            assert not release.is_set()
                            release.set()
                    assert release.is_set()
                    assert events[-1]["response"]["text"] == "第一段\n第二段"
    finally:
        release.set()
        server.should_exit = True
        await asyncio.wait_for(task, 5)
        sock.close()


async def test_disconnect_saves_partial_reply_and_allows_next_turn():
    from uuid import uuid4

    cancelled = asyncio.Event()

    class CancelModel:
        async def plan(self, request, reference):
            return SearchPlan(kind="chat")

        async def stream_reply(self, request, plan, cards, previous_results):
            if request.message == "继续":
                yield "可以继续。"
                return
            try:
                yield "部分回复"
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    app = create_app(Settings(_env_file=None), CompanionAgent(Catalog.load(), CancelModel()))
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on"))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    port = sock.getsockname()[1]
    task = asyncio.create_task(server.serve(sockets=[sock]))
    cid = str(uuid4())
    try:
        async with asyncio.timeout(5):
            while not server.started:
                await asyncio.sleep(0.01)
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=3) as client:
                async with client.stream(
                    "POST", "/v1/chat", json={"message": "停止测试", "conversation_id": cid}
                ) as response:
                    async for line in response.aiter_lines():
                        if json.loads(line)["type"] == "text_delta":
                            break
                await cancelled.wait()
                while True:
                    rows = (await client.get(f"/v1/conversations/{cid}/turns")).json()
                    if rows[0]["status"] == "cancelled":
                        break
                    await asyncio.sleep(0.01)
                assert rows[0]["assistant_text"] == "部分回复"
                reply = await client.post(
                    "/v1/chat", json={"message": "继续", "conversation_id": cid}
                )
                assert reply.status_code == 200
                assert json.loads(reply.text.splitlines()[-1])["response"]["text"] == "可以继续。"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 5)
        sock.close()
