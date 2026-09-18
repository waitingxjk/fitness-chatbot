import copy
import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from fitness_bot.agent import CompanionAgent
from fitness_bot.app import create_app
from fitness_bot.catalog import Catalog
from fitness_bot.config import Settings
from fitness_bot.schemas import SearchPlan


class CaptureModel:
    def __init__(self):
        self.requests = []
        self.reply_requests = []

    async def plan(self, request, reference):
        self.requests.append(request)
        return SearchPlan(kind="chat")

    async def stream_reply(self, request, *args):
        self.reply_requests.append(request)
        yield "根据已同步的训练记录进行分析。"


@pytest.fixture
def setup():
    model = CaptureModel()
    with TestClient(
        create_app(Settings(_env_file=None), CompanionAgent(Catalog.load(), model))
    ) as client:
        yield client, model


def workout(title="测试训练", *, day=7, source="device-a"):
    payload = json.loads((Path(__file__).parent / "fixtures/workout_upload.json").read_text())
    payload["sourceId"] = source
    w = payload["workout"]
    w.update(title=title, status="completed", startedAt=f"2026-09-{day:02d}T01:00:00Z")
    w["endedAt"] = f"2026-09-{day:02d}T02:00:00Z"
    r = w["records"][0]
    r["isDraft"] = False
    r["occurredAt"] = w["startedAt"]
    r["sets"][0].update(isCompleted=True, loadValue=40, loadUnit="lb", repetitions=12)
    return payload


def upload(client, payload, headers=None):
    response = client.put("/v1/workouts/" + payload["workout"]["id"], json=payload, headers=headers)
    assert response.status_code == 200


def chat(client, **kwargs):
    response = client.post("/v1/chat", json={"message": "分析我的历史训练", **kwargs})
    assert response.status_code == 200
    done = json.loads(response.text.splitlines()[-1])
    assert done["type"] == "done"
    return done


def test_database_history_reaches_both_model_stages_and_persists(setup):
    client, model = setup
    payload = workout()
    # Draft exercises and unfinished sets must not become performed training.
    draft = copy.deepcopy(payload["workout"]["records"][0])
    draft.update(id="draft", isDraft=True)
    draft["sets"] = []
    payload["workout"]["records"].append(draft)
    incomplete = copy.deepcopy(payload["workout"]["records"][0]["sets"][0])
    incomplete.update(id="incomplete", isCompleted=False, loadValue=999)
    payload["workout"]["records"][0]["sets"].append(incomplete)
    upload(client, payload)
    done = chat(client, context={"available_equipment": ["dumbbell"]})
    history = model.requests[-1].context.model_dump()["workout_history"]
    assert history["total_workouts"] == 1
    assert history["truncated"] is False
    saved = history["workouts"][0]
    assert saved["title"] == "测试训练"
    assert saved["started_at"].startswith("2026-09-07")
    assert len(saved["records"]) == 1
    record = saved["records"][0]
    assert record["exercise_name"] == "哑铃卧推"
    assert record["completed_sets"] == 1
    assert record["sets"][0]["loadUnit"] == "lb"
    assert record["sets"][0]["loadValue"] == 40
    assert len(record["sets"]) == 1
    assert "detailPayload" not in json.dumps(history)
    assert (
        model.reply_requests[-1].context.workout_history
        == model.requests[-1].context.workout_history
    )
    assert model.requests[-1].context.available_equipment == ["dumbbell"]
    turns = client.get(f"/v1/conversations/{done['conversation_id']}/turns").json()
    assert turns[0]["context"]["workout_history"] == history
    step = done["response"]["trace"][-1]
    assert step["data"]["saved_workout_count"] == 1


def test_filters_by_user_status_and_tombstones_across_sources(setup):
    client, model = setup
    upload(client, workout("旧训练", day=1))
    upload(client, workout("新训练", day=8, source="device-b"))
    other = client.portal.call(client.app.state.identity.create_user, "other")
    upload(client, workout("他人记录"), {"X-User-Id": other["userId"]})
    active = workout("未结束", source="active-device")
    active["workout"]["status"] = "active"
    upload(client, active)
    deleted = workout("已删除", source="deleted-device")
    upload(client, deleted)
    client.put(
        "/v1/workouts/" + deleted["workout"]["id"],
        json={"sourceId": "deleted-device", "version": 2, "deleted": True, "workout": None},
    )
    chat(client)
    history = model.requests[-1].context.model_dump()["workout_history"]
    assert history["total_workouts"] == 2
    assert [w["title"] for w in history["workouts"]] == ["新训练", "旧训练"]


