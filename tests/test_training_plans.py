import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_training_decision import draft
from test_training_profile import profile
from test_training_review import upload

from fitness_bot.agent import CompanionAgent
from fitness_bot.app import create_app
from fitness_bot.catalog import Catalog
from fitness_bot.config import Settings
from fitness_bot.schemas import SearchPlan


class PlanModel:
    def __init__(self):
        self.requests = []
        self.parent = None

    async def plan(self, request, reference):
        self.requests.append(request)
        return SearchPlan(kind="session_plan", parent_plan_id=self.parent)

    async def session_plan(self, request, *args):
        from fitness_bot.training_decision import evidence_index
        from fitness_bot.training_schemas import SessionDraft

        candidate = draft()
        available = evidence_index(request.context.training_review or {})
        candidate["evidence_ids"] = [key for key in candidate["evidence_ids"] if key in available]
        return SessionDraft.model_validate(candidate)


@pytest.fixture
def setup():
    model = PlanModel()
    with TestClient(
        create_app(Settings(_env_file=None), CompanionAgent(Catalog.load(), model))
    ) as client:
        assert (
            client.put(
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
            ).status_code
            == 200
        )
        yield client, model


def generate(client):
    response = client.post("/v1/chat", json={"message": "给我整次训练方案"})
    done = json.loads(response.text.splitlines()[-1])
    assert done["type"] == "done", done
    return done


def event(kind, revision, **extra):
    return {"event_id": str(uuid4()), "expected_revision": revision, "kind": kind, **extra}


def test_generated_plan_committed_before_done_and_acceptance_is_idempotent(setup):
    client, model = setup
    done = generate(client)
    plan = done["response"]["session_plan"]
    saved = client.get(f"/v1/training/plans/{plan['id']}")
    assert saved.status_code == 200
    assert saved.json()["plan"] == plan
    action = event("accepted", 0)
    first = client.post(f"/v1/training/plans/{plan['id']}/events", json=action)
    assert first.status_code == 200
    assert first.json()["status"] == "accepted"
    assert first.json()["events_truncated"] is False
    assert (
        client.post(f"/v1/training/plans/{plan['id']}/events", json=action).json() == first.json()
    )
    assert (
        client.post(
            f"/v1/training/plans/{plan['id']}/events", json=event("skipped", 0, reason="时间变化")
        ).status_code
        == 409
    )
    generate(client)
    assert any(p["status"] == "accepted" for p in model.requests[-1].context.session_plans)


def test_plan_execution_link_and_feedback_do_not_change_ios_workout(setup):
    client, _ = setup
    plan = generate(client)["response"]["session_plan"]
    url = f"/v1/training/plans/{plan['id']}/events"
    assert client.post(url, json=event("accepted", 0)).status_code == 200
    payload = upload(client, "actual", "2026-09-08T01:00:00Z")
    read_url = f"/v1/workouts/actual?sourceId={payload['sourceId']}"
    before_response = client.get(read_url)
    assert before_response.status_code == 200
    before = before_response.json()
    result = client.post(
        url,
        json=event(
            "executed",
            1,
            workout={"source_id": payload["sourceId"], "workout_id": "actual"},
            reason="只完成了卧推",
            effort={
                "scale": "RIR",
                "value": 2,
                "timing": "last_working_set",
                "observed_at": datetime.now(UTC).isoformat(),
            },
        ),
    )
    assert result.status_code == 200
    assert result.json()["status"] == "executed"
    assert result.json()["events"][-1]["actual"]["completed_sets"] == 1
    assert result.json()["execution"]["actual"]["completed_sets"] == 1
    assert client.get(read_url).json() == before
    assert client.post(url, json=event("accepted", 2)).status_code == 409


