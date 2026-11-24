"""Full candidate plans use labeled defaults; accepting remains a separate user action."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from test_training_decision import draft, request
from test_training_review import upload

from fitness_bot.agent import CompanionAgent
from fitness_bot.app import create_app
from fitness_bot.catalog import Catalog
from fitness_bot.config import Settings
from fitness_bot.schemas import SearchPlan
from fitness_bot.training_decision import evidence_index, validate_session_plan
from fitness_bot.training_schemas import SessionDraft


class CandidateModel:
    def __init__(self):
        self.inputs = {"goal": "hypertrophy", "experience": "beginner", "session_minutes": 60}
        self.requests = []

    async def plan(self, request, reference):
        return SearchPlan(kind="session_plan", session_inputs=self.inputs)

    async def stream_reply(self, *args):
        yield "Unexpected questionnaire"

    async def session_plan(self, request, catalog):
        self.requests.append(request)
        value = draft()
        value["evidence_ids"] = [
            k for k in value["evidence_ids"] if k in evidence_index(request.context.training_review)
        ]
        # A declared equipment restriction also controls the fake model's selection.
        allowed = request.context.training_profile["profile"]["equipment"]
        value["items"] = [
            i for i in value["items"] if catalog.get(i["exercise_id"]).equipment_code in allowed
        ]
        return SessionDraft.model_validate(value)


def test_one_turn_history_candidate_has_sets_and_explicit_acceptance():
    model = CandidateModel()
    with TestClient(
        create_app(Settings(_env_file=None), CompanionAgent(Catalog.load(), model))
    ) as client:
        upload(client, "historical-workout", "2026-09-07T01:00:00Z")
        events = [
            json.loads(line)
            for line in client.post(
                "/v1/chat", json={"message": "根据历史推荐明天的完整运动计划"}
            ).text.splitlines()
        ]
        response = events[-1]["response"]
        plan = response["session_plan"]
        assert plan is not None, response["text"]
        assert response["plan_preview"] is None
        assert all(i["set_prescriptions"] for i in plan["items"])
        assert any(a["field"] == "equipment" for a in plan["planning_assumptions"])
        assert plan["profile_snapshot"]["readiness"] is None
        assert plan["profile_snapshot"]["session_minutes"] == 60
        saved = client.get(f"/v1/training/plans/{plan['id']}").json()
        assert saved["status"] == "proposed"
        assert saved["events"] == []
        assert client.get("/v1/training/profile").json()["version"] == 0
        # Keep the explicit inputs through another turn; do not persist guessed profile facts.
        model.inputs = {"equipment": ["dumbbell"]}
        followup = client.post(
            "/v1/chat",
            json={
                "conversation_id": events[-1]["conversation_id"],
                "message": "这里只能用哑铃，调整一下",
            },
        )
        updated = json.loads(followup.text.splitlines()[-1])["response"]["session_plan"]
        assert updated["profile_snapshot"]["goal"] == "hypertrophy"
        assert updated["profile_snapshot"]["experience"] == "beginner"
        assert updated["profile_snapshot"]["session_minutes"] == 60
        assert updated["profile_snapshot"]["equipment"] == ["dumbbell"]
        assert all(i["exercise"]["equipmentCode"] == "dumbbell" for i in updated["items"])
        assert not any(a["field"] == "equipment" for a in updated["planning_assumptions"])


def test_no_history_still_generates_full_candidate_with_labeled_defaults():
    model = CandidateModel()
    model.inputs = None
    with TestClient(
        create_app(Settings(_env_file=None), CompanionAgent(Catalog.load(), model))
    ) as client:
        events = [
            json.loads(line)
            for line in client.post(
                "/v1/chat", json={"message": "给我一份完整训练计划"}
            ).text.splitlines()
        ]
        plan = events[-1]["response"]["session_plan"]
        assert plan is not None
        assert {a["field"] for a in plan["planning_assumptions"]} >= {
            "goal",
            "experience",
            "session_minutes",
            "equipment",
            "readiness",
        }
        assert plan["profile_snapshot"]["readiness"] is None


def test_missing_readiness_allows_candidate_but_does_not_authorize_progression():
    context = request()
    context.context.training_profile["profile"]["readiness"] = None
    plan = validate_session_plan(SessionDraft.model_validate(draft()), context, Catalog.load())
    assert plan["status"] == "proposed"
    assert plan["profile_snapshot"]["readiness"] is None
    value = draft()
    value["adjustment"] = "increase_load"
    with pytest.raises(ValueError, match="[Rr]eadiness|状态"):
        validate_session_plan(SessionDraft.model_validate(value), context, Catalog.load())


def test_explicit_pain_still_blocks_and_stale_normal_state_is_not_fresh_evidence():
    from fitness_bot.training_decision import plan_question

    context = request()
    context.context.training_profile["profile"]["readiness"]["pain"] = "present"
    assert "疼痛" in plan_question(context)
    context.context.training_profile["profile"]["readiness"] = {
        "observed_at": (datetime.now(UTC) - timedelta(days=3)).isoformat(),
        "pain": "none",
        "fatigue": "normal",
        "sleep": "normal",
    }
    assert plan_question(context) is None


@pytest.mark.parametrize("action", ["revision", "accepted", "later_row"])
def test_changed_history_reference_is_rejected_before_revision_or_acceptance(action):
    from test_plan_revisions import edit_payload
    from test_training_plans import event

    model = CandidateModel()
    with TestClient(
        create_app(Settings(_env_file=None), CompanionAgent(Catalog.load(), model))
    ) as client:
        source = upload(client, "historical-workout", "2026-09-07T01:00:00Z")
        result = client.post("/v1/chat", json={"message": "根据历史安排训练"})
        plan = json.loads(result.text.splitlines()[-1])["response"]["session_plan"]
        assert plan["items"][0]["load"]["strategy"] == "history_reference"
        if action == "later_row":
            edit = edit_payload(plan)
            edit["items"][0]["set_prescriptions"][0]["load"] = {
                "strategy": "trial",
                "explanation": "待试重",
            }
            child = client.post(f"/v1/training/plans/{plan['id']}/revision", json=edit)
            assert child.status_code == 200, child.text
            plan = child.json()["plan"]
        source["version"] += 1
        source["workout"]["records"][0]["sets"][0]["loadValue"] = 99
        assert client.put("/v1/workouts/historical-workout", json=source).status_code == 200
        if action == "revision":
            edit = edit_payload(plan)
            edit["items"][0]["set_prescriptions"][0]["repetitions"] = 7
            response = client.post(f"/v1/training/plans/{plan['id']}/revision", json=edit)
        else:
            response = client.post(
                f"/v1/training/plans/{plan['id']}/events", json=event("accepted", 0)
            )
        assert response.status_code == 409, response.text


def test_successful_acceptance_retry_remains_idempotent_after_history_changes():
    from test_training_plans import event

    model = CandidateModel()
    with TestClient(
        create_app(Settings(_env_file=None), CompanionAgent(Catalog.load(), model))
    ) as client:
        source = upload(client, "historical-workout", "2026-09-07T01:00:00Z")
        result = client.post("/v1/chat", json={"message": "根据历史安排训练"})
        plan = json.loads(result.text.splitlines()[-1])["response"]["session_plan"]
        payload = event("accepted", 0)
        endpoint = f"/v1/training/plans/{plan['id']}/events"
        saved = client.post(endpoint, json=payload)
        assert saved.status_code == 200
        source["version"] += 1
        client.put("/v1/workouts/historical-workout", json=source)
        replay = client.post(endpoint, json=payload)
        assert replay.status_code == 200 and replay.json() == saved.json()


def test_unrelated_profile_update_does_not_erase_explicit_conversation_equipment():
    from types import SimpleNamespace

    from fitness_bot.plan_inputs import restore_conversation_inputs

    saved = {"version": 2, "profile": {"equipment": ["barbell"], "time_zone": "Asia/Singapore"}}
    previous = [
        SimpleNamespace(
            context={
                "training_profile": {
                    "version": 1,
                    "turn_overrides": {"equipment": ["dumbbell"], "session_minutes": 45},
                }
            }
        )
    ]
    restored = restore_conversation_inputs(saved, previous)
    assert restored["profile"]["equipment"] == ["dumbbell"]
    assert restored["profile"]["time_zone"] == "Asia/Singapore"
    assert restored["profile"]["session_minutes"] == 45
    assert saved["profile"]["equipment"] == ["barbell"]


def test_realistic_model_history_claim_returns_complete_plan_and_can_be_saved():
    from test_training_plans import event

    class UnverifiedHistoryModel(CandidateModel):
        async def session_plan(self, request, catalog):
            value = await super().session_plan(request, catalog)
            observation = next(
                o
                for o in request.context.training_review["observations"]
                if o["exercise_id"] == value.items[0].exercise_id
            )
            assert observation["comparison_eligible"] is False
            value.items[0].load_strategy = "history"
            value.items[0].load_observation_id = observation["id"]
            return value

    with TestClient(
        create_app(
            Settings(_env_file=None), CompanionAgent(Catalog.load(), UnverifiedHistoryModel())
        )
    ) as client:
        upload(client, "same-exercise", "2026-09-07T01:00:00Z")
        response = client.post(
            "/v1/chat", json={"message": "请根据我的已完成训练历史，创建完整训练计划。"}
        )
        done = json.loads(response.text.splitlines()[-1])
        assert done["type"] == "done", done
        plan = done["response"]["session_plan"]
        assert plan["items"][0]["load"]["strategy"] == "history_reference"
        saved = client.post(f"/v1/training/plans/{plan['id']}/events", json=event("accepted", 0))
        assert saved.status_code == 200
        assert saved.json()["status"] == "accepted"
