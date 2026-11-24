from copy import deepcopy

from test_training_decision import draft, request

from fitness_bot.catalog import Catalog
from fitness_bot.training_decision import validate_session_plan
from fitness_bot.training_schemas import SessionDraft


def validate(value, minutes):
    return validate_session_plan(
        SessionDraft.model_validate(value), request(minutes), Catalog.load()
    )


def test_time_budget_reduces_extra_sets_before_dropping_movement_coverage():
    value = draft()
    for item in value["items"][1:]:
        item["sets"] = 3
    full = validate(value, 60)
    fitted = validate(value, full["estimated_minutes_max"] - 3)
    assert len(fitted["items"]) == len(full["items"])
    assert fitted["items"][0] == full["items"][0]
    assert fitted["working_sets"] < full["working_sets"]
    assert fitted["estimated_minutes_max"] <= full["estimated_minutes_max"] - 3
    for item, before in zip(fitted["items"], full["items"], strict=True):
        assert item["sets"] >= 2
        assert item["rest_seconds"] == before["rest_seconds"]
        assert all(row in before["set_prescriptions"] for row in item["set_prescriptions"])
    assert any(a["field"] == "time_allocation" for a in fitted["planning_assumptions"])


def test_when_removal_is_unavoidable_preserve_unique_body_part_before_duplicate():
    value = draft()
    for item in value["items"]:
        item["sets"] = 2
    full = validate(value, 60)
    fitted = validate(value, full["estimated_minutes_max"] - 1)
    ids = {item["exercise"]["id"] for item in fitted["items"]}
    assert "0289" in ids  # User priority stays intact.
    assert "0334" in ids  # Only shoulder exercise, despite its supplement role.
    assert len(ids & {"0180", "0150"}) == 1  # Both are back exercises.
    assert len(fitted["removed_items"]) == 1


def test_budget_fitting_preserves_warmup_and_per_set_prescriptions():
    value = draft()
    item = value["items"][1]
    item["set_prescriptions"] = [
        {"kind": "warmup", "repetitions": 6, "rest_seconds": 60},
        {"kind": "working", "repetitions": 10, "rest_seconds": 120},
        {"kind": "working", "repetitions": 8, "rest_seconds": 90},
        {"kind": "working", "repetitions": 6, "rest_seconds": 90},
    ]
    full = validate(deepcopy(value), 60)
    fitted = validate(value, full["estimated_minutes_max"] - 1)
    before = full["items"][1]["set_prescriptions"]
    after = fitted["items"][1]["set_prescriptions"]
    assert after == before[:-1]
    assert fitted["items"][1]["sets"] == 2
    assert fitted["items"][1]["total_sets"] == 3
