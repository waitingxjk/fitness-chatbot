"""Real loopback HTTP contract gate; an injected model never calls an LLM."""

import copy
import json
import os
import socket
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
import uvicorn
from test_training_decision import draft
from test_training_profile import profile

from fitness_bot.agent import CompanionAgent
from fitness_bot.app import create_app
from fitness_bot.catalog import Catalog
from fitness_bot.config import Settings
from fitness_bot.schemas import SearchPlan
from fitness_bot.training_schemas import SessionDraft


class WirePlanModel:
    def __init__(self):
        self.requests = []

    async def plan(self, request, reference):
        self.requests.append(request)
        return SearchPlan(kind="session_plan")

    async def session_preview(self, request, candidates):
        return {
            "theme": "补全本次候选",
            "rationale": "先确认新增安排可用时间。",
            "items": [{"exercise_id": "0180", "purpose": "补充上肢拉"}],
        }

    async def session_plan(self, request, *args):
        value = draft()
        value["evidence_ids"] = []
        if request.context.planning_target == "current_workout":
            value["items"] = value["items"][1:]
            value["items"][0]["role"] = "priority"
        if request.message == "无需新增":
            value["items"] = []
        return SessionDraft.model_validate(value)


def test_real_http_current_next_preview_and_persistence(tmp_path):
    model = WirePlanModel()
    app = create_app(Settings(_env_file=None), CompanionAgent(Catalog.load(), model))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started, "isolated HTTP server did not start"
    fixtures = {}
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=20) as client:
            p = profile(
                session_minutes=60,
                readiness={
                    "observed_at": datetime.now(UTC).isoformat(),
                    "pain": "none",
                    "fatigue": "normal",
                    "sleep": "normal",
                },
            )
            assert client.put("/v1/training/profile", json=p).status_code == 200
            snapshot = json.loads(
                (Path(__file__).parent / "fixtures/workout_upload.json").read_text()
            )
            record = snapshot["workout"]["records"][0]
            record["isDraft"] = False
            record["sets"][0].update(loadValue=20, repetitions=8, isCompleted=True)
            pending = copy.deepcopy(record["sets"][0])
            pending.update(id="pending-set", sequence=1, isCompleted=False)
            record["sets"].append(pending)

            def chat(name, context, message="安排训练"):
                body = {
                    "request_id": str(uuid4()),
                    "conversation_id": str(uuid4()),
                    "message": message,
                    "context": context,
                }
                with client.stream("POST", "/v1/chat", json=body) as response:
                    assert response.status_code == 200
                    assert "application/x-ndjson" in response.headers["content-type"]
                    events = [json.loads(line) for line in response.iter_lines() if line]
                assert events[-1]["type"] == "done", events[-1]
                result = events[-1]["response"]
                fixtures[name] = {"request": body, "response": result, "events": events}
                if result.get("session_plan"):
                    plan = result["session_plan"]
                    persisted = client.get(f"/v1/training/plans/{plan['id']}")
                    assert persisted.status_code == 200
                    assert persisted.json()["plan"] == plan
                return result

            context = {
                "planning_target": "current_workout",
                "current_workout": snapshot,
                "remaining_minutes": 60,
            }
            current = chat("current", context)["session_plan"]
            assert current["planning_target"] == "current_workout"
            assert (
                current["current_workout"]["workout"]["records"] == snapshot["workout"]["records"]
            )
            assert "0289" not in {i["exercise"]["id"] for i in current["items"]}
            assert current["existing_completed_sets"] == 1
            assert current["existing_pending_sets"] == 1
            assert current["outcome"] == "add_exercises"
            preview = chat(
                "preview", {k: v for k, v in context.items() if k != "remaining_minutes"}
            )
            assert preview["session_plan"] is None
            assert preview["plan_preview"]["planning_target"] == "current_workout"
            empty = chat("nothing_to_add", context, "无需新增")["session_plan"]
            assert empty["outcome"] == "nothing_to_add"
            assert empty["items"] == []
            future = chat("next", {"planning_target": "next_workout"})["session_plan"]
            assert future["planning_target"] == "next_workout"
            assert not future.get("current_workout")
            assert model.requests[-1].context.active_workout is None
            assert model.requests[-1].context.current_workout is None
            assert model.requests[-1].context.recent_exercises == []
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
    assert not thread.is_alive(), "HTTP server failed to stop"
    export = os.environ.get("WORKOUT_PLAN_WIRE_FIXTURES")
    if export:
        destination = Path(export)
        destination.mkdir(parents=True, exist_ok=True)
        for name, value in fixtures.items():
            (destination / f"{name}.json").write_text(
                json.dumps(value, ensure_ascii=False, indent=2) + "\n"
            )
