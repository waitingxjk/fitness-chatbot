"""Planning never rewrites the App-owned current workout or leaks it into next work."""

import hashlib
import json
from copy import deepcopy
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from test_plan_revisions import edit_payload
from test_training_decision import draft, request
from test_training_plans import event
from test_training_plans import setup as _plan_setup
from test_training_review import upload

from fitness_bot.catalog import Catalog
from fitness_bot.schemas import ChatRequest, SearchPlan
from fitness_bot.storage import ChatTurn
from fitness_bot.training_decision import plan_question, validate_session_plan
from fitness_bot.training_schemas import SessionDraft

setup = _plan_setup
CURRENT_FIXTURE = Path(__file__).parent / "fixtures/planning_current_workout.json"


def current_snapshot():
    return json.loads(CURRENT_FIXTURE.read_text())


def current_context(**changes):
    return {
        "planning_target": "current_workout",
        "current_workout": current_snapshot(),
        "remaining_minutes": 50,
        **changes,
    }


def current_request(**changes):
    original = request().model_dump(mode="json")
    original["context"].update(current_context(**changes))
    return ChatRequest.model_validate(original)


def additions():
    candidate = draft()
    candidate["items"] = candidate["items"][1:]
    candidate["items"][0]["role"] = "priority"
    return candidate


def validate(candidate=None, **changes):
    return validate_session_plan(
        SessionDraft.model_validate(additions() if candidate is None else candidate),
        current_request(**changes),
        Catalog.load(),
    )


def post_plan(client, **payload):
    response = client.post("/v1/chat", json={"message": "安排训练", **payload})
    assert response.status_code == 200, response.text
    events = [json.loads(line) for line in response.text.splitlines()]
    assert events[-1]["type"] == "done", events[-1]
    return events[-1]


def install_current_model(model, *, empty=False, target_date=None):
    model.decision_requests = []
    model.preview_requests = []

    async def plan(request, reference):
        model.requests.append(request.model_copy(deep=True))
        return SearchPlan(
            kind="session_plan",
            parent_plan_id=model.parent,
            session_inputs={"target_date": target_date} if target_date else None,
        )

    async def session_plan(request, *args):
        model.decision_requests.append(request.model_copy(deep=True))
        value = additions()
        value["evidence_ids"] = []
        if empty:
            value["items"] = []
        return SessionDraft.model_validate(value)

    async def preview(request, candidates):
        model.preview_requests.append((request.model_copy(deep=True), candidates))
        return {"theme": "训练候选", "rationale": "补充条件后安排", "items": []}

    model.plan = plan
    model.session_plan = session_plan
    model.session_preview = preview


def test_current_plan_preserves_duplicate_records_and_raw_sets_separately():
    original = current_snapshot()
    result = validate()
    assert result["planning_target"] == "current_workout"
    assert result["current_workout"] == original
    assert result["existing_completed_sets"] == 1
    assert result["existing_pending_sets"] == 2
    assert result["working_sets"] == 7
    assert result["outcome"] == "add_exercises"
    assert result["remaining_minutes"] == 50
    assert all(i["exercise"]["id"] != "0289" for i in result["items"])
    assert result["current_workout"]["workout"]["records"][0]["sets"][1]["loadValue"] == -1


def test_current_plan_rejects_new_items_matching_any_existing_record():
    with pytest.raises(ValueError, match="already.*current workout"):
        validate(draft())


@pytest.mark.parametrize("adjustment,outcome", [("maintain", "nothing_to_add"), ("rest", "rest")])
def test_completed_or_pending_current_plan_may_have_no_additions(adjustment, outcome):
    candidate = additions()
    candidate.update(items=[], adjustment=adjustment)
    result = validate(candidate)
    assert result["outcome"] == outcome
    assert result["current_workout"] == current_snapshot()
    assert result["items"] == []
    assert result["working_sets"] == 0
    assert result["estimated_minutes_min"] == result["estimated_minutes_max"] == 0


def test_current_plan_requires_fresh_snapshot_and_explicit_new_work_budget():
    assert "快照" in plan_question(current_request(current_workout=None))
    assert "新增" in plan_question(current_request(remaining_minutes=None))
    with pytest.raises(ValueError, match="Missing planning inputs"):
        validate(remaining_minutes=None)
    ready = current_request()
    ready.context.training_profile["profile"]["session_minutes"] = None
    assert plan_question(ready) is None


