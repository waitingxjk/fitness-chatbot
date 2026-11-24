from copy import deepcopy
from uuid import uuid4

import pytest
from test_training_decision import draft, request

from fitness_bot.catalog import Catalog
from fitness_bot.plan_revisions import PlanRevisionInput, revised_plan
from fitness_bot.training_decision import validate_session_plan
from fitness_bot.training_schemas import SemanticSessionDraft


def semantic_draft():
    value = draft()
    value["semantics_version"] = 1
    for item in value["items"]:
        item.update(
            phase="main", purpose_role="primary" if item["role"] == "priority" else "accessory"
        )
        item["set_prescriptions"] = [
            dict(kind="working", repetitions=item["repetitions"], rest_seconds=item["rest_seconds"])
            for _ in range(item["sets"])
        ]
    activity = {
        **deepcopy(value["items"][0]),
        "exercise_id": "2141",
        "effort": "轻松完成，保持动作可控。",
        "role": "supplement",
        "sets": 1,
        "phase": "warmup",
        "purpose_role": "general_warmup",
        "repetitions": None,
        "duration_seconds": 180,
        "rest_seconds": 0,
        "switch_seconds": 15,
        "set_prescriptions": [{"kind": "activity", "duration_seconds": 180, "rest_seconds": 0}],
    }
    value["items"] = [
        activity,
        *value["items"],
        {
            **deepcopy(activity),
            "phase": "cooldown",
            "purpose_role": "cooldown",
        },
    ]
    return value


def semantic_request(minutes=60):
    context = request(minutes)
    context.context.training_profile["profile"]["equipment"] += [
        "elliptical machine",
        "body weight",
    ]
    return context


def planned(value=None, minutes=60):
    return validate_session_plan(
        SemanticSessionDraft.model_validate(value or semantic_draft()),
        semantic_request(minutes),
        Catalog.load(),
    )


def test_complete_semantic_plan_preserves_same_exercise_in_separate_phases():
    result = planned()
    assert result["semantics_version"] == 1
    assert [s["phase"] for s in result["phase_summaries"]] == ["warmup", "main", "cooldown"]
    assert result["items"][0]["exercise"]["id"] == result["items"][-1]["exercise"]["id"]
    assert len({i["item_id"] for i in result["items"]}) == 6
    assert result["working_sets"] == 10
    assert result["items"][0]["sets"] == 0
    assert "余力" not in result["items"][0]["effort"]
    assert result["items"][0]["set_prescriptions"][0]["kind"] == "activity"
    assert sum(s["estimated_seconds"] for s in result["phase_summaries"]) == sum(
        i["estimated_seconds"] for i in result["items"]
    )
    import math

    assert result["estimated_minutes_max"] == math.ceil(
        sum(i["estimated_seconds"] for i in result["items"]) / 60 + result["buffer_minutes"]
    )


@pytest.mark.parametrize(
    ("phase", "purpose_role"),
    [
        ("warmup", "general_warmup"),
        ("warmup", "mobility"),
        ("warmup", "technique"),
        ("main", "technique"),
        ("main", "conditioning"),
        ("main", "stretching"),
        ("cooldown", "cooldown"),
        ("cooldown", "stretching"),
    ],
)
def test_activity_roles_are_supported(phase, purpose_role):
    value = semantic_draft()
    activity = deepcopy(value["items"][0])
    activity.update(exercise_id="0685", phase=phase, purpose_role=purpose_role)
    insert_at = (
        0
        if phase == "warmup"
        else len(value["items"])
        if phase == "cooldown"
        else len(value["items"]) - 1
    )
    value["items"].insert(insert_at, activity)
    assert planned(value)["items"]


@pytest.mark.parametrize(
    ("phase", "role"),
    [
        ("main", "general_warmup"),
        ("warmup", "primary"),
        ("cooldown", "accessory"),
        ("main", "invented"),
    ],
)
def test_invalid_phase_role_pairs_are_rejected(phase, role):
    value = semantic_draft()
    value["items"][0].update(phase=phase, purpose_role=role)
    with pytest.raises(ValueError):
        planned(value)


