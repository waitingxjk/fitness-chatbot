from copy import deepcopy

import pytest
from test_training_decision import draft, request

from fitness_bot.catalog import Catalog
from fitness_bot.training_decision import validate_session_plan
from fitness_bot.training_schemas import SessionDraft


def test_legacy_plan_expands_each_working_set_without_inventing_load():
    result = validate_session_plan(SessionDraft.model_validate(draft()), request(), Catalog.load())
    item = result["items"][0]
    assert len(item.get("set_prescriptions", [])) == 3
    assert all(s["kind"] == "working" for s in item["set_prescriptions"])
    assert all(s["load"]["value"] is None for s in item["set_prescriptions"])
    assert [s["repetitions"] for s in item["set_prescriptions"]] == [8, 8, 8]


def test_explicit_sets_drive_summary_and_time_including_warmup():
    data = draft()
    data["items"] = data["items"][:1]
    data["items"][0]["set_prescriptions"] = [
        {"kind": kind, "repetitions": reps, "rest_seconds": rest}
        for kind, reps, rest in [("warmup", 12, 60), ("working", 8, 120), ("working", 6, 90)]
    ]
    result = validate_session_plan(SessionDraft.model_validate(data), request(), Catalog.load())
    item = result["items"][0]
    assert item["sets"] == 2
    assert result["working_sets"] == 2
    assert item["total_sets"] == 3
    assert item["estimated_seconds"] == (12 + 8 + 6) * 4 + 60 + 120 + 90
    assert [s["repetitions"] for s in item["set_prescriptions"]] == [12, 8, 6]
    assert result["estimated_minutes_min"] == 18


def test_per_set_wrong_dose_type_is_rejected():
    data = draft()
    data["items"][0]["set_prescriptions"] = [
        {"duration_seconds": 30, "rest_seconds": 60},
    ]
    with pytest.raises(ValueError, match="Dose incompatible"):
        validate_session_plan(SessionDraft.model_validate(data), request(), Catalog.load())


def test_model_cannot_bypass_effort_limits_through_per_set_values():
    data = draft()
    data["items"][0]["set_prescriptions"] = [
        {"repetitions": 8, "target_rir_min": 0, "target_rir_max": 0, "rest_seconds": 60},
    ]
    with pytest.raises(ValueError):
        SessionDraft.model_validate(data)


def test_user_prescription_validation_requires_appropriate_units():
    from fitness_bot.prescriptions import validate_user_sets

    exercise = Catalog.load().get("0289")
    good = {
        "kind": "working",
        "repetitions": 8,
        "duration_seconds": None,
        "rest_seconds": 90,
        "target_rir_min": 2,
        "target_rir_max": 3,
        "load": {"strategy": "user", "value": 15, "unit": "kg", "convention": "per_hand"},
    }
    rows = validate_user_sets([good], exercise)
    assert rows[0]["load"]["value"] == 15
    wrong = deepcopy(good)
    wrong["load"]["unit"] = "level"
    with pytest.raises(ValueError, match="unit"):
        validate_user_sets([wrong], exercise)
    wrong["load"]["value"] = float("nan")
    with pytest.raises(ValueError):
        validate_user_sets([wrong], exercise)


def test_user_weighted_sets_cannot_mix_units_or_claim_history():
    from fitness_bot.prescriptions import validate_user_sets

    exercise = Catalog.load().get("0289")
    rows = [
        dict(
            kind="working",
            repetitions=8,
            rest_seconds=90,
            load=dict(strategy="user", value=20, unit=unit, convention="per_hand"),
        )
        for unit in ["kg", "lb"]
    ]
    with pytest.raises(ValueError, match="unit"):
        validate_user_sets(rows, exercise)
    rows[1]["load"]["unit"] = "kg"
    rows[0]["load"]["strategy"] = "history"
    result = validate_user_sets(rows, exercise)
    assert result[0]["load"]["strategy"] == "user"