@pytest.mark.parametrize("change", ["deleted", "completed", "broken_link", "boolean_budget"])
def test_current_snapshot_input_is_validated(change):
    context = current_context()
    if change == "deleted":
        context["current_workout"].update(deleted=True, workout=None)
    elif change == "completed":
        context["current_workout"]["workout"]["status"] = "completed"
    elif change == "broken_link":
        context["current_workout"]["workout"]["records"][0]["workoutId"] = "another"
    else:
        context["remaining_minutes"] = True
    with pytest.raises(ValidationError):
        ChatRequest(message="安排训练", context=context)


def test_current_plan_remaining_budget_does_not_count_existing_pending_work():
    snapshot = current_snapshot()
    snapshot["workout"]["records"][1]["sets"][0]["durationSeconds"] = 100000
    result = validate(current_workout=snapshot, remaining_minutes=30)
    assert result["estimated_minutes_max"] <= 30
    assert result["current_workout"] == snapshot


def test_current_preview_rejects_repeated_exercise_candidates(setup):
    client, model = setup
    install_current_model(model)
    done = post_plan(client, context=current_context(remaining_minutes=None))
    preview = done["response"]["plan_preview"]
    assert preview["planning_target"] == "current_workout"
    assert preview["current_workout"] == current_snapshot()
    assert "新增" in preview["next_question"]
    assert all(e.id != "0289" for e in model.preview_requests[-1][1])
    assert model.decision_requests == []


def test_current_followup_restores_target_but_never_old_snapshot(setup):
    client, model = setup
    install_current_model(model, target_date=date(2026, 9, 20))
    first = post_plan(client, context=current_context(remaining_minutes=None))
    install_current_model(model)
    second = post_plan(client, conversation_id=first["conversation_id"], message="可以")
    preview = second["response"]["plan_preview"]
    assert preview["planning_target"] == "current_workout"
    assert preview["current_workout"] is None
    assert preview["target_date"] == "2026-09-20"
    assert "快照" in preview["next_question"]
    assert model.decision_requests == []
    assert client.get("/v1/training/plans").json() == []


def test_next_plan_strips_current_and_client_recent_data_from_both_model_stages(setup):
    client, model = setup
    install_current_model(model)
    upload(client, "history", "2026-09-08T01:00:00Z")
    done = post_plan(
        client,
        context=current_context(
            planning_target="next_workout",
            active_workout={"id": "local-workout-001", "exercise_ids": ["0289"]},
            recent_exercises=[{"exercise_id": "0289", "performed_at": "2026-09-18T01:00:00Z"}],
        ),
    )
    for captured in [model.requests[-1], model.decision_requests[-1]]:
        assert captured.context.current_workout is None
        assert captured.context.active_workout is None
        assert captured.context.recent_exercises == []
        assert captured.context.remaining_minutes is None
        assert captured.context.workout_history.total_workouts == 1
    plan = done["response"]["session_plan"]
    assert plan["planning_target"] == "next_workout"
    assert plan["current_workout"] is None


def test_explicit_next_clears_previous_current_target_in_followups(setup):
    client, model = setup
    install_current_model(model)
    first = post_plan(client, context=current_context())
    second = post_plan(
        client,
        conversation_id=first["conversation_id"],
        context={"planning_target": "next_workout"},
    )
    third = post_plan(client, conversation_id=first["conversation_id"])
    for done in (second, third):
        assert done["response"]["session_plan"]["planning_target"] == "next_workout"
        assert done["response"]["session_plan"]["current_workout"] is None


def test_current_plan_persists_before_done_and_replay_uses_original_snapshot(setup):
    client, model = setup
    install_current_model(model)
    payload = {
        "request_id": str(uuid4()),
        "conversation_id": str(uuid4()),
        "context": current_context(),
    }
    done = post_plan(client, **payload)
    plan = done["response"]["session_plan"]
    assert client.get(f"/v1/training/plans/{plan['id']}").json()["plan"] == plan
    assert post_plan(client, **payload)["response"] == done["response"]
    changed = deepcopy(payload)
    changed["context"]["current_workout"]["version"] += 1
    assert client.post("/v1/chat", json={"message": "安排训练", **changed}).status_code == 409
    saved = client.get(f"/v1/conversations/{done['conversation_id']}/turns").json()[-1]
    assert saved["response"]["session_plan"]["current_workout"] == current_snapshot()
    assert (
        client.get("/v1/workouts", params={"sourceId": current_snapshot()["sourceId"]}).json() == []
    )


