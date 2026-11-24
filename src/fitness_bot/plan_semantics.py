"""Occurrence-level plan semantics, independent of catalog and set classifications."""

from collections import Counter
from typing import Literal

PlanPhase = Literal["warmup", "main", "cooldown"]
PurposeRole = Literal[
    "general_warmup",
    "mobility",
    "technique",
    "primary",
    "accessory",
    "conditioning",
    "cooldown",
    "stretching",
]
PHASE_LABELS = {"warmup": "热身准备", "main": "主体训练", "cooldown": "整理放松"}
ROLE_LABELS = {
    "general_warmup": "一般热身",
    "mobility": "活动准备",
    "technique": "技术练习",
    "primary": "重点训练",
    "accessory": "辅助训练",
    "conditioning": "体能训练",
    "cooldown": "缓和活动",
    "stretching": "拉伸放松",
}
ROLE_PHASES = {
    "general_warmup": {"warmup"},
    "mobility": {"warmup"},
    "technique": {"warmup", "main"},
    "primary": {"main"},
    "accessory": {"main"},
    "conditioning": {"main"},
    "cooldown": {"cooldown"},
    "stretching": {"main", "cooldown"},
}
ACTIVITY_ROLES = set(ROLE_LABELS) - {"primary", "accessory"}


def validate_role(phase, purpose_role):
    if phase not in ROLE_PHASES.get(purpose_role, set()):
        raise ValueError("Invalid phase and purpose_role combination")


def validate_structure(items, *, current=False, allow_empty=False):
    if not items:
        if allow_empty:
            return
        raise ValueError("Complete plan requires phases")
    order = list(PHASE_LABELS)
    phases = [item["phase"] for item in items]
    if phases != sorted(phases, key=order.index):
        raise ValueError("Plan phase order must be warmup, main, cooldown")
    if not current and set(phases) != set(order):
        raise ValueError("Complete plan requires warmup, main and cooldown phases")
    ids = [item["item_id"] for item in items]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate plan item_id")
    occurrences = [(item["phase"], item["exercise"]["id"]) for item in items]
    if len(set(occurrences)) != len(occurrences):
        raise ValueError("Plan requires unique exercises within each phase")
    for item in items:
        validate_role(item["phase"], item["purpose_role"])
        activity = item["purpose_role"] in ACTIVITY_ROLES
        kinds = {r["kind"] for r in item["set_prescriptions"]}
        if activity and kinds != {"activity"}:
            raise ValueError("Activity purpose roles require activity rows")
        if not activity and ("working" not in kinds or "activity" in kinds):
            raise ValueError("Primary/accessory training requires working sets")


def phase_summaries(items):
    result = []
    for phase, label in PHASE_LABELS.items():
        group = [i for i in items if i["phase"] == phase]
        if not group:
            continue
        kinds = Counter(r["kind"] for i in group for r in i["set_prescriptions"])
        result.append(
            {
                "phase": phase,
                "label": label,
                "item_count": len(group),
                "estimated_seconds": sum(i["estimated_seconds"] for i in group),
                "working_sets": kinds["working"],
                "warmup_sets": kinds["warmup"],
                "activity_count": kinds["activity"],
            }
        )
    return result