def test_cross_user_plan_and_execution_reference_are_hidden(setup):
    client, _ = setup
    plan = generate(client)["response"]["session_plan"]
    other = client.portal.call(client.app.state.identity.create_user, "other")
    headers = {"X-User-Id": other["userId"]}
    url = f"/v1/training/plans/{plan['id']}"
    assert client.get(url, headers=headers).status_code == 404
    assert (
        client.post(url + "/events", json=event("accepted", 0), headers=headers).status_code == 404
    )
    foreign = upload(client, "foreign", "2026-09-08T01:00:00Z", headers=headers)
    assert client.post(url + "/events", json=event("accepted", 0)).status_code == 200
    assert (
        client.post(
            url + "/events",
            json=event(
                "executed", 1, workout={"source_id": foreign["sourceId"], "workout_id": "foreign"}
            ),
        ).status_code
        == 404
    )


def test_successor_keeps_original_plan_and_records_new_version(setup):
    client, model = setup
    parent = generate(client)["response"]["session_plan"]
    model.parent = parent["id"]
    successor = generate(client)["response"]["session_plan"]
    assert successor["id"] != parent["id"]
    assert successor["parent_plan_id"] == parent["id"]
    assert successor["version"] == 2
    assert client.get(f"/v1/training/plans/{parent['id']}").json()["plan"] == parent


def test_accepted_plan_survives_new_proposals_and_old_parent_reaches_model(setup):
    client, model = setup
    parent = generate(client)["response"]["session_plan"]
    client.post(f"/v1/training/plans/{parent['id']}/events", json=event("accepted", 0))
    for _ in range(6):
        generate(client)
    plans = client.get("/v1/training/plans").json()
    assert any(p["plan"]["id"] == parent["id"] for p in plans)
    model.parent = parent["id"]
    generate(client)
    assert any(p["plan"]["id"] == parent["id"] for p in model.requests[-1].context.session_plans)


def test_skipping_needs_reason_and_effort_scale_is_validated(setup):
    client, _ = setup
    plan = generate(client)["response"]["session_plan"]
    url = f"/v1/training/plans/{plan['id']}/events"
    assert client.post(url, json=event("skipped", 0)).status_code == 422
    assert (
        client.post(
            url,
            json=event(
                "feedback",
                0,
                effort={
                    "scale": "RPE",
                    "value": 99,
                    "timing": "session",
                    "observed_at": datetime.now(UTC).isoformat(),
                },
            ),
        ).status_code
        == 422
    )
    result = client.post(url, json=event("skipped", 0, reason="器械不可用"))
    assert result.status_code == 200
    assert result.json()["events"][-1]["reason"] == "器械不可用"


def test_execution_evidence_survives_long_feedback_history(setup):
    client, model = setup
    plan = generate(client)["response"]["session_plan"]
    url = f"/v1/training/plans/{plan['id']}/events"
    client.post(url, json=event("accepted", 0))
    payload = upload(client, "actual", "2026-09-08T01:00:00Z")
    client.post(
        url,
        json=event(
            "executed", 1, workout={"source_id": payload["sourceId"], "workout_id": "actual"}
        ),
    )
    for revision in range(2, 13):
        assert (
            client.post(url, json=event("feedback", revision, reason="补充状态")).status_code == 200
        )
    generate(client)
    saved = next(
        p for p in model.requests[-1].context.session_plans if p["plan"]["id"] == plan["id"]
    )
    assert saved["execution"]["actual"]["completed_sets"] == 1


def test_reduce_label_cannot_increase_parent_sets(setup):
    from test_training_decision import request

    from fitness_bot.training_decision import validate_session_plan
    from fitness_bot.training_schemas import SessionDraft

    client, _ = setup
    plan = generate(client)["response"]["session_plan"]
    r = request()
    r.context.plan_to_modify = plan["id"]
    r.context.session_plans = [{"plan": plan, "status": "accepted"}]
    data = draft()
    data["adjustment"] = "reduce"
    data["items"][0]["sets"] = 6
    with pytest.raises(ValueError, match="Reduced"):
        validate_session_plan(SessionDraft.model_validate(data), r, Catalog.load())
