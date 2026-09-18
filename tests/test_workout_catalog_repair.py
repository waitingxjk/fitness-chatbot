import copy
import json
from pathlib import Path

import pytest

from fitness_bot.catalog import Catalog
from fitness_bot.workout_catalog_repair import repaired_workout


def example():
    workout = json.loads((Path(__file__).parent / "fixtures/workout_upload.json").read_text())[
        "workout"
    ]
    record = workout["records"][0]
    record.update(exerciseId="imported-bridge", exerciseNameSnapshot="杠铃臀桥")
    return workout, {
        "record_id": record["id"],
        "from_id": "imported-bridge",
        "to_id": "1409",
        "source_name": "杠铃臀桥",
        "basis": "原图名称与标准动作一致",
        "match_kind": "exact",
        "preserve_duration": False,
    }


def test_repair_changes_only_identity_provenance_and_versions():
    workout, change = example()
    original = copy.deepcopy(workout)
    result = repaired_workout(
        workout, [change], Catalog.load(), "repair-test", "2026-09-18T00:00:00Z"
    )
    assert workout == original
    before, after = original["records"][0], result["records"][0]
    assert after["exerciseId"] == "1409"
    assert after["exerciseNameSnapshot"] == before["exerciseNameSnapshot"]
    assert after["sets"] == before["sets"]
    assert after["version"] == before["version"] + 1
    assert after["detailPayload"]["catalogRepair"]["originalExerciseId"] == "imported-bridge"
    for key in set(before) - {"exerciseId", "version", "detailPayload"}:
        assert after[key] == before[key]
    for key in set(original) - {"records", "updatedAt"}:
        assert result[key] == original[key]


@pytest.mark.parametrize(
    "field,value",
    [
        ("from_id", "wrong"),
        ("source_name", "wrong"),
        ("to_id", "missing"),
        ("record_id", "missing"),
        ("to_id", "3224"),
    ],
)
def test_repair_refuses_wrong_source_missing_target_or_record_type(field, value):
    workout, change = example()
    change[field] = value
    with pytest.raises(ValueError):
        repaired_workout(workout, [change], Catalog.load(), "repair-test", "2026-09-18T00:00:00Z")


def test_repair_duration_mapping_preserves_seconds_not_repetitions():
    workout, change = example()
    record = workout["records"][0]
    record["detailType"] = "duration"
    record["sets"][0].update(
        durationSeconds=20, repetitions=0, loadType="none", loadValue=None, loadUnit=None
    )
    change.update(to_id="3224", preserve_duration=True)
    result = repaired_workout(
        workout, [change], Catalog.load(), "repair-test", "2026-09-18T00:00:00Z"
    )
    assert result["records"][0]["detailType"] == "duration"
    assert result["records"][0]["sets"] == record["sets"]
