from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from fitness_bot.agent import CompanionAgent
from fitness_bot.app import create_app
from fitness_bot.catalog import Catalog
from fitness_bot.config import Settings
from fitness_bot.schemas import ChatRequest


def draft():
    return {
        "theme": "上肢，卧推优先",
        "rationale": "优先支持明确的力量目标，先维持要求观察表现。",
        "evidence_ids": ["recent.frequency", "baseline.frequency"],
        "adjustment": "maintain",
        "adjustment_reason": "暂不同时增加重量和组数。",
        "preparation": "熟悉的上肢动态准备和递增热身，不做到明显疲劳。",
        "preparation_minutes": 8,
        "finish_minutes": 3,
        "buffer_minutes": 5,
        "items": [
            {
                "exercise_id": exercise,
                "role": role,
                "purpose": purpose,
                "sets": sets,
                "repetitions": reps,
                "duration_seconds": None,
                "rest_seconds": rest,
                "effort": "保留约 2–3 次余力，不强行做到力竭。",
                "load_strategy": "trial",
                "load_observation_id": None,
                "switch_seconds": 90,
                "seconds_per_rep": 4,
                "alternative_exercise_ids": [],
                "alternative_condition": "器械不可用时重新生成方案并试做选重。",
            }
            for exercise, role, purpose, sets, reps, rest in [
                ("0289", "priority", "提高卧推表现", 3, 8, 120),
                ("0180", "assistance", "上肢拉", 3, 10, 90),
                ("0150", "assistance", "垂直拉", 2, 10, 90),
                ("0334", "supplement", "肩部补充", 2, 12, 60),
            ]
        ],
        "feedback_questions": ["实际完成多少组？", "最后一组余力、疼痛或跳过原因？"],
    }


def request(minutes=50, fatigue="normal"):
    from fitness_bot.training_schemas import TrainingProfile

    return ChatRequest(
        message="推荐明天整次训练",
        context={
            "training_profile": {
                "version": 1,
                "profile": TrainingProfile(
                    goal="strength",
                    priority_exercise_ids=["0289"],
                    experience="experienced",
                    weekly_sessions=3,
                    session_minutes=minutes,
                    equipment=["dumbbell", "cable"],
                    readiness={
                        "observed_at": datetime.now(UTC),
                        "pain": "none",
                        "fatigue": fatigue,
                        "sleep": "normal",
                    },
                ).model_dump(mode="json"),
            },
            "training_review": {
                "recent": {"completed_workouts": 3},
                "baseline": {"completed_workouts": 9},
                "observations": [],
                "generated_at": datetime.now(UTC).isoformat(),
            },
        },
    )


def test_full_plan_preserves_four_real_exercises_and_computes_time():
    from fitness_bot.training_decision import validate_session_plan
    from fitness_bot.training_schemas import SessionDraft

    result = validate_session_plan(SessionDraft.model_validate(draft()), request(), Catalog.load())
    assert len(result["items"]) == 4
    assert result["items"][0]["exercise"]["id"] == "0289"
    assert result["working_sets"] == 10
    assert 30 < result["estimated_minutes_max"] <= 50
    assert result["confirmed_cycle"] is None
    assert result["suggested_adjustment"] == "maintain"
    assert result["rules_version"]
    assert result["ordering_checks"]
    assert result["items"][0]["load"]["value"] is None


@pytest.mark.parametrize(
    "change, message",
    [
        (lambda d: d["items"][0].update(exercise_id="fake"), "catalog"),
        (lambda d: d["items"][0].update(exercise_id="0009"), "equipment"),
        (lambda d: d["items"].reverse(), "priority"),
        (lambda d: d.update(evidence_ids=["invented"]), "evidence"),
        (
            lambda d: d["items"][0].update(load_strategy="history", load_observation_id="invented"),
            "load",
        ),
    ],
)
def test_untrusted_model_plan_must_pass_program_validation(change, message):
    from fitness_bot.training_decision import validate_session_plan
    from fitness_bot.training_schemas import SessionDraft

    data = draft()
    change(data)
    with pytest.raises(ValueError, match=message):
        validate_session_plan(SessionDraft.model_validate(data), request(), Catalog.load())


