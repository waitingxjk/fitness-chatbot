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


async def test_review_streams_validated_finding_before_model_finishes():
    from fitness_bot.training_schemas import ReviewDraft, ReviewFinding

    release = asyncio.Event()
    finding = ReviewFinding(
        conclusion="记录中有重复安排",
        evidence_ids=["recent.frequency"],
        comparison="对照所选训练",
        possible_explanation="可能与当时安排有关",
        suggested_action="结合反馈判断",
    )

    class GatedReview:
        async def plan(self, *args):
            return SearchPlan(kind="review")

        async def stream_review(self, request):
            yield {"type": "finding", "finding": finding}
            await release.wait()
            yield {
                "type": "result",
                "draft": ReviewDraft(
                    findings=[finding], keep=["保留记录"], next_adjustments=["结合感受"]
                ),
                "omitted_findings": 0,
            }

    app = create_app(Settings(_env_file=None), CompanionAgent(Catalog.load(), GatedReview()))
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on"))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(8):
            while not server.started:
                await asyncio.sleep(0.01)
            async with httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{sock.getsockname()[1]}", timeout=5
            ) as client:
                async with client.stream(
                    "POST", "/v1/chat", json={"message": "分析历史"}
                ) as response:
                    text, events = "", []
                    async for line in response.aiter_lines():
                        event = json.loads(line)
                        events.append(event)
                        if event["type"] == "text_delta":
                            text += event["delta"]
                            if "记录中有重复安排" in text and not release.is_set():
                                release.set()
                        if event["type"] == "done":
                            saved = (
                                await client.get(
                                    f"/v1/conversations/{event['conversation_id']}/turns"
                                )
                            ).json()
                            assert saved[0]["status"] == "completed"
                assert release.is_set()
                assert events[-1]["type"] == "done", events
                assert text == events[-1]["response"]["text"]
                assert text.count("记录中有重复安排") == 1
    finally:
        release.set()
        server.should_exit = True
        await asyncio.wait_for(task, 5)
        sock.close()


async def test_plan_streams_provisional_reasoning_then_persists_validated_result():
    from datetime import UTC, datetime

    from test_training_plans import PlanModel
    from test_training_profile import profile

    release = asyncio.Event()

    class GatedPlan(PlanModel):
        async def session_plan_with_progress(self, request, catalog, *, on_progress):
            on_progress("先保留重点动作，再补充互补安排")
            await release.wait()
            return await self.session_plan(request, catalog)

    app = create_app(Settings(_env_file=None), CompanionAgent(Catalog.load(), GatedPlan()))
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on"))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(8):
            while not server.started:
                await asyncio.sleep(0.01)
            async with httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{sock.getsockname()[1]}", timeout=5
            ) as client:
                result = await client.put(
                    "/v1/training/profile",
                    json=profile(
                        session_minutes=50,
                        readiness={
                            "observed_at": datetime.now(UTC).isoformat(),
                            "pain": "none",
                            "fatigue": "normal",
                            "sleep": "normal",
                        },
                    ),
                )
                assert result.status_code == 200
                async with client.stream(
                    "POST", "/v1/chat", json={"message": "安排完整训练"}
                ) as response:
                    events = []
                    async for line in response.aiter_lines():
                        event = json.loads(line)
                        events.append(event)
                        if event["type"] == "text_delta" and not release.is_set():
                            assert "生成中，尚未确认" in event["delta"]
                            assert "先保留重点动作" in event["delta"]
                            release.set()
                        if event["type"] == "done":
                            plan = event["response"]["session_plan"]
                            assert (
                                await client.get(f"/v1/training/plans/{plan['id']}")
                            ).status_code == 200
                assert release.is_set()
                assert events[-1]["type"] == "done", events
                assert "生成中" not in events[-1]["response"]["text"]
    finally:
        release.set()
        server.should_exit = True
        await asyncio.wait_for(task, 5)
        sock.close()


async def test_review_disconnect_saves_visible_facts_and_cancels_model():
    from uuid import uuid4

    from fitness_bot.training_schemas import ReviewDraft, ReviewFinding

    cancelled = asyncio.Event()
    finding = ReviewFinding(
        conclusion="记录中有重复安排",
        evidence_ids=["recent.frequency"],
        comparison="对照所选训练",
        possible_explanation="可能与当时安排有关",
        suggested_action="结合反馈判断",
    )

    class CancelReview:
        async def plan(self, *args):
            return SearchPlan(kind="review")

        async def stream_review(self, request):
            if request.message == "继续":
                yield {
                    "type": "result",
                    "draft": ReviewDraft(keep=["保留记录"], next_adjustments=["结合反馈"]),
                    "omitted_findings": 0,
                }
                return
            try:
                yield {"type": "finding", "finding": finding}
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    app = create_app(Settings(_env_file=None), CompanionAgent(Catalog.load(), CancelReview()))
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
                base_url=f"http://127.0.0.1:{sock.getsockname()[1]}", timeout=5
            ) as client:
                async with client.stream(
                    "POST", "/v1/chat", json={"message": "分析历史", "conversation_id": cid}
                ) as response:
                    async for line in response.aiter_lines():
                        event = json.loads(line)
                        if event["type"] == "text_delta" and "记录中有重复安排" in event["delta"]:
                            break
                await cancelled.wait()
                while True:
                    saved = (await client.get(f"/v1/conversations/{cid}/turns")).json()
                    if saved[0]["status"] == "cancelled":
                        break
                    await asyncio.sleep(0.01)
                assert "记录中有重复安排" in saved[0]["assistant_text"]
                result = await client.post(
                    "/v1/chat", json={"message": "继续", "conversation_id": cid}
                )
                assert json.loads(result.text.splitlines()[-1])["type"] == "done"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 5)
        sock.close()
