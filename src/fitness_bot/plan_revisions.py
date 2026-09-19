"""User-authored immutable plan revisions and idempotent persistence."""

import math
from copy import deepcopy
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from fitness_bot.planning import plan_outcome
from fitness_bot.prescriptions import SetPrescription, summarize_sets, validate_user_sets
from fitness_bot.storage import ObjectNotFound, TurnConflict, utcnow
from fitness_bot.training_storage import PlanEventRow, SessionPlanRow, plan_snapshot


class RevisedItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    exercise_id: str = Field(min_length=1, max_length=64)
    set_prescriptions: list[SetPrescription] = Field(min_length=1, max_length=12)


class PlanRevisionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision_id: UUID
    expected_revision: int = Field(ge=0)
    items: list[RevisedItem] = Field(max_length=8)


def revised_plan(original, payload, catalog):
    is_current = original.get("planning_target") == "current_workout"
    if not payload.items and not is_current:
        raise ValueError("Next workout revisions require work items")
    old = {item["exercise"]["id"]: item for item in original["items"]}
    items = []
    seen = set()
    for change in payload.items:
        exercise = catalog.get(change.exercise_id)
        if not exercise or not exercise.is_active or exercise.id in seen:
            raise ValueError("Plan requires unique active catalog exercises")
        if exercise.id not in old:
            raise ValueError("Replacing an exercise requires a newly generated plan")
        seen.add(exercise.id)
        previous = old[exercise.id]
        rows = validate_user_sets([r.model_dump() for r in change.set_prescriptions], exercise)
        summary = summarize_sets(
            rows,
            seconds_per_rep=previous.get("seconds_per_rep", 4),
            switch_seconds=previous.get("switch_seconds", 90),
        )
        work = [row for row in rows if row["kind"] == "working"]
        items.append(
            {
                **deepcopy(previous),
                **summary,
                "set_prescriptions": rows,
                "load": deepcopy(work[0]["load"]),
                "effort": "按每组设置执行，记录时填写实际表现。",
            }
        )
    total = (
        (
            original["preparation_minutes"]
            + original["finish_minutes"]
            + sum(item["estimated_seconds"] for item in items) / 60
        )
        if items
        else 0
    )
    upper = math.ceil(total + original["buffer_minutes"]) if items else 0
    budget = (
        original.get("remaining_minutes")
        if is_current
        else original.get("profile_snapshot", {}).get("session_minutes")
    )
    if budget is not None and upper > budget:
        raise ValueError(
            "Adjusted plan exceeds available time; reduce sets or replan with more time"
        )
    result = deepcopy(original)
    result.update(
        id=str(payload.revision_id),
        version=original["version"] + 1,
        parent_plan_id=original["id"],
        status="proposed",
        items=items,
        outcome=plan_outcome(
            "rest" if not items and original.get("outcome") == "rest" else "maintain",
            items,
        ),
        working_sets=sum(item["sets"] for item in items),
        estimated_minutes_min=math.ceil(total),
        estimated_minutes_max=upper,
        user_edited=True,
        edit_request=payload.model_dump(mode="json"),
        rationale="用户已调整动作顺序或逐组设置，请确认当前卡片中的完整计划。",
        suggested_adjustment="user_modified",
        progression_basis=None,
        adjustment_reason="这是用户调整后的目标，未被认定为基于历史证据的自动递增。",
        load_evidence=[],
        ordering_checks=[],
        removed_items=[],
        limitations=[
            "用户调整后的设置尚未进行新的整体训练策略评估。",
            "卡片保存目标值，开始训练后仍需按实际表现记录。",
        ],
    )
    if not items:
        result.update(preparation_minutes=0, finish_minutes=0, buffer_minutes=0)
    return result


async def save_revision(store, user_id, plan_id, payload, catalog):
    parent_id, child_id = str(plan_id), str(payload.revision_id)
    content = payload.model_dump(mode="json")
    try:
        async with store.sessions.begin() as session:
            parent = await session.get(SessionPlanRow, parent_id)
            if not parent or parent.user_id != user_id:
                raise ObjectNotFound("Plan not found")
            existing = await session.get(SessionPlanRow, child_id)
            if existing:
                if (
                    existing.user_id != user_id
                    or existing.plan.get("parent_plan_id") != parent_id
                    or existing.plan.get("edit_request") != content
                ):
                    raise TurnConflict("Revision ID already used")
                event = await session.get(PlanEventRow, child_id)
                if not event or "revision_result" not in event.receipt:
                    raise TurnConflict("Revision ID already used")
                return event.receipt["revision_result"]
            if parent.revision != payload.expected_revision or parent.status not in {
                "proposed",
                "accepted",
            }:
                raise TurnConflict("Plan changed or cannot be revised; reload")
            if (await plan_snapshot(session, parent))["superseded_by"]:
                raise TurnConflict("A newer plan exists; revise that version instead")
            child_plan = revised_plan(parent.plan, payload, catalog)
            changed = await session.execute(
                update(SessionPlanRow)
                .where(
                    SessionPlanRow.id == parent_id,
                    SessionPlanRow.user_id == user_id,
                    SessionPlanRow.revision == payload.expected_revision,
                )
                .values(revision=payload.expected_revision + 1)
            )
            if changed.rowcount != 1:
                raise TurnConflict("Plan changed; reload")
            created = utcnow().isoformat() + "Z"
            child = SessionPlanRow(
                id=child_id,
                user_id=user_id,
                request_id=child_id,
                plan=child_plan,
                status="proposed",
                revision=0,
                created_at=created,
            )
            session.add(child)
            await session.flush()
            result = await plan_snapshot(session, child)
            event = {
                "event_id": child_id,
                "kind": "revised",
                "child_plan_id": child_id,
                "expected_revision": payload.expected_revision,
                "created_at": created,
            }
            session.add(
                PlanEventRow(
                    event_id=child_id,
                    plan_id=parent_id,
                    payload=content,
                    receipt={"event": event, "revision_result": result},
                    revision=payload.expected_revision + 1,
                )
            )
        return result
    except IntegrityError as exc:
        raise TurnConflict("Concurrent plan revision; retry the same revision ID") from exc
