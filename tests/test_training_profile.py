import json

import pytest
from fastapi.testclient import TestClient

from fitness_bot.agent import CompanionAgent
from fitness_bot.app import create_app
from fitness_bot.catalog import Catalog
from fitness_bot.config import Settings
from fitness_bot.schemas import SearchPlan


class OfflineModel:
    async def plan(self, request, reference):
        return SearchPlan(kind="chat")

    async def stream_reply(self, *args):
        yield "test"


@pytest.fixture
def client():
    with TestClient(
        create_app(Settings(_env_file=None), CompanionAgent(Catalog.load(), OfflineModel()))
    ) as client:
        yield client


def profile(**changes):
    return {
        "expected_version": 0,
        "profile": {
            "goal": "strength",
            "priority_exercise_ids": ["0289"],
            "experience": "experienced",
            "weekly_sessions": 3,
            "session_minutes": 45,
            "equipment": ["dumbbell", "cable"],
            "time_zone": "Asia/Singapore",
            **changes,
        },
    }


def test_profile_explicit_versioned_updates_and_unknown_defaults(client):
    empty = client.get("/v1/training/profile")
    assert empty.status_code == 200
    assert empty.json()["version"] == 0
    assert empty.json()["profile"]["goal"] is None
    assert empty.json()["profile"]["confirmed_cycle"] is None
    saved = client.put("/v1/training/profile", json=profile())
    assert saved.status_code == 200
    assert saved.json()["version"] == 1
    assert saved.json()["profile"]["goal"] == "strength"
    assert client.put("/v1/training/profile", json=profile()).json() == saved.json()
    assert client.put("/v1/training/profile", json=profile(session_minutes=30)).status_code == 409
    update = profile(session_minutes=30)
    update["expected_version"] = 1
    assert client.put("/v1/training/profile", json=update).json()["version"] == 2


def test_profile_scoping_and_server_context_replacement(client):
    client.put("/v1/training/profile", json=profile())
    other = client.portal.call(client.app.state.identity.create_user, "other")
    assert (
        client.get("/v1/training/profile", headers={"X-User-Id": other["userId"]}).json()["version"]
        == 0
    )
    response = client.post(
        "/v1/chat",
        json={
            "message": "我的目标",
            "context": {"training_profile": {"profile": {"goal": "forged"}}},
        },
    )
    done = json.loads(response.text.splitlines()[-1])
    turns = client.get(f"/v1/conversations/{done['conversation_id']}/turns").json()
    stored = turns[0]["context"]["training_profile"]
    assert stored["version"] == 1
    assert stored["profile"]["goal"] == "strength"
    assert turns[0]["context"]["training_review"]["time_zone"] == "Asia/Singapore"


@pytest.mark.parametrize(
    "changes",
    [
        {"priority_exercise_ids": ["fake-id"]},
        {"equipment": ["made-up"]},
        {"session_minutes": 0},
        {"weekly_sessions": 8},
        {"time_zone": "bad-zone"},
        {"readiness": {"observed_at": "2026-09-08T01:00:00", "pain": "none"}},
        {"readiness": {"observed_at": "2026-09-08T01:00:00Z", "pain": "none", "rpe": 7}},
    ],
)
def test_profile_validation(client, changes):
    assert client.put("/v1/training/profile", json=profile(**changes)).status_code == 422


def test_readiness_scale_timing_and_confirmed_cycle_are_explicit(client):
    data = profile(
        readiness={
            "observed_at": "2026-09-08T01:00:00Z",
            "pain": "none",
            "fatigue": "high",
            "sleep": "poor",
        },
        confirmed_cycle={
            "phase": "deload",
            "starts_on": "2026-09-08",
            "ends_on": "2026-09-14",
            "reason": "用户确认本周降低要求",
        },
    )
    saved = client.put("/v1/training/profile", json=data).json()
    assert saved["profile"]["readiness"]["fatigue"] == "high"
    assert saved["profile"]["confirmed_cycle"]["phase"] == "deload"