def test_strict_generation_schema_requires_role_and_all_phases():
    value = semantic_draft()
    del value["items"][0]["purpose_role"]
    with pytest.raises(ValueError):
        SemanticSessionDraft.model_validate(value)
    value = semantic_draft()
    value["items"].pop()
    with pytest.raises(ValueError, match="phase|cooldown"):
        planned(value)


def test_phase_order_and_duplicate_within_phase_are_rejected():
    value = semantic_draft()
    value["items"][0], value["items"][1] = value["items"][1], value["items"][0]
    with pytest.raises(ValueError, match="phase|order"):
        planned(value)
    value = semantic_draft()
    value["items"].insert(0, deepcopy(value["items"][0]))
    with pytest.raises(ValueError, match="unique|Duplicate"):
        planned(value)


def revision_payload(plan):
    return {
        "revision_id": str(uuid4()),
        "expected_revision": 0,
        "items": [
            {
                "exercise_id": i["exercise"]["id"],
                "item_id": i["item_id"],
                "set_prescriptions": i["set_prescriptions"],
            }
            for i in plan["items"]
        ],
    }


def test_revision_uses_occurrence_identity_and_recomputes_phase_time():
    original = planned()
    payload = revision_payload(original)
    payload["items"][0]["set_prescriptions"][0]["duration_seconds"] = 120
    revised = revised_plan(original, PlanRevisionInput.model_validate(payload), Catalog.load())
    assert revised["items"][0]["set_prescriptions"][0]["duration_seconds"] == 120
    assert revised["items"][-1]["set_prescriptions"][0]["duration_seconds"] == 180
    assert (
        revised["phase_summaries"][0]["estimated_seconds"]
        == original["phase_summaries"][0]["estimated_seconds"] - 60
    )
    assert revised["working_sets"] == original["working_sets"]


def test_revision_cannot_ambiguously_select_duplicate_or_remove_entire_phase():
    original = planned()
    payload = revision_payload(original)
    del payload["items"][0]["item_id"]
    with pytest.raises(ValueError, match="ambiguous|item_id"):
        revised_plan(original, PlanRevisionInput.model_validate(payload), Catalog.load())
    payload = revision_payload(original)
    payload["items"].pop()
    with pytest.raises(ValueError, match="phase|cooldown"):
        revised_plan(original, PlanRevisionInput.model_validate(payload), Catalog.load())


def test_time_fit_preserves_preparation_and_cooldown():
    full = planned()
    fitted = planned(minutes=full["estimated_minutes_max"] - 4)
    assert fitted["items"][0]["set_prescriptions"] == full["items"][0]["set_prescriptions"]
    assert fitted["items"][-1]["set_prescriptions"] == full["items"][-1]["set_prescriptions"]
    assert fitted["estimated_minutes_max"] <= full["estimated_minutes_max"] - 4
    assert fitted["working_sets"] < full["working_sets"]


def test_reduce_legacy_parent_promotes_preparation_without_adding_strength_work():
    from fitness_bot.training_schemas import SessionDraft

    original = validate_session_plan(
        SessionDraft.model_validate(draft()), semantic_request(), Catalog.load()
    )
    value = semantic_draft()
    value["adjustment"] = "reduce"
    for item in value["items"][1:-1]:
        item["set_prescriptions"] = item["set_prescriptions"][:1]
    # Old plan had three finish minutes; fit the concrete cooldown inside it.
    value["items"][-1]["set_prescriptions"][0]["duration_seconds"] = 120
    reduced = validate_session_plan(
        SemanticSessionDraft.model_validate(value),
        semantic_request(),
        Catalog.load(),
        original_plan=original,
    )
    assert reduced["working_sets"] == 4
    assert reduced["preparation_minutes"] <= original["preparation_minutes"]
    assert reduced["finish_minutes"] <= original["finish_minutes"]


