"""Explicit offline catalog identity repair; never invoked by chatbot requests."""

import hashlib
import json
from copy import deepcopy

from fitness_bot.workout_schemas import WorkoutSnapshot


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def repaired_workout(workout, changes, catalog, repair_id, timestamp):
    result = deepcopy(workout)
    by_id = {record["id"]: record for record in result["records"]}
    seen = set()
    for change in changes:
        record = by_id.get(change["record_id"])
        if record is None or record["id"] in seen:
            raise ValueError("Missing or duplicate record")
        seen.add(record["id"])
        if (
            record["exerciseId"] != change["from_id"]
            or record["exerciseNameSnapshot"] != change["source_name"]
        ):
            raise ValueError("Source identity changed")
        target = catalog.get(change["to_id"])
        if target is None or not target.is_active:
            raise ValueError("Target must be an active catalog exercise")
        if target.id == record["exerciseId"]:
            raise ValueError("Repair must change identity")
        timed_bodyweight = (
            change.get("preserve_duration") is True
            and record["detailType"] == "duration"
            and target.detail_type == "bodyweightReps"
            and target.equipment_code == "body weight"
        )
        if record["detailType"] != target.detail_type and not timed_bodyweight:
            raise ValueError("Record type differs from catalog; refuses dose conversion")
        if change["match_kind"] not in {"exact", "same_family"} or not change["basis"]:
            raise ValueError("Mapping requires a reviewed basis")
        payload = deepcopy(record.get("detailPayload") or {})
        if "catalogRepair" in payload:
            raise ValueError("Record already has repair provenance; review before replacing it")
        payload["catalogRepair"] = {
            "repairId": repair_id,
            "repairedAt": timestamp,
            "originalExerciseId": record["exerciseId"],
            "originalExerciseName": record["exerciseNameSnapshot"],
            "catalogExerciseId": target.id,
            "catalogVersion": catalog.version,
            "matchKind": change["match_kind"],
            "basis": change["basis"],
            "recordDetailTypePreserved": record["detailType"],
            "loadComparabilityConfirmed": False,
        }
        record.update(exerciseId=target.id, detailPayload=payload, version=record["version"] + 1)
    if changes:
        result["updatedAt"] = timestamp
    # Validate links and accepted field types without normalizing preserved source values.
    WorkoutSnapshot.model_validate(result)
    return result
