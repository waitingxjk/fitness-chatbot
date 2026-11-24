"""Same-exercise recorded loads as unverified proposal references, not progression evidence."""

import math
from datetime import datetime


def historical_reference(exercise, observations):
    if exercise.detail_type not in {"weightedReps", "machineLevelReps"}:
        return None
    matching = []
    for item in observations:
        if item.get("exercise_id") != exercise.id or not item.get("completed_sets"):
            continue
        try:
            performed = datetime.fromisoformat(item["performed_at"].replace("Z", "+00:00"))
            if performed.tzinfo is None:
                continue
        except (KeyError, ValueError, AttributeError):
            continue
        matching.append((performed, item))
    if not matching:
        return None
    latest = max(time for time, _ in matching)
    # Do not reach past an incompatible latest record for an older convenient weight.
    recent = [item for time, item in matching if time == latest]
    units = {item.get("load_unit") for item in recent}
    types = {item.get("load_type") for item in recent}
    conventions = {item.get("load_convention") or "unknown" for item in recent}
    if len(units) != 1 or len(types) != 1 or len(conventions) != 1:
        return None
    unit, load_type = next(iter(units)), next(iter(types))
    machine_level = exercise.detail_type == "machineLevelReps"
    if unit not in ({"level"} if machine_level else {"kg", "lb"}):
        return None
    if load_type not in (
        {"machineLevel"} if machine_level else {"external", "added", "assistance"}
    ):
        return None
    for item in recent:
        value = item.get("load_value")
        if (
            item.get("detail_type") != exercise.detail_type
            or item.get("progression_blocked")
            or isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not 0 < value <= 10000
            or (machine_level and not float(value).is_integer())
        ):
            return None
    # Less external weight or more assistance is the conservative recorded reference.
    chosen = (max if load_type == "assistance" else min)(
        recent, key=lambda item: item["load_value"]
    )
    convention = (
        "machine_level"
        if machine_level
        else (load_type if load_type in {"added", "assistance"} else next(iter(conventions)))
    )
    return {
        "strategy": "history_reference",
        "value": chosen["load_value"],
        "unit": unit,
        "convention": convention,
        "observation_id": chosen["id"],
        "explanation": f"参考 {latest.date().isoformat()} 同一动作已完成记录；"
        + (
            "负重口径未确认，沿用原记录单位，"
            if convention == "unknown"
            else "不代表已确认本次器械与状态相同，"
        )
        + "这是候选参考重量，可直接修改；不作为自动加重依据。",
    }
