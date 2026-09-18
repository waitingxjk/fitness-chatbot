"""Draft edits are durable chat metadata, never implicit catalog writes."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_custom_exercises import WALL, AuthorModel, chat, make_app


def test_edit_draft_then_confirm_exact_version_and_restore_history():
    with TestClient(make_app()) as client:
        cid = str(uuid4())
        _, reply = chat(client, conversation_id=cid)
        draft = reply["custom_exercise"]
        url = "/v1/custom-exercises/drafts/" + draft["draft_id"]
        changed = {
            **WALL,
            "name": "靠墙天使",
            "body_part_code": "shoulders",
            "detail_type": "duration",
            "aliases": ["Wall Slide"],
        }
        response = client.put(url, json={"version": 1, "exercise": changed})
        assert response.status_code == 200, response.text
        updated = response.json()
        assert updated["version"] == 2
        assert updated["definition"]["name"] == "靠墙天使"
        assert updated["labels"]["body_part"] == "肩部"
        assert client.get("/v1/custom-exercises").json() == []
        restored = client.get(f"/v1/conversations/{cid}/turns").json()[-1]["response"]
        assert restored["custom_exercise"] == updated
        assert client.put(url, json={"version": 1, "exercise": WALL}).status_code == 409
        assert client.post(url + "/save", json={"version": 1}).status_code == 409
        # Old clients must not save a newer draft they have not reviewed.
        assert client.post(url + "/save").status_code == 409
        saved = client.post(url + "/save", json={"version": 2})
        assert saved.status_code == 200, saved.text
        assert saved.json()["definition"]["name"] == "靠墙天使"
        assert saved.json()["exercise"]["detailType"] == "duration"
        assert client.post(url + "/save", json={"version": 2}).json() == saved.json()
        assert client.post(url + "/save", json={"version": 1}).status_code == 409
        assert client.put(url, json={"version": 2, "exercise": WALL}).status_code == 409
        assert len(client.get("/v1/custom-exercises").json()) == 1


def test_draft_edit_checks_owner_and_fields():
    with TestClient(make_app()) as client:
        _, reply = chat(client)
        url = "/v1/custom-exercises/drafts/" + reply["custom_exercise"]["draft_id"]
        other = client.portal.call(client.app.state.identity.create_user, "other")["userId"]
        assert (
            client.put(
                url, headers={"X-User-Id": other}, json={"version": 1, "exercise": WALL}
            ).status_code
            == 404
        )
        assert (
            client.put(
                url, json={"version": 1, "exercise": {**WALL, "body_part_code": "invented"}}
            ).status_code
            == 422
        )
        assert client.get("/v1/custom-exercises").json() == []


@pytest.mark.parametrize("bad_id", ["not-a-catalog-id", str(uuid4())])
def test_invalid_existing_reference_does_not_fail_chat(bad_id):
    model = AuthorModel()
    model.draft = {"status": "existing", "existing_exercise_id": bad_id}
    with TestClient(make_app(model)) as client:
        _, reply = chat(client, "是墙天使")
        assert reply["custom_exercise"]["status"] == "needs_input"
        assert reply["text"] and reply["cards"] == []
        assert client.get("/v1/custom-exercises").json() == []


async def test_uncertain_name_resolution_stops_before_catalog_completion():
    from test_expert_skills import model_with_capture

    from fitness_bot.catalog import Catalog
    from fitness_bot.custom_exercise_schemas import ExerciseNameResolution
    from fitness_bot.schemas import ChatRequest

    result = ExerciseNameResolution(
        status="needs_input",
        input_name="飞鸟",
        question="你指的是胸部飞鸟还是反向飞鸟？",
    )
    model, capture = model_with_capture(result)
    reply = await model.author_exercise(ChatRequest(message="创建一个飞鸟动作"), Catalog.load())
    assert reply.status == "needs_input"
    assert "反向飞鸟" in reply.question
    assert len(capture.calls) == 1


async def test_corrected_name_retries_invalid_catalog_reference(monkeypatch):
    from test_expert_skills import model_with_capture

    from fitness_bot.catalog import Catalog
    from fitness_bot.custom_exercise_schemas import CustomExerciseDraft, ExerciseNameResolution
    from fitness_bot.schemas import ChatRequest

    model, _ = model_with_capture(None)
    calls = []
    results = [
        ExerciseNameResolution(status="resolved", input_name="墙天使", name="墙天使"),
        CustomExerciseDraft(status="existing", existing_exercise_id="old-draft-id"),
        CustomExerciseDraft(status="ready", creation_requested=True, exercise=WALL),
    ]

    async def decision(request, schema, instruction, extra=None, **kwargs):
        calls.append(extra)
        return results.pop(0)

    monkeypatch.setattr(model, "_decision", decision)
    request = ChatRequest(
        message="是墙天使",
        context={
            "custom_exercise_drafts": [
                {"draft_id": "old-draft-id", "definition": {**WALL, "name": "强天使"}}
            ]
        },
    )
    reply = await model.author_exercise(request, Catalog.load())
    assert reply.exercise.name == "墙天使"
    assert calls[1]["resolved_name"] == "墙天使"
    assert "unavailable" in calls[2]["validation_error"]
    assert "old-draft-id" not in {e["id"] for e in calls[1]["existing_exercises"]}


@pytest.mark.parametrize("bad_status", ["existing", "ready"])
async def test_bounded_repair_never_returns_bad_id_or_wrong_name(monkeypatch, bad_status):
    from test_expert_skills import model_with_capture

    from fitness_bot.catalog import Catalog
    from fitness_bot.custom_exercise_schemas import CustomExerciseDraft, ExerciseNameResolution
    from fitness_bot.schemas import ChatRequest

    model, _ = model_with_capture(None)
    calls = []

    async def decision(request, schema, instruction, extra=None, **kwargs):
        calls.append(schema)
        if schema is ExerciseNameResolution:
            return ExerciseNameResolution(status="resolved", name="墙天使")
        if bad_status == "existing":
            return CustomExerciseDraft(status="existing", existing_exercise_id="bad-id")
        return CustomExerciseDraft(
            status="ready", creation_requested=True, exercise={**WALL, "name": "强天使"}
        )

    monkeypatch.setattr(model, "_decision", decision)
    reply = await model.author_exercise(ChatRequest(message="是墙天使"), Catalog.load())
    assert reply.status == "needs_input"
    assert len(calls) == 3
    assert reply.exercise is None
    assert reply.existing_exercise_id is None


async def test_confident_correction_proceeds_and_removes_typo_aliases(monkeypatch):
    from test_expert_skills import model_with_capture

    from fitness_bot.catalog import Catalog
    from fitness_bot.custom_exercise_schemas import CustomExerciseDraft, ExerciseNameResolution
    from fitness_bot.schemas import ChatRequest

    model, _ = model_with_capture(None)
    results = [
        ExerciseNameResolution(
            status="resolved",
            input_name="强天使",
            name="墙天使",
            is_correction=True,
            discarded_names=["强天使"],
        ),
        CustomExerciseDraft(
            status="ready",
            creation_requested=True,
            exercise={**WALL, "aliases": ["强天使动作", "强天使", "Wall Angel", "靠墙滑臂"]},
        ),
    ]

    async def decision(*args, **kwargs):
        return results.pop(0)

    monkeypatch.setattr(model, "_decision", decision)
    reply = await model.author_exercise(ChatRequest(message="创建一个强天使动作"), Catalog.load())
    assert not results
    assert reply.status == "ready"
    assert reply.exercise.name == "墙天使"
    assert reply.exercise.aliases == ["Wall Angel", "靠墙滑臂"]
    assert reply.name_correction.resolved_name == "墙天使"


def test_correction_notice_is_delivered_with_unsaved_card():
    model = AuthorModel()
    model.draft = {
        "status": "ready",
        "creation_requested": True,
        "exercise": WALL,
        "name_correction": {"input_name": "强天使", "resolved_name": "墙天使"},
    }
    with TestClient(make_app(model)) as client:
        _, reply = chat(client, "创建一个强天使动作")
        assert reply["custom_exercise"]["status"] == "draft"
        assert "我按「墙天使」理解" in reply["text"]
        assert reply["custom_exercise"]["name_correction"]["input_name"] == "强天使"
        assert client.get("/v1/custom-exercises").json() == []


async def test_intentional_custom_name_is_not_corrected_or_removed(monkeypatch):
    from test_expert_skills import model_with_capture

    from fitness_bot.catalog import Catalog
    from fitness_bot.custom_exercise_schemas import CustomExerciseDraft, ExerciseNameResolution
    from fitness_bot.schemas import ChatRequest

    model, _ = model_with_capture(None)
    replies = [
        ExerciseNameResolution(status="resolved", input_name="强天使", name="强天使"),
        CustomExerciseDraft(
            status="ready",
            creation_requested=True,
            exercise={**WALL, "name": "强天使", "aliases": ["强天使动作"]},
        ),
    ]

    async def decision(*args, **kwargs):
        return replies.pop(0)

    monkeypatch.setattr(model, "_decision", decision)
    result = await model.author_exercise(ChatRequest(message="就叫强天使，别改名"), Catalog.load())
    assert result.exercise.name == "强天使"
    assert result.exercise.aliases == ["强天使动作"]
    assert result.name_correction is None


async def test_similar_catalog_exercise_does_not_replace_corrected_exercise(monkeypatch):
    from test_expert_skills import model_with_capture

    from fitness_bot.catalog import Catalog
    from fitness_bot.custom_exercise_schemas import (
        CustomExerciseDraft,
        ExerciseNameResolution,
        ExistingExerciseMatch,
    )
    from fitness_bot.schemas import ChatRequest

    model, _ = model_with_capture(None)
    calls = []
    replies = [
        ExerciseNameResolution(status="resolved", name="墙天使"),
        CustomExerciseDraft(status="existing", existing_exercise_id="1365"),
        ExistingExerciseMatch(same_exercise=False),
        CustomExerciseDraft(status="ready", creation_requested=True, exercise=WALL),
    ]

    async def decision(request, schema, instruction, extra=None, **kwargs):
        calls.append(extra)
        return replies.pop(0)

    monkeypatch.setattr(model, "_decision", decision)
    result = await model.author_exercise(ChatRequest(message="是墙天使"), Catalog.load())
    assert result.status == "ready"
    assert result.exercise.name == "墙天使"
    assert calls[2]["candidate"]["displayNameZhHans"] == "上部背部拉伸"
    assert "different exercise" in calls[3]["validation_error"]