def test_revision_preserves_current_snapshot_target_date_and_only_edits_additions(setup):
    client, model = setup
    install_current_model(model, target_date=date(2026, 9, 20))
    original = post_plan(client, context=current_context())["response"]["session_plan"]
    data = edit_payload(original)
    data["items"] = data["items"][:1]
    revised = client.post(f"/v1/training/plans/{original['id']}/revision", json=data)
    assert revised.status_code == 200, revised.text
    child = revised.json()["plan"]
    assert child["current_workout"] == current_snapshot()
    assert child["planning_target"] == "current_workout"
    assert child["target_date"] == "2026-09-20"
    assert child["existing_completed_sets"] == 1
    assert child["working_sets"] == 3
    bad = edit_payload(child)
    bad["items"][0]["exercise_id"] = "0289"
    assert client.post(f"/v1/training/plans/{child['id']}/revision", json=bad).status_code == 422


def test_current_revision_can_remove_all_additions_without_removing_existing_work(setup):
    client, model = setup
    install_current_model(model)
    original = post_plan(client, context=current_context())["response"]["session_plan"]
    data = edit_payload(original)
    data["items"] = []
    result = client.post(f"/v1/training/plans/{original['id']}/revision", json=data)
    assert result.status_code == 200, result.text
    child = result.json()["plan"]
    assert child["current_workout"] == current_snapshot()
    assert child["outcome"] == "nothing_to_add"
    assert child["working_sets"] == child["estimated_minutes_max"] == 0


def test_current_execution_requires_matching_workout_and_keeps_snapshot_immutable(setup):
    client, model = setup
    install_current_model(model)
    original = post_plan(client, context=current_context())["response"]["session_plan"]
    url = f"/v1/training/plans/{original['id']}/events"
    assert client.post(url, json=event("accepted", 0)).status_code == 200
    other = upload(client, "other", "2026-09-08T01:00:00Z")
    result = client.post(
        url,
        json=event("executed", 1, workout={"source_id": other["sourceId"], "workout_id": "other"}),
    )
    assert result.status_code == 409
    snapshot = current_snapshot()
    snapshot["workout"].update(status="completed", endedAt="2026-09-18T01:30:00Z")
    wid = snapshot["workout"]["id"]
    assert client.put(f"/v1/workouts/{wid}", json=snapshot).status_code == 200
    result = client.post(
        url,
        json=event("executed", 1, workout={"source_id": snapshot["sourceId"], "workout_id": wid}),
    )
    assert result.status_code == 200, result.text
    assert result.json()["plan"]["current_workout"] == current_snapshot()