def test_short_time_removes_low_priority_work_and_keeps_rest():
    from fitness_bot.training_decision import validate_session_plan
    from fitness_bot.training_schemas import SessionDraft

    result = validate_session_plan(
        SessionDraft.model_validate(draft()), request(30), Catalog.load()
    )
    assert result["estimated_minutes_max"] <= 30
    assert result["items"][0]["exercise"]["id"] == "0289"
    assert result["items"][0]["rest_seconds"] == 120
    assert result["removed_items"]


def test_high_fatigue_blocks_progression_and_missing_readiness_asks():
    from fitness_bot.training_decision import plan_question, validate_session_plan
    from fitness_bot.training_schemas import SessionDraft

    data = draft()
    data.update(adjustment="increase_load")
    with pytest.raises(ValueError, match="fatigue"):
        validate_session_plan(
            SessionDraft.model_validate(data), request(fatigue="high"), Catalog.load()
        )
    context = request()
    context.context.training_profile["profile"]["readiness"] = None
    assert "状态" in plan_question(context)
    context.context.training_profile["profile"]["readiness"] = {
        "observed_at": (datetime.now(UTC) - timedelta(days=3)).isoformat(),
        "pain": "none",
    }
    assert "状态" in plan_question(context)
    assert "目标" in plan_question(ChatRequest(message="明天练什么"))


def test_new_intents_stream_and_save_structured_result():
    import json

    from fitness_bot.schemas import SearchPlan

    class ReviewModel:
        async def plan(self, request, reference):
            return SearchPlan(kind="review")

        async def review(self, request):
            from fitness_bot.training_schemas import ReviewDraft

            return ReviewDraft(
                findings=[],
                keep=["继续同步实际训练"],
                next_adjustments=["先确认同步情况"],
                question="最近的训练都同步了吗？",
            )

    with TestClient(
        create_app(Settings(_env_file=None), CompanionAgent(Catalog.load(), ReviewModel()))
    ) as client:
        response = client.post("/v1/chat", json={"message": "复盘"})
        assert response.status_code == 200
        done = json.loads(response.text.splitlines()[-1])
        assert done["type"] == "done"
        assert done["response"]["kind"] == "review"
        assert done["response"]["review"]["facts"]["snapshot_count"] == 0
        assert done["response"]["cards"] == []
        turns = client.get(f"/v1/conversations/{done['conversation_id']}/turns").json()
        assert turns[0]["response"]["review"] == done["response"]["review"]


@pytest.mark.parametrize(
    "field, text",
    [
        ("adjustment_reason", "下次直接增加到 100 kg，比上次增加 20%"),
        ("rationale", "这次用二十公斤"),
        ("preparation", "先做 20 组"),
        ("preparation", "每组连续完成三十次"),
    ],
)
def test_narrative_cannot_bypass_structured_dose_and_load(field, text):
    from fitness_bot.training_decision import validate_session_plan
    from fitness_bot.training_schemas import SessionDraft

    data = draft()
    data[field] = text
    with pytest.raises(ValueError, match="narrative"):
        validate_session_plan(SessionDraft.model_validate(data), request(), Catalog.load())


def test_effort_instructions_come_from_structured_fields():
    from fitness_bot.training_decision import render_session, validate_session_plan
    from fitness_bot.training_schemas import SessionDraft

    data = draft()
    data["items"][0]["effort"] = "增加到 100 kg，做到力竭。"
    with pytest.raises(ValueError, match="narrative"):
        validate_session_plan(SessionDraft.model_validate(data), request(), Catalog.load())
    data["items"][0]["effort"] = "保持动作控制"
    result = validate_session_plan(SessionDraft.model_validate(data), request(), Catalog.load())
    assert "保留约 2–3 次余力" in render_session(result)


