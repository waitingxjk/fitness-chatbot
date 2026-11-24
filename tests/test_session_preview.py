"""A current-workout request missing its addition budget remains a non-executable preview."""

import json
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from fitness_bot.agent import CompanionAgent
from fitness_bot.app import create_app
from fitness_bot.catalog import Catalog
from fitness_bot.config import Settings
from fitness_bot.schemas import SearchPlan


class PreviewModel:
    def __init__(self):
        self.preview_calls = 0
        self.items = [
            {"exercise_id": "0289", "purpose": "作为推的重点候选"},
            {"exercise_id": "0180", "purpose": "补充水平拉的候选"},
        ]

    async def plan(self, request, reference):
        return SearchPlan(kind="session_plan", session_inputs={"equipment": ["dumbbell", "cable"]})

    async def session_preview(self, request, candidates):
        self.preview_calls += 1
        assert {"0289", "0180"}.issubset({exercise.id for exercise in candidates})
        return {
            "theme": "上肢训练候选",
            "rationale": "先讨论推拉搭配，具体需求待确认。",
            "items": self.items,
        }

    async def stream_reply(self, *args):
        yield "旧版纯文字回退"

    async def session_plan(self, *args):
        raise AssertionError("Incomplete profile cannot create an executable plan")


def missing_current_budget():
    from test_planning_targets import current_context

    context = current_context(remaining_minutes=None)
    context["current_workout"]["workout"]["records"] = []
    return context


def test_missing_current_budget_returns_ordered_preview_cards_and_persists_them():
    model = PreviewModel()
    with TestClient(
        create_app(Settings(_env_file=None), CompanionAgent(Catalog.load(), model))
    ) as c:
        r = c.post(
            "/v1/chat", json={"message": "补全本次训练", "context": missing_current_budget()}
        )
        events = [json.loads(line) for line in r.text.splitlines()]
        done = events[-1]
        assert done["type"] == "done"
        response = done["response"]
        assert [card["exercise"]["id"] for card in response["cards"]] == ["0289", "0180"]
        assert all(not card["can_add_to_active_workout"] for card in response["cards"])
        assert all(card["set_prescriptions"] == [] for card in response["cards"])
        assert response["session_plan"] is None
        assert response["suggestion_kind"] is None
        assert response["plan_preview"]["status"] == "needs_input"
        assert "新增安排" in response["plan_preview"]["next_question"]
        assert "待完善" in response["text"]
        assert "旧版纯文字" not in response["text"]
        assert model.preview_calls == 1
        assert c.get("/v1/training/plans").json() == []
        assert c.get("/v1/training/profile").json()["version"] == 0
        saved = c.get(f"/v1/conversations/{done['conversation_id']}/turns").json()[-1]
        assert saved["response"]["plan_preview"] == response["plan_preview"]
        assert saved["response"]["cards"] == response["cards"]


def test_preview_excludes_unknown_exercises_without_failing_the_turn():
    model = PreviewModel()
    model.items = [{"exercise_id": "unknown-exercise", "purpose": "候选"}]
    with TestClient(
        create_app(Settings(_env_file=None), CompanionAgent(Catalog.load(), model))
    ) as c:
        events = [
            json.loads(line)
            for line in c.post(
                "/v1/chat", json={"message": "安排训练", "context": missing_current_budget()}
            ).text.splitlines()
        ]
        assert events[-1]["type"] == "done"
        assert events[-1]["response"]["cards"] == []
        assert events[-1]["response"]["plan_preview"]["status"] == "needs_input"
        assert not any(event["type"] == "exercise_cards" for event in events)


def test_preview_preserves_valid_cards_when_other_model_ids_are_invalid_or_duplicate():
    model = PreviewModel()
    model.items = [
        {"exercise_id": "unknown-a", "purpose": "候选"},
        {"exercise_id": "0289", "purpose": "推的候选"},
        {"exercise_id": "unknown-b", "purpose": "候选"},
        {"exercise_id": "0289", "purpose": "重复候选"},
        {"exercise_id": "0180", "purpose": "拉的候选"},
        {"exercise_id": "0001", "purpose": "真实但不在可用器械范围内"},
    ]
    with TestClient(
        create_app(Settings(_env_file=None), CompanionAgent(Catalog.load(), model))
    ) as c:
        done = json.loads(
            c.post(
                "/v1/chat", json={"message": "重新生成", "context": missing_current_budget()}
            ).text.splitlines()[-1]
        )
        assert done["type"] == "done"
        cards = done["response"]["cards"]
        assert [card["exercise"]["id"] for card in cards] == ["0289", "0180"]
        assert all(not card["can_add_to_active_workout"] for card in cards)
        assert "只展示已匹配当前可用动作库的候选" in done["response"]["text"]