def test_legacy_request_hash_is_unchanged_by_optional_planning_fields(setup):
    client, _ = setup
    payload = {"message": "安排训练", "request_id": str(uuid4()), "conversation_id": str(uuid4())}
    req = ChatRequest.model_validate(payload)
    old_input = req.model_dump(mode="json", exclude={"history", "previous_result_ids", "images"})
    for key in (
        "custom_exercise_drafts",
        "workout_history",
        "training_review",
        "training_profile",
        "session_plans",
        "plan_to_modify",
        "review_options",
        "planning_target",
        "current_workout",
        "remaining_minutes",
        "planning_target_date",
    ):
        old_input["context"].pop(key, None)
    expected = hashlib.sha256(
        json.dumps(old_input, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    post_plan(client, **payload)

    async def saved_hash():
        async with client.app.state.store.sessions() as session:
            return await session.scalar(
                select(ChatTurn.input_hash).where(ChatTurn.request_id == payload["request_id"])
            )

    assert client.portal.call(saved_hash) == expected


def test_model_successor_inherits_current_target_and_date_but_requires_new_snapshot(setup):
    client, model = setup
    install_current_model(model, target_date=date(2026, 9, 20))
    original = post_plan(client, context=current_context())["response"]["session_plan"]
    install_current_model(model)
    model.parent = original["id"]
    preview = post_plan(client)["response"]["plan_preview"]
    assert preview["planning_target"] == "current_workout"
    assert preview["target_date"] == "2026-09-20"
    assert preview["current_workout"] is None
    fresh = current_snapshot()
    fresh["version"] += 1
    result = post_plan(client, context=current_context(current_workout=fresh))["response"][
        "session_plan"
    ]
    assert result["current_workout"] == fresh
    assert result["parent_plan_id"] == original["id"]
    assert result["target_date"] == "2026-09-20"


def test_model_successor_cannot_change_target_or_workout_identity(setup):
    client, model = setup
    install_current_model(model)
    original = post_plan(client, context=current_context())["response"]["session_plan"]
    model.parent = original["id"]
    for context in (
        {"planning_target": "next_workout"},
        current_context(current_workout={**current_snapshot(), "sourceId": "another-device"}),
    ):
        response = client.post("/v1/chat", json={"message": "修改方案", "context": context})
        last = json.loads(response.text.splitlines()[-1])
        assert last["type"] == "error"
    assert client.get(f"/v1/training/plans/{original['id']}").json()["superseded_by"] is None


def test_all_completed_snapshot_is_preserved_when_nothing_is_added(setup):
    client, model = setup
    install_current_model(model, empty=True)
    snapshot = current_snapshot()
    for record in snapshot["workout"]["records"]:
        for item in record["sets"]:
            item["isCompleted"] = True
    result = post_plan(client, context=current_context(current_workout=snapshot))["response"]
    plan = result["session_plan"]
    assert plan["existing_completed_sets"] == 3
    assert plan["existing_pending_sets"] == 0
    assert plan["current_workout"] == snapshot
    assert "无需新增" in result["text"]
    assert "再开始训练" not in result["text"]


def test_next_plan_does_not_retain_current_plan_snapshots_in_model_context(setup):
    client, model = setup
    install_current_model(model)
    post_plan(client, context=current_context())
    post_plan(client, context={"planning_target": "next_workout"})
    for captured in [model.requests[-1], model.decision_requests[-1]]:
        assert all(p["plan"].get("current_workout") is None for p in captured.context.session_plans)


async def test_real_model_boundary_excludes_current_exercises_and_explains_target():
    from test_expert_skills import model_with_capture

    model, capture = model_with_capture(SessionDraft.model_validate(additions()))
    await model.session_plan(current_request(), Catalog.load())
    payload = json.loads(capture.calls[-1][-1].content)
    assert "0289" not in {exercise["id"] for exercise in payload["catalog"]}
    assert payload["planning"]["existing_completed_sets"] == 1
    assert payload["planning"]["existing_pending_sets"] == 2
    assert payload["time_budget_minutes"] == 50
    assert "只返回需要新增的动作" in capture.calls[-1][0].content


def test_current_revision_uses_new_work_budget_instead_of_profile_duration(setup):
    client, model = setup
    install_current_model(model)
    original = post_plan(client, context=current_context(remaining_minutes=60))["response"][
        "session_plan"
    ]
    data = edit_payload(original)
    for item in data["items"]:
        for row in item["set_prescriptions"]:
            row["rest_seconds"] = 900
    result = client.post(f"/v1/training/plans/{original['id']}/revision", json=data)
    assert result.status_code == 422


@pytest.mark.parametrize("target", ["current_workout", "next_workout"])
def test_target_date_survives_many_nonplanning_turns_and_ignores_client_echo(setup, target):
    client, model = setup
    install_current_model(model, target_date=date(2026, 9, 20))
    context = (
        current_context(remaining_minutes=None)
        if target == "current_workout"
        else {"planning_target": target}
    )
    first = post_plan(client, context=context)

    async def discussion_plan(*args):
        return SearchPlan(kind="chat")

    async def reply(*args):
        yield "按你的节奏讨论。"

    model.plan, model.stream_reply = discussion_plan, reply
    for _ in range(16):
        post_plan(client, conversation_id=first["conversation_id"], message="继续讨论")
    install_current_model(model)
    final = post_plan(
        client,
        conversation_id=first["conversation_id"],
        context={"planning_target_date": "2030-01-01"},
    )["response"]
    result = final["plan_preview"] if target == "current_workout" else final["session_plan"]
    assert result["planning_target"] == target
    assert result["target_date"] == "2026-09-20"


def test_empty_current_successor_can_remain_reduced_without_fabricating_new_work():
    candidate = additions()
    candidate.update(items=[])
    original = validate(candidate)
    candidate["adjustment"] = "reduce"
    result = validate_session_plan(
        SessionDraft.model_validate(candidate),
        current_request(),
        Catalog.load(),
        original_plan=original,
    )
    assert result["outcome"] == "nothing_to_add"
    assert result["current_workout"] == current_snapshot()


def test_empty_current_rest_revisions_keep_rest_outcome():
    from fitness_bot.plan_revisions import PlanRevisionInput, revised_plan

    candidate = additions()
    candidate.update(items=[], adjustment="rest")
    original = validate(candidate)
    first = revised_plan(
        original,
        PlanRevisionInput(revision_id=uuid4(), expected_revision=0, items=[]),
        Catalog.load(),
    )
    second = revised_plan(
        first, PlanRevisionInput(revision_id=uuid4(), expected_revision=0, items=[]), Catalog.load()
    )
    assert first["outcome"] == second["outcome"] == "rest"