def test_server_replaces_echoed_history_and_replay_survives_workout_updates(setup):
    client, model = setup
    payload = workout()
    upload(client, payload)
    request = {
        "conversation_id": str(uuid4()),
        "request_id": str(uuid4()),
        "context": {"workout_history": {"total_workouts": 99, "workouts": [{"title": "伪造"}]}},
    }
    done = chat(client, **request)
    assert model.requests[-1].context.model_dump()["workout_history"]["total_workouts"] == 1
    payload["version"] = 2
    payload["workout"]["title"] = "更新后的训练"
    upload(client, payload)
    # Browser history may echo server context. It is not input for the replay hash.
    request["context"] = model.requests[-1].context.model_dump(mode="json")
    replay = chat(client, **request)
    assert replay["replayed"] is True
    assert len(model.requests) == 1
    assert replay["response"] == done["response"]
    chat(client, context=request["context"])
    assert (
        model.requests[-1].context.model_dump()["workout_history"]["workouts"][0]["title"]
        == "更新后的训练"
    )


def test_empty_account_has_explicit_empty_database_history(setup):
    client, model = setup
    chat(client)
    assert model.requests[-1].context.model_dump()["workout_history"] == {
        "total_workouts": 0,
        "workouts": [],
        "truncated": False,
    }


def test_history_is_bounded_and_truncation_is_explicit(setup):
    client, model = setup
    for index in range(32):
        upload(client, workout(source=f"device-{index:02d}"))
    chat(client)
    history = model.requests[-1].context.model_dump()["workout_history"]
    assert history["total_workouts"] == 32
    assert len(history["workouts"]) == 30
    assert history["truncated"] is True


def test_history_read_failure_does_not_call_model_or_create_turn(setup, monkeypatch):
    from fitness_bot import workout_context

    client, model = setup

    async def fail(*args):
        raise SQLAlchemyError("database failed")

    monkeypatch.setattr(workout_context, "load_workout_history", fail)
    response = client.post("/v1/chat", json={"message": "分析我的历史训练"})
    assert response.status_code == 503
    assert model.requests == []
    assert client.get("/v1/conversations").json() == []


def test_large_snapshot_respects_character_budget_and_marks_partial_details(setup, monkeypatch):
    from fitness_bot import workout_context

    client, model = setup
    payload = workout()
    template = payload["workout"]["records"][0]
    records = []
    for index in range(12):
        record = copy.deepcopy(template)
        record.update(id=f"r-{index}", sequence=index)
        record["sets"][0].update(id=f"s-{index}", exerciseRecordId=record["id"])
        records.append(record)
    payload["workout"]["records"] = records
    upload(client, payload)
    monkeypatch.setattr(workout_context, "MAX_HISTORY_CHARS", 1200)
    chat(client)
    history = model.requests[-1].context.model_dump()["workout_history"]
    assert len(json.dumps(history, ensure_ascii=False, separators=(",", ":"))) <= 1200
    assert history["truncated"] is True
    saved = history["workouts"][0]
    assert saved["record_count"] == 12
    assert saved["truncated"] is True
    assert 0 < len(saved["records"]) < 12


def test_record_and_set_limits_preserve_counts_and_mark_truncation(setup, monkeypatch):
    from fitness_bot import workout_context

    client, model = setup
    payload = workout()
    record = payload["workout"]["records"][0]
    extra_set = copy.deepcopy(record["sets"][0])
    extra_set.update(id="extra-set", sequence=1)
    record["sets"].append(extra_set)
    extra_record = copy.deepcopy(record)
    extra_record.update(id="extra-record", sequence=1, sets=[])
    payload["workout"]["records"].append(extra_record)
    upload(client, payload)
    monkeypatch.setattr(workout_context, "MAX_RECORDS", 1)
    monkeypatch.setattr(workout_context, "MAX_SETS", 1)
    chat(client)
    history = model.requests[-1].context.model_dump()["workout_history"]
    saved = history["workouts"][0]
    assert history["truncated"] and saved["truncated"]
    assert saved["record_count"] == 2 and len(saved["records"]) == 1
    assert saved["records"][0]["completed_sets"] == 2
    assert len(saved["records"][0]["sets"]) == 1