def test_reported_pain_never_generates_workout_preview_cards():
    model = PreviewModel()
    with TestClient(
        create_app(Settings(_env_file=None), CompanionAgent(Catalog.load(), model))
    ) as c:
        c.put(
            "/v1/training/profile",
            json={
                "expected_version": 0,
                "profile": {
                    "readiness": {"observed_at": datetime.now(UTC).isoformat(), "pain": "present"},
                },
            },
        )
        done = json.loads(c.post("/v1/chat", json={"message": "安排训练"}).text.splitlines()[-1])
        assert done["type"] == "done"
        assert done["response"]["cards"] == []
        assert done["response"]["plan_preview"]["status"] == "blocked"
        assert model.preview_calls == 0


def test_preview_candidate_scope_uses_known_history_when_equipment_is_unknown():
    from fitness_bot.schemas import ChatRequest
    from fitness_bot.session_preview import preview_candidates

    request = ChatRequest(
        message="安排训练",
        context={
            "workout_history": {
                "total_workouts": 1,
                "workouts": [
                    {
                        "records": [
                            {"exercise_id": "0289"},
                            {"exercise_id": "custom-unresolved"},
                        ]
                    }
                ],
            }
        },
    )
    assert [e.id for e in preview_candidates(request, Catalog.load())] == ["0289"]


def test_preview_draft_cannot_carry_unvalidated_prescriptions_or_narrative_loads():
    import pytest

    from fitness_bot.training_schemas import SessionPreviewDraft

    base = {
        "theme": "上肢候选",
        "rationale": "先讨论推拉搭配。",
        "items": [
            {"exercise_id": "0289", "purpose": "推的候选"},
        ],
    }
    with pytest.raises(ValueError):
        SessionPreviewDraft.model_validate(
            {
                **base,
                "items": [
                    {**base["items"][0], "set_prescriptions": [{"repetitions": 8}]},
                ],
            }
        )
    with pytest.raises(ValueError):
        SessionPreviewDraft.model_validate({**base, "rationale": "使用 20 kg。"})


async def test_model_preview_preserves_cards_when_narrative_contains_unvalidated_doses():
    from fitness_bot.model import OpenAICompanionModel
    from fitness_bot.schemas import ChatRequest

    class PreviewLLM:
        def with_structured_output(self, schema, **kwargs):
            # A JSON schema parses the tool arguments before the application
            # validates qualitative prose, so one bad sentence cannot lose cards.
            assert isinstance(schema, dict)
            assert schema["title"] == "SessionPreviewDraft"
            assert schema["$defs"]["PreviewExercise"]["properties"]["exercise_id"]["enum"] == [
                "0289",
                "0180",
            ]
            return self

        async def ainvoke(self, messages):
            return {
                "theme": "上肢候选",
                "rationale": "根据最近 3 次训练，使用 20 kg。",
                "items": [
                    {"exercise_id": "0289", "purpose": "先做三组"},
                    {"exercise_id": "0180", "purpose": "补充水平拉的候选"},
                ],
            }

    model = OpenAICompanionModel(Settings(_env_file=None, openai_api_key="test-key"))
    model.decision_llm = PreviewLLM()
    catalog = Catalog.load()
    candidates = [catalog.get("0289"), catalog.get("0180")]
    draft = await model.session_preview(ChatRequest(message="重新生成"), candidates)
    assert draft.theme == "上肢候选"
    assert draft.rationale == "先讨论动作候选，具体训练安排待条件确认。"
    assert [item.exercise_id for item in draft.items] == ["0289", "0180"]
    assert draft.items[0].purpose == "动作候选，具体安排待确认。"
    assert draft.items[1].purpose == "补充水平拉的候选"


def test_preview_prose_recovery_does_not_relax_the_structural_contract():
    import pytest

    from fitness_bot.session_preview import qualitative_preview

    with pytest.raises(ValueError):
        qualitative_preview(
            {
                "theme": "候选",
                "rationale": "使用 20 kg。",
                "items": [{"exercise_id": "0289", "purpose": "推", "sets": 3}],
            }
        )