def test_confirmed_cycle_resolves_against_requested_training_date():
    from fitness_bot.training_decision import validate_session_plan
    from fitness_bot.training_schemas import SessionDraft

    r = request()
    tomorrow = datetime.now(UTC).date() + timedelta(days=1)
    r.context.training_profile["profile"]["confirmed_cycle"] = {
        "phase": "deload",
        "starts_on": tomorrow.isoformat(),
        "ends_on": tomorrow.isoformat(),
        "reason": "计划中的调整",
    }
    data = draft()
    data["adjustment"] = "reduce"
    result = validate_session_plan(
        SessionDraft.model_validate(data), r, Catalog.load(), target_date=tomorrow
    )
    assert result["confirmed_cycle"]["phase"] == "deload"


def progression_request():
    r = request()
    r.context.training_review["observations"] = [
        {
            "id": oid,
            "exercise_id": "0289",
            "comparison_eligible": True,
            "load_value": 20,
            "load_unit": "kg",
            "load_type": "external",
            "load_convention": "per_hand",
            "completed_sets": 3,
            "repetitions": 24,
        }
        for oid in ["latest", "prior"]
    ]
    r.context.training_review["progression_signals"] = [
        {
            "exercise_id": "0289",
            "latest_observation_id": "latest",
            "prior_observation_id": "prior",
            "sets": 3,
            "repetitions_per_set": 8,
        }
    ]
    return r


def test_progression_uses_both_evidence_records_and_changes_one_variable():
    from fitness_bot.training_decision import validate_session_plan
    from fitness_bot.training_schemas import SessionDraft

    data = draft()
    data.update(
        adjustment="increase_reps", progression_exercise_id="0289", evidence_ids=["latest", "prior"]
    )
    data["items"][0].update(repetitions=9, load_strategy="history", load_observation_id="latest")
    result = validate_session_plan(
        SessionDraft.model_validate(data), progression_request(), Catalog.load()
    )
    assert result["items"][0]["load"]["value"] == 20
    assert result["progression_basis"]["prior_observation_id"] == "prior"
    data["items"][0]["sets"] = 4
    with pytest.raises(ValueError, match="one variable"):
        validate_session_plan(
            SessionDraft.model_validate(data), progression_request(), Catalog.load()
        )


def test_unrelated_evidence_cannot_unlock_an_increase():
    from fitness_bot.training_decision import validate_session_plan
    from fitness_bot.training_schemas import SessionDraft

    data = draft()
    data.update(adjustment="increase_reps", progression_exercise_id="0180")
    with pytest.raises(ValueError, match="specific exercise"):
        validate_session_plan(
            SessionDraft.model_validate(data), progression_request(), Catalog.load()
        )


def test_title_can_repeat_exact_user_time_cap_but_cannot_invent_one():
    from fitness_bot.training_decision import validate_session_plan
    from fitness_bot.training_schemas import SessionDraft

    data = draft()
    data["theme"] = "三十分钟上肢训练"
    result = validate_session_plan(SessionDraft.model_validate(data), request(30), Catalog.load())
    assert result["estimated_minutes_max"] <= 30
    with pytest.raises(ValueError, match="Title time"):
        validate_session_plan(SessionDraft.model_validate(data), request(50), Catalog.load())


def test_invalid_optional_alternative_is_excluded_without_discarding_valid_work():
    from fitness_bot.training_decision import validate_session_plan
    from fitness_bot.training_schemas import SessionDraft

    data = draft()
    data["items"][0]["alternative_exercise_ids"] = ["fake", "1760"]
    result = validate_session_plan(SessionDraft.model_validate(data), request(), Catalog.load())
    assert len(result["items"]) == 4
    assert result["items"][0]["alternatives"] == []
    assert len(result["items"][0]["discarded_alternatives"]) == 2


@pytest.mark.parametrize(
    "claim", ["完成了3个有效组", "近期开始恢复训练", "comparison_eligible=true"]
)
def test_review_does_not_promote_missing_or_unverified_facts(claim):
    from fitness_bot.training_decision import validate_review
    from fitness_bot.training_schemas import ReviewDraft

    value = ReviewDraft(findings=[], keep=[claim], next_adjustments=["继续同步记录"])
    with pytest.raises(ValueError):
        validate_review(value, {"synchronization_complete": None})
