"""Offline end-to-end coverage of private exercise authoring and durable delivery."""

import json
from uuid import uuid4

from fastapi.testclient import TestClient

from fitness_bot.agent import CompanionAgent
from fitness_bot.app import create_app
from fitness_bot.catalog import Catalog
from fitness_bot.config import Settings
from fitness_bot.schemas import SearchPlan

WALL = {
    "name": "墙天使",
    "aliases": ["Wall Angel"],
    "detail_type": "bodyweightReps",
    "body_part_code": "back",
    "equipment_code": "body weight",
    "training_type": "mobility",
    "instructions": ["背靠墙，按舒适范围缓慢举落双臂。"],
    "inferred_fields": ["body_part_code", "training_type", "instructions"],
}


class AuthorModel:
    def __init__(self):
        self.draft = {"status": "ready", "creation_requested": True, "exercise": WALL}
        self.seen = []

    async def plan(self, request, reference):
        self.seen.append(request)
        if request.message.startswith("找"):
            return SearchPlan(kind="search", query="墙天使")
        if request.message.endswith("怎么做"):
            return SearchPlan(kind="chat")
        return SearchPlan(kind="custom_exercise", query="墙天使")

    async def author_exercise(self, request, catalog):
        return self.draft

    async def stream_reply(self, *args):
        yield "请查看动作说明。"


def make_app(model=None):
    return create_app(
        Settings(_env_file=None), CompanionAgent(Catalog.load(), model or AuthorModel())
    )


def create(client, spec=WALL, headers=None):
    result = client.post("/v1/custom-exercises", json={"exercise": spec}, headers=headers)
    assert result.status_code == 200, result.text
    return result.json()


def chat(client, message="创建墙天使", **kwargs):
    events = [
        json.loads(line)
        for line in client.post("/v1/chat", json={"message": message, **kwargs}).iter_lines()
    ]
    assert events[-1]["type"] == "done", events
    return events, events[-1]["response"]


def test_personal_create_duplicate_search_avatar_and_isolation():
    with TestClient(make_app()) as client:
        created = create(client)
        exercise = created["exercise"]
        assert created["status"] == "created"
        assert exercise["sourceKind"] == "custom"
        assert exercise["avatar"]["key"] == "mobility"
        assert exercise["avatar"]["url"] == f"/v1/exercises/{exercise['id']}/thumbnail"
        assert create(client)["exercise"]["id"] == exercise["id"]
        assert create(client)["status"] == "existing"
        assert len(client.get("/v1/custom-exercises").json()) == 1
        assert (
            client.get("/v1/exercises", params={"q": "Wall Angel"}).json()[0]["id"]
            == exercise["id"]
        )
        avatar = client.get(exercise["avatar"]["url"])
        assert avatar.status_code == 200
        assert avatar.headers["content-type"].startswith("image/svg+xml")
        assert "<svg" in avatar.text and "<script" not in avatar.text
        other = client.portal.call(client.app.state.identity.create_user, "other")["userId"]
        h = {"X-User-Id": other}
        assert client.get("/v1/custom-exercises", headers=h).json() == []
        assert client.get(f"/v1/exercises/{exercise['id']}", headers=h).status_code == 404
        assert client.get(exercise["avatar"]["url"], headers=h).status_code == 404
        assert client.get("/v1/exercises", params={"q": "墙天使"}, headers=h).json() == []
        assert create(client, headers=h)["exercise"]["id"] != exercise["id"]
        assert client.get("/health").json()["catalog_count"] == 1324