@pytest.mark.asyncio
async def test_single_recommendation_is_one_real_card_with_structured_sets():
    from fitness_bot.agent import CompanionAgent
    from fitness_bot.schemas import ChatRequest, SearchPlan
    from fitness_bot.training_schemas import RecommendationDraft

    class Model:
        async def plan(self, *_):
            return SearchPlan(
                kind="recommendation", query="卧推", equipment="dumbbell", recommendation_count=1
            )

        async def recommendation(self, request, cards):
            return RecommendationDraft(
                items=[
                    {
                        "exercise_id": cards[0].exercise.id,
                        "purpose": "在当前训练中补充推的动作。",
                        "set_prescriptions": [{"repetitions": 8, "rest_seconds": 120}] * 2,
                    }
                ]
            )

        async def stream_reply(self, request, plan, cards, previous):
            assert len(cards[0].set_prescriptions) == 2
            yield "建议见卡片。"

    events = [
        e
        async for e in CompanionAgent(Catalog.load(), Model()).stream(
            ChatRequest(message="推荐一个卧推动作")
        )
    ]
    result = events[-1]["response"]
    assert result["suggestion_kind"] == "single_exercise"
    assert len(result["cards"]) == 1
    assert len(result["cards"][0]["set_prescriptions"]) == 2
    assert result["cards"][0]["set_prescriptions"][0]["load"]["value"] is None


@pytest.mark.asyncio
async def test_recommendation_cannot_substitute_an_unretrieved_exercise():
    from fitness_bot.agent import CompanionAgent
    from fitness_bot.schemas import ChatRequest, SearchPlan
    from fitness_bot.training_schemas import RecommendationDraft

    class Model:
        async def plan(self, *_):
            return SearchPlan(kind="recommendation", query="卧推", equipment="dumbbell")

        async def recommendation(self, *_):
            return RecommendationDraft(
                items=[
                    {
                        "exercise_id": "fake",
                        "purpose": "测试",
                        "set_prescriptions": [{"repetitions": 8, "rest_seconds": 120}],
                    }
                ]
            )

    with pytest.raises(ValueError, match="retrieved"):
        _ = [
            e
            async for e in CompanionAgent(Catalog.load(), Model()).stream(
                ChatRequest(message="推荐动作")
            )
        ]


def test_progression_cannot_claim_all_sets_increased_when_only_one_did():
    from test_training_decision import progression_request

    data = draft()
    data.update(
        adjustment="increase_reps", progression_exercise_id="0289", evidence_ids=["latest", "prior"]
    )
    data["items"][0].update(
        load_strategy="history",
        load_observation_id="latest",
        set_prescriptions=[{"repetitions": n, "rest_seconds": 120} for n in [9, 8, 8]],
    )
    with pytest.raises(ValueError, match="one variable"):
        validate_session_plan(
            SessionDraft.model_validate(data), progression_request(), Catalog.load()
        )


def test_reduced_plan_cannot_hide_increased_work_in_warmup_rows():
    data = draft()
    data["items"] = data["items"][:1]
    original = validate_session_plan(SessionDraft.model_validate(data), request(), Catalog.load())
    data["adjustment"] = "reduce"
    data["items"][0]["set_prescriptions"] = [
        {"kind": "warmup", "repetitions": 30, "rest_seconds": 60} for _ in range(9)
    ] + [{"kind": "working", "repetitions": 8, "rest_seconds": 120} for _ in range(2)]
    with pytest.raises(ValueError, match="Reduced plan"):
        validate_session_plan(
            SessionDraft.model_validate(data),
            request(minutes=90, fatigue="high"),
            Catalog.load(),
            original_plan=original,
        )


def test_recommendation_reason_cannot_contradict_structured_dose_or_load():
    from fitness_bot.training_schemas import ExerciseSuggestionDraft

    with pytest.raises(ValueError, match="structured"):
        ExerciseSuggestionDraft(
            exercise_id="0289",
            purpose="使用100kg做20次，共20组",
            set_prescriptions=[{"repetitions": 8}],
        )
    with pytest.raises(ValueError, match="six"):
        ExerciseSuggestionDraft(
            exercise_id="0289", purpose="补充推的动作", set_prescriptions=[{"repetitions": 8}] * 8
        )
