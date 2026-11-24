from copy import deepcopy

import pytest
from test_training_decision import draft, request

from fitness_bot.catalog import Catalog
from fitness_bot.plan_revisions import PlanRevisionInput, revised_plan
from fitness_bot.prescriptions import SetPrescription
from fitness_bot.training_decision import validate_session_plan
from fitness_bot.training_schemas import SessionDraft


def observation(**changes):
    return {
        "id": "same-exercise-observation",
        "exercise_id": "0289",
        "detail_type": "weightedReps",
        "performed_at": "2026-09-07T01:00:00Z",
        "completed_sets": 3,
        "load_type": "external",
        "load_value": 20,
        "load_unit": "kg",
        "load_convention": "unknown",
        "comparison_eligible": False,
        "progression_blocked": False,
        **changes,
    }


def candidate(observations):
    context = request(minutes=60)
    context.context.training_review["observations"] = observations
    return validate_session_plan(SessionDraft.model_validate(draft()), context, Catalog.load())


def test_same_exercise_history_fills_working_sets_without_claiming_comparability():
    plan = candidate([observation()])
    load = plan["items"][0]["set_prescriptions"][0]["load"]
    assert load["strategy"] == "history_reference"
    assert load["value"] == 20 and load["unit"] == "kg"
    assert load["convention"] == "unknown"
    assert load["observation_id"] == "same-exercise-observation"
    assert "未确认" in load["explanation"]
    assert plan["load_evidence"][0]["comparison_eligible"] is False
    assert plan["suggested_adjustment"] == "maintain"
    assert SetPrescription.model_validate(plan["items"][0]["set_prescriptions"][0])


def test_references_use_latest_workout_and_do_not_mix_units_or_transfer_exercises():
    plan = candidate(
        [
            observation(id="old-heavy", load_value=50, performed_at="2026-08-01T00:00:00Z"),
            observation(id="latest-heavy", load_value=25),
            observation(id="latest-light", load_value=20),
            observation(id="other", exercise_id="nonexistent", load_value=99),
        ]
    )
    assert plan["items"][0]["load"]["value"] == 20
    assert plan["items"][1]["load"]["value"] is None
    mixed = candidate([observation(), observation(id="lb", load_unit="lb", load_value=40)])
    assert mixed["items"][0]["load"]["value"] is None


@pytest.mark.parametrize(
    "changes",
    [
        {"load_value": 0},
        {"load_value": float("nan")},
        {"load_unit": "level"},
        {"load_type": "bodyweight"},
        {"completed_sets": 0},
        {"detail_type": "duration"},
        {"progression_blocked": True},
    ],
)
def test_inapplicable_history_remains_trial(changes):
    assert candidate([observation(**changes)])["items"][0]["load"]["value"] is None


def test_assistance_reference_retains_its_meaning():
    plan = candidate([observation(load_type="assistance", load_value=35)])
    assert plan["items"][0]["load"]["convention"] == "assistance"


def test_warmup_remains_trial_and_reduce_does_not_copy_old_working_weight():
    value = draft()
    value["items"][0]["set_prescriptions"] = [
        {"kind": "warmup", "repetitions": 8},
        {"kind": "working", "repetitions": 8},
    ]
    context = request(minutes=60)
    context.context.training_review["observations"] = [observation()]
    plan = validate_session_plan(SessionDraft.model_validate(value), context, Catalog.load())
    assert plan["items"][0]["set_prescriptions"][0]["load"]["value"] is None
    assert plan["items"][0]["set_prescriptions"][1]["load"]["value"] == 20
    value["adjustment"] = "reduce"
    reduced = validate_session_plan(SessionDraft.model_validate(value), context, Catalog.load())
    assert reduced["items"][0]["load"]["value"] is None


def test_revising_reps_preserves_reference_but_cannot_forge_unknown_numeric_reference():
    plan = candidate([observation()])
    items = [
        {"exercise_id": i["exercise"]["id"], "set_prescriptions": deepcopy(i["set_prescriptions"])}
        for i in plan["items"]
    ]
    items[0]["set_prescriptions"][0]["repetitions"] = 7
    payload = {
        "revision_id": "dc96b735-d688-409f-9ea1-c72d42c75c25",
        "expected_revision": 0,
        "items": items,
    }
    updated = revised_plan(plan, PlanRevisionInput.model_validate(payload), Catalog.load())
    assert updated["items"][0]["set_prescriptions"][0]["load"]["strategy"] == "history_reference"
    items[0]["set_prescriptions"][0]["load"]["value"] = 99
    with pytest.raises(ValueError, match="reference|convention"):
        revised_plan(plan, PlanRevisionInput.model_validate(payload), Catalog.load())


def test_added_reference_preserves_added_weight_semantics():
    plan = candidate([observation(load_type="added", load_value=20)])
    assert plan["items"][0]["load"]["value"] == 20
    assert plan["items"][0]["load"]["convention"] == "added"


@pytest.mark.parametrize("adjustment", ["maintain", "reduce"])
def test_model_history_claim_on_unverified_same_exercise_is_not_fatal(adjustment):
    value = draft()
    value["adjustment"] = adjustment
    value["items"][0].update(
        load_strategy="history", load_observation_id="same-exercise-observation"
    )
    context = request(minutes=60)
    context.context.training_review["observations"] = [observation()]
    plan = validate_session_plan(SessionDraft.model_validate(value), context, Catalog.load())
    load = plan["items"][0]["load"]
    assert load["strategy"] == ("history_reference" if adjustment == "maintain" else "trial")
    assert load["value"] == (20 if adjustment == "maintain" else None)
    assert plan["items"][0]["set_prescriptions"]


def test_model_history_claim_cannot_cross_exercises_or_forge_observations():
    value = draft()
    value["items"][0].update(
        load_strategy="history", load_observation_id="same-exercise-observation"
    )
    context = request(minutes=60)
    context.context.training_review["observations"] = [observation(exercise_id="0180")]
    with pytest.raises(ValueError, match="same-exercise"):
        validate_session_plan(SessionDraft.model_validate(value), context, Catalog.load())