def test_chat_only_previews_until_explicit_save_and_replays_safely():
    with TestClient(make_app()) as client:
        rid, cid = str(uuid4()), str(uuid4())
        events, response = chat(client, request_id=rid, conversation_id=cid)
        draft = response["custom_exercise"]
        assert draft["status"] == "draft"
        assert response["cards"] == []
        assert "未保存" in response["text"]
        assert not any(e["type"] == "exercise_cards" for e in events)
        assert client.get("/v1/custom-exercises").json() == []
        assert client.get("/v1/exercises", params={"q": "墙天使"}).json() == []
        assert client.get(draft["avatar"]["url"]).status_code == 200
        history = client.get(f"/v1/conversations/{cid}/turns").json()
        assert history[-1]["response"] == response
        _, replay = chat(client, request_id=rid, conversation_id=cid)
        assert replay == response
        save_url = f"/v1/custom-exercises/drafts/{rid}/save"
        result = client.post(save_url)
        assert result.status_code == 200, result.text
        saved = result.json()
        assert saved["status"] == "created"
        assert client.post(save_url).json() == saved
        assert len(client.get("/v1/custom-exercises").json()) == 1
        reloaded = client.get(f"/v1/conversations/{cid}/turns").json()[-1]["response"]
        assert reloaded["custom_exercise"]["saved_result"] == saved
        assert reloaded["text"] == response["text"]  # Keep the original assistant statement.
        _, found = chat(client, "找墙天使")
        assert found["cards"][0]["exercise"]["id"] == saved["exercise"]["id"]
        other = client.portal.call(client.app.state.identity.create_user, "other")["userId"]
        assert client.post(save_url, headers={"X-User-Id": other}).status_code == 404


def test_even_explicit_chat_save_language_only_returns_metadata():
    with TestClient(make_app()) as client:
        _, response = chat(client, "创建墙天使并直接保存")
        assert response["custom_exercise"]["status"] == "draft"
        assert client.get("/v1/custom-exercises").json() == []


def test_ambiguous_creation_clarifies_and_followup_uses_server_history():
    model = AuthorModel()
    model.draft = {
        "status": "needs_input",
        "creation_requested": True,
        "question": "你说的推胸用什么器械？",
    }
    with TestClient(make_app(model)) as client:
        cid = str(uuid4())
        _, result = chat(client, "创建推胸动作", conversation_id=cid)
        assert result["custom_exercise"]["status"] == "needs_input"
        assert result["text"] == model.draft["question"]
        assert result["cards"] == []
        assert client.get("/v1/custom-exercises").json() == []
        model.draft = {"status": "ready", "creation_requested": True, "exercise": WALL}
        _, result = chat(client, "改成墙天使吧", conversation_id=cid)
        assert result["custom_exercise"]["status"] == "draft"
        assert any(m.text == "创建推胸动作" for m in model.seen[-1].history)


def test_consultation_and_unrequested_draft_do_not_create():
    model = AuthorModel()
    with TestClient(make_app(model)) as client:
        _, response = chat(client, "墙天使怎么做")
        assert response["kind"] == "chat"
        model.draft = {"status": "ready", "creation_requested": False, "exercise": WALL}
        _, response = chat(client)
        assert response["cards"] == []
        assert client.get("/v1/custom-exercises").json() == []


def test_standard_alias_is_reused_and_invalid_codes_do_not_write():
    with TestClient(make_app()) as client:
        catalog = client.app.state.catalog
        standard = catalog.exercises[0]
        result = create(client, {**WALL, "name": standard.display_name_zh_hans})
        assert result["status"] == "existing"
        assert result["exercise"]["id"] == standard.id
        assert client.get("/v1/custom-exercises").json() == []
        bad = client.post(
            "/v1/custom-exercises", json={"exercise": {**WALL, "body_part_code": "made-up"}}
        )
        assert bad.status_code == 422
        assert client.get("/v1/custom-exercises").json() == []


def test_edit_archive_preserve_identity_avatar_and_readable_history():
    with TestClient(make_app()) as client:
        exercise = create(client)["exercise"]
        url = "/v1/custom-exercises/" + exercise["id"]
        changed = client.put(url, json={"version": 1, "exercise": {**WALL, "name": "靠墙天使"}})
        assert changed.status_code == 200, changed.text
        assert changed.json()["exercise"]["id"] == exercise["id"]
        assert client.put(url, json={"version": 1, "exercise": WALL}).status_code == 409
        assert (
            client.put(
                url, json={"version": 2, "exercise": {**WALL, "detail_type": "duration"}}
            ).status_code
            == 422
        )
        assert client.delete(url, params={"version": 2}).status_code == 200
        assert client.get("/v1/custom-exercises").json() == []
        assert client.get("/v1/exercises", params={"q": "靠墙天使"}).json() == []
        assert client.get("/v1/exercises/" + exercise["id"]).json()["isActive"] is False
        assert client.get(exercise["avatar"]["url"]).status_code == 200