def test_progression_targets_main_occurrence_not_same_exercise_technique_warmup():
    from test_training_decision import progression_request

    value = semantic_draft()
    value.update(
        adjustment="increase_reps", progression_exercise_id="0289", evidence_ids=["latest", "prior"]
    )
    main = value["items"][1]
    main.update(load_strategy="history", load_observation_id="latest")
    for row in main["set_prescriptions"]:
        row["repetitions"] = 9
    warmup = deepcopy(main)
    warmup.update(
        phase="warmup",
        purpose_role="technique",
        role="supplement",
        load_strategy="trial",
        load_observation_id=None,
    )
    warmup["set_prescriptions"] = [{"kind": "activity", "repetitions": 6, "rest_seconds": 0}]
    value["items"].insert(1, warmup)
    context = progression_request()
    context.context.training_profile["profile"]["equipment"].append("elliptical machine")
    result = validate_session_plan(
        SemanticSessionDraft.model_validate(value), context, Catalog.load()
    )
    assert result["progression_basis"]
    assert result["items"][1]["load"]["value"] is None
    assert result["items"][2]["load"]["value"] == 20


def test_http_plan_commits_all_phases_and_accepts_once_without_execution():
    import json

    from fastapi.testclient import TestClient
    from test_training_plans import event

    from fitness_bot.agent import CompanionAgent
    from fitness_bot.app import create_app
    from fitness_bot.config import Settings
    from fitness_bot.schemas import SearchPlan

    class Model:
        async def plan(self, *_):
            return SearchPlan(
                kind="session_plan",
                session_inputs={
                    "equipment": ["dumbbell", "cable", "elliptical machine"],
                    "session_minutes": 60,
                },
            )

        async def session_plan(self, *_):
            value = semantic_draft()
            value["evidence_ids"] = []
            return SemanticSessionDraft.model_validate(value)

    with TestClient(
        create_app(Settings(_env_file=None), CompanionAgent(Catalog.load(), Model()))
    ) as client:
        response = client.post("/v1/chat", json={"message": "按历史安排完整训练"})
        done = json.loads(response.text.splitlines()[-1])
        assert done["type"] == "done", done
        plan = done["response"]["session_plan"]
        saved = client.get(f"/v1/training/plans/{plan['id']}").json()
        assert saved["plan"] == plan
        assert saved["status"] == "proposed"
        payload = revision_payload(plan)
        payload["items"][0]["set_prescriptions"][0]["duration_seconds"] = 120
        child = client.post(f"/v1/training/plans/{plan['id']}/revision", json=payload)
        assert child.status_code == 200, child.text
        child_id = child.json()["plan"]["id"]
        action = event("accepted", 0)
        accepted = client.post(f"/v1/training/plans/{child_id}/events", json=action)
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["status"] == "accepted"
        assert len(accepted.json()["plan"]["items"]) == 6
        assert (
            client.post(f"/v1/training/plans/{child_id}/events", json=action).json()
            == accepted.json()
        )


def test_current_semantic_additions_can_omit_preparation_and_finish():
    from test_planning_targets import additions, current_request

    value = additions()
    value["semantics_version"] = 1
    for item in value["items"]:
        item.update(
            phase="main", purpose_role="primary" if item["role"] == "priority" else "accessory"
        )
        item["set_prescriptions"] = [
            dict(kind="working", repetitions=item["repetitions"], rest_seconds=item["rest_seconds"])
            for _ in range(item["sets"])
        ]
    result = validate_session_plan(
        SemanticSessionDraft.model_validate(value), current_request(), Catalog.load()
    )
    assert [s["phase"] for s in result["phase_summaries"]] == ["main"]
    assert result["preparation_minutes"] == result["finish_minutes"] == 0