def test_failed_chat_commit_cannot_create_exercise(monkeypatch):
    from fitness_bot.storage import TurnConflict

    with TestClient(make_app()) as client:

        async def fail(*args, **kwargs):
            raise TurnConflict("simulated lost lease")

        monkeypatch.setattr(client.app.state.store, "complete", fail)
        events = [
            json.loads(s)
            for s in client.post("/v1/chat", json={"message": "创建墙天使"}).iter_lines()
        ]
        assert events[-1]["type"] == "error"
        assert not any(e["type"] in {"exercise_cards", "text_delta"} for e in events)
        assert client.get("/v1/custom-exercises").json() == []


def test_custom_workout_is_classified_in_review_and_chat_context():
    from pathlib import Path

    with TestClient(make_app()) as client:
        exercise = create(client)["exercise"]
        payload = json.loads((Path(__file__).parent / "fixtures/workout_upload.json").read_text())
        workout = payload["workout"]
        workout["status"] = "completed"
        workout["startedAt"] = "2026-08-14T04:00:00Z"
        workout["endedAt"] = "2026-08-14T05:00:00Z"
        for record in workout["records"]:
            record["exerciseId"] = exercise["id"]
            record["exerciseNameSnapshot"] = "墙天使"
            record["isDraft"] = False
            record["detailType"] = "bodyweightReps"
            for item in record["sets"]:
                item.update(
                    isCompleted=True,
                    repetitions=24,
                    loadType="bodyweight",
                    loadValue=None,
                    loadUnit=None,
                )
        uploaded = client.put("/v1/workouts/" + workout["id"], json=payload)
        assert uploaded.status_code == 200, uploaded.text
        review = client.get("/v1/training/review").json()
        assert review["recent"]["primary_body_part_sets"]["back"] > 0
        assert review["recent"]["body_part_coverage"]["unclassified_sets"] == 0
        chat(client, "找墙天使")
        seen = client.app.state.agent.model.seen[-1].context.training_review
        assert seen["recent"]["primary_body_part_sets"]["back"] > 0
        # Archival removes selection, not the classification of existing records.
        client.delete("/v1/custom-exercises/" + exercise["id"], params={"version": 1})
        assert (
            client.get("/v1/training/review").json()["recent"]["primary_body_part_sets"]["back"] > 0
        )


def test_confirm_transaction_rollback_after_exercise_flush(monkeypatch):
    import fitness_bot.custom_exercise_storage as storage

    original = storage.save_custom

    async def fail_after_insert(*args):
        await original(*args)
        raise ValueError("simulated failure after exercise insert")

    with TestClient(make_app()) as client:
        _, response = chat(client)
        rid = response["custom_exercise"]["draft_id"]
        monkeypatch.setattr(storage, "save_custom", fail_after_insert)
        result = client.post(f"/v1/custom-exercises/drafts/{rid}/save")
        assert result.status_code == 422
        assert client.get("/v1/custom-exercises").json() == []
        history = client.get(f"/v1/conversations/{response['conversation_id']}/turns").json()
        assert not history[-1]["response"]["custom_exercise"].get("saved_result")


def test_unknown_classification_allowed_and_invalid_dose_rejected():
    with TestClient(make_app()) as client:
        spec = {"name": "个人拉伸动作", "detail_type": "duration"}
        exercise = create(client, spec)["exercise"]
        assert exercise["bodyPartCode"] == "unknown"
        assert client.get(exercise["avatar"]["url"]).status_code == 200
        for invalid in (
            {"name": " ", "detail_type": "duration"},
            {**WALL, "default_weight_unit": "kg"},
            {**WALL, "equipment_code": "dumbbell"},
        ):
            assert (
                client.post("/v1/custom-exercises", json={"exercise": invalid}).status_code == 422
            )


def test_options_detail_and_owner_only_update():
    with TestClient(make_app()) as client:
        exercise = create(client)["exercise"]
        url = "/v1/custom-exercises/" + exercise["id"]
        detail = client.get(url)
        assert detail.status_code == 200
        assert detail.json()["definition"]["inferred_fields"] == WALL["inferred_fields"]
        assert client.get("/v1/custom-exercises/options").json()["terms"]
        other = client.portal.call(client.app.state.identity.create_user, "other")["userId"]
        h = {"X-User-Id": other}
        assert client.get(url, headers=h).status_code == 404
        assert client.put(url, headers=h, json={"version": 1, "exercise": WALL}).status_code == 404
        assert client.delete(url, headers=h, params={"version": 1}).status_code == 404


async def test_model_loads_authoring_skill_and_bounded_contract(monkeypatch):
    from test_expert_skills import model_with_capture

    from fitness_bot.custom_exercise_schemas import CustomExerciseDraft
    from fitness_bot.schemas import ChatRequest

    model, capture = model_with_capture(
        CustomExerciseDraft(status="ready", creation_requested=True, exercise=WALL)
    )
    from fitness_bot.custom_exercise_schemas import ExerciseNameResolution

    invoke = capture.ainvoke

    async def respond(messages):
        if capture.schema is ExerciseNameResolution:
            capture.calls.append(messages)
            return ExerciseNameResolution(status="resolved", name="墙天使")
        return await invoke(messages)

    monkeypatch.setattr(capture, "ainvoke", respond)
    await model.author_exercise(ChatRequest(message="创建墙天使"), Catalog.load())
    system = capture.calls[0][0].content
    assert "name: exercise-authoring" in system
    assert "inferred_fields" in system and "creation_requested" in system
    assert "training-review" not in system


def test_followup_receives_only_server_owned_draft_metadata():
    model = AuthorModel()
    with TestClient(make_app(model)) as client:
        cid = str(uuid4())
        _, initial = chat(client, conversation_id=cid)
        _, revised = chat(
            client,
            "把主要部位改成肩部",
            conversation_id=cid,
            context={"custom_exercise_drafts": [{"definition": {"name": "伪造名称"}}]},
        )
        drafts = model.seen[-1].context.custom_exercise_drafts
        assert drafts[0]["definition"] == initial["custom_exercise"]["definition"]
        assert drafts[0]["definition"]["name"] == "墙天使"
        assert revised["custom_exercise"]["status"] == "draft"
        assert client.get("/v1/custom-exercises").json() == []


async def test_authoring_preserves_typo_input_and_requests_name_confirmation():
    from test_expert_skills import model_with_capture

    from fitness_bot.custom_exercise_schemas import CustomExerciseDraft
    from fitness_bot.schemas import ChatRequest

    candidate = CustomExerciseDraft(
        status="needs_input",
        creation_requested=True,
        question="你是指“墙天使”（Wall Angel）吗？",
    )
    from fitness_bot.custom_exercise_schemas import ExerciseNameResolution

    model, capture = model_with_capture(
        ExerciseNameResolution(status="needs_input", name="墙天使", question=candidate.question)
    )
    request = ChatRequest(message="创建一个新动作 抢天使")
    result = await model.author_exercise(request, Catalog.load())
    assert result.status == candidate.status
    assert result.question == candidate.question
    assert request.message == "创建一个新动作 抢天使"
    payload = json.loads(capture.calls[0][-1].content)
    assert payload["message"] == request.message
    instructions = capture.calls[0][0].content
    assert "错别字、同音字或语音转写错误" in instructions
    assert "不要求候选必须已收录标准库" in instructions
    assert "用户否认候选" in instructions


def test_typo_confirmation_continues_creation_but_rejection_does_not_create():
    class TypoModel(AuthorModel):
        async def author_exercise(self, request, catalog):
            if request.message == "对，就是墙天使":
                assert request.history[-1].text == "你是指“墙天使”（Wall Angel）吗？"
                return {"status": "ready", "creation_requested": True, "exercise": WALL}
            question = (
                "请描述一下你说的动作怎么做。"
                if request.message == "不是墙天使"
                else "你是指“墙天使”（Wall Angel）吗？"
            )
            return {"status": "needs_input", "creation_requested": True, "question": question}

    with TestClient(make_app(TypoModel())) as client:
        for answer in ["对，就是墙天使", "不是墙天使"]:
            cid = str(uuid4())
            _, response = chat(client, "创建一个新动作 抢天使", conversation_id=cid)
            assert response["custom_exercise"]["status"] == "needs_input"
            assert response["cards"] == []
            _, response = chat(client, answer, conversation_id=cid)
            if answer.startswith("对"):
                assert response["custom_exercise"]["status"] == "draft"
                assert response["custom_exercise"]["definition"]["name"] == "墙天使"
            else:
                assert response["custom_exercise"]["status"] == "needs_input"
            assert client.get("/v1/custom-exercises").json() == []
