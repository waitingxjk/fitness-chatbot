"""Immutable preference versions and session plans; never writes workout snapshots."""

from sqlalchemy import JSON, Integer, String, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column

from fitness_bot.identity import USER_ID
from fitness_bot.storage import Base, ObjectNotFound, TurnConflict, utcnow
from fitness_bot.training_schemas import TrainingProfile


class TrainingProfileRow(Base):
    __tablename__ = "training_profile_versions"

    user_id: Mapped[str] = mapped_column(USER_ID, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile: Mapped[dict] = mapped_column(JSON)
    saved_at: Mapped[str] = mapped_column(String(40))


class ObservationAttestationRow(Base):
    __tablename__ = "training_observation_attestations"

    user_id: Mapped[str] = mapped_column(USER_ID, primary_key=True)
    observation_id: Mapped[str] = mapped_column(String(24), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON)
    saved_at: Mapped[str] = mapped_column(String(40))


class SessionPlanRow(Base):
    __tablename__ = "training_session_plans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(USER_ID, index=True)
    request_id: Mapped[str] = mapped_column(String(36), unique=True)
    plan: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="proposed")
    revision: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String(40))


class PlanEventRow(Base):
    __tablename__ = "training_plan_events"

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    plan_id: Mapped[str] = mapped_column(String(36), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    receipt: Mapped[dict] = mapped_column(JSON)
    revision: Mapped[int] = mapped_column(Integer)


async def plan_snapshot(session, row):
    successor_id = await session.scalar(
        select(SessionPlanRow.id)
        .where(
            SessionPlanRow.user_id == row.user_id,
            SessionPlanRow.plan["parent_plan_id"].as_string() == row.id,
        )
        .order_by(SessionPlanRow.created_at.desc(), SessionPlanRow.id)
        .limit(1)
    )
    events = await session.scalars(
        select(PlanEventRow)
        .where(PlanEventRow.plan_id == row.id)
        .order_by(PlanEventRow.revision.desc())
        .limit(10)
    )
    events = list(reversed(list(events)))
    execution = await session.scalar(
        select(PlanEventRow)
        .where(
            PlanEventRow.plan_id == row.id,
            PlanEventRow.payload["kind"].as_string() == "executed",
        )
        .order_by(PlanEventRow.revision.desc())
        .limit(1)
    )
    return {
        "plan": row.plan,
        "status": row.status,
        "revision": row.revision,
        "created_at": row.created_at,
        "superseded_by": successor_id,
        "events": [e.receipt["event"] for e in events],
        "events_truncated": row.revision > len(events),
        "execution": execution.receipt["event"] if execution else None,
    }


async def load_session_plans(session, user_id):
    pinned = []
    for status in ("accepted", "executed"):
        row = await session.scalar(
            select(SessionPlanRow)
            .where(
                SessionPlanRow.user_id == user_id,
                SessionPlanRow.status == status,
            )
            .order_by(SessionPlanRow.created_at.desc(), SessionPlanRow.id)
            .limit(1)
        )
        if row:
            pinned.append(row)
    rows = await session.scalars(
        select(SessionPlanRow)
        .where(SessionPlanRow.user_id == user_id)
        .order_by(SessionPlanRow.created_at.desc(), SessionPlanRow.id)
        .limit(5)
    )
    selected = {row.id: row for row in [*pinned, *rows]}
    result = []
    for row in list(selected.values())[:5]:
        snapshot = await plan_snapshot(session, row)
        result.append(snapshot)
    return result


async def persist_session_plan(session, user_id, request_id, plan):
    from fitness_bot.training_evidence import revalidate_plan_evidence

    await revalidate_plan_evidence(session, user_id, plan)
    if parent_id := plan.get("parent_plan_id"):
        parent = await session.get(SessionPlanRow, parent_id)
        if not parent or parent.user_id != user_id:
            raise ObjectNotFound("Parent plan not found")
        if plan["version"] != parent.plan["version"] + 1:
            raise TurnConflict("Invalid successor version")
        if plan.get("planning_target", "next_workout") != parent.plan.get(
            "planning_target", "next_workout"
        ):
            raise TurnConflict("A successor must keep its planning target")
        current, previous = plan.get("current_workout"), parent.plan.get("current_workout")
        if previous and (
            not current
            or current["sourceId"] != previous["sourceId"]
            or current["workout"]["id"] != previous["workout"]["id"]
            or current["version"] < previous["version"]
        ):
            raise TurnConflict("A successor requires the same current workout and a fresh snapshot")
        if (await plan_snapshot(session, parent))["superseded_by"]:
            raise TurnConflict("A newer plan exists; modify that version instead")
        expected = plan.get("parent_revision")
        if expected is None or expected != parent.revision:
            raise TurnConflict("Parent plan changed during generation; retry")
        changed = await session.execute(
            update(SessionPlanRow)
            .where(
                SessionPlanRow.id == parent_id,
                SessionPlanRow.user_id == user_id,
                SessionPlanRow.revision == expected,
            )
            .values(revision=expected + 1)
        )
        if changed.rowcount != 1:
            raise TurnConflict("Parent plan changed during generation; retry")
        event = {
            "event_id": plan["id"],
            "kind": "revised",
            "child_plan_id": plan["id"],
            "expected_revision": expected,
            "created_at": utcnow().isoformat() + "Z",
        }
        session.add(
            PlanEventRow(
                event_id=plan["id"],
                plan_id=parent_id,
                payload=event,
                receipt={"event": event},
                revision=expected + 1,
            )
        )
    session.add(
        SessionPlanRow(
            id=plan["id"],
            user_id=user_id,
            request_id=str(request_id),
            plan=plan,
            status="proposed",
            revision=0,
            created_at=utcnow().isoformat() + "Z",
        )
    )


def profile_snapshot(row):
    return {
        "version": row.version if row else 0,
        "profile": row.profile if row else TrainingProfile().model_dump(mode="json"),
        "saved_at": row.saved_at if row else None,
    }


async def load_training_profile(session, user_id):
    row = await session.scalar(
        select(TrainingProfileRow)
        .where(TrainingProfileRow.user_id == user_id)
        .order_by(TrainingProfileRow.version.desc())
        .limit(1)
    )
    return profile_snapshot(row)


class TrainingStore:
    def __init__(self, store):
        self.sessions = store.sessions

    async def review(self, user_id, options, catalog):
        from fitness_bot.training_review import load_training_review

        async with self.sessions() as session:
            return await load_training_review(session, user_id, options, catalog)

    async def profile(self, user_id):
        async with self.sessions() as session:
            return await load_training_profile(session, user_id)

    async def plans(self, user_id):
        async with self.sessions() as session:
            return await load_session_plans(session, user_id)

    async def plan(self, user_id, plan_id):
        async with self.sessions() as session:
            row = await session.get(SessionPlanRow, str(plan_id))
            if not row or row.user_id != user_id:
                raise ObjectNotFound("Plan not found")
            return await plan_snapshot(session, row)

    async def event(self, user_id, plan_id, payload):
        from fitness_bot.workout_storage import WorkoutRow

        plan_id = str(plan_id)
        content = payload.model_dump(mode="json")
        try:
            async with self.sessions.begin() as session:
                row = await session.get(SessionPlanRow, plan_id)
                if not row or row.user_id != user_id:
                    raise ObjectNotFound("Plan not found")
                existing = await session.get(PlanEventRow, str(payload.event_id))
                if existing:
                    if existing.plan_id != plan_id or existing.payload != content:
                        raise TurnConflict("Event ID already used")
                    return existing.receipt["result"]
                if payload.kind == "accepted":
                    current = await plan_snapshot(session, row)
                    if current["superseded_by"]:
                        raise TurnConflict("A newer plan exists; confirm that version instead")
                next_states = {
                    "proposed": {"accepted", "skipped", "feedback"},
                    "accepted": {"executed", "skipped", "feedback"},
                    "executed": {"feedback"},
                    "skipped": {"feedback"},
                }
                if (
                    row.revision != payload.expected_revision
                    or payload.kind not in next_states[row.status]
                ):
                    raise TurnConflict("Plan changed or transition unavailable; reload")
                event = {**content, "created_at": utcnow().isoformat() + "Z"}
                if payload.workout:
                    link = payload.workout
                    current = row.plan.get("current_workout")
                    if row.plan.get("planning_target") == "current_workout" and (
                        not current
                        or current["sourceId"] != link.source_id
                        or current["workout"]["id"] != link.workout_id
                    ):
                        raise TurnConflict("Execution must reference this plan's current workout")
                    workout = await session.get(
                        WorkoutRow, (user_id, link.source_id, link.workout_id)
                    )
                    if not workout or workout.deleted or workout.workout["status"] != "completed":
                        raise ObjectNotFound("Completed workout not found")
                    actual = {}
                    for record in workout.workout["records"]:
                        if record["isDraft"]:
                            continue
                        actual[record["exerciseId"]] = actual.get(record["exerciseId"], 0) + sum(
                            s["isCompleted"] for s in record["sets"]
                        )
                    event["actual"] = {
                        "source_id": link.source_id,
                        "workout_id": link.workout_id,
                        "snapshot_version": workout.version,
                        "completed_sets": sum(actual.values()),
                        "exercise_sets": actual,
                        "planned_sets": {i["exercise"]["id"]: i["sets"] for i in row.plan["items"]},
                    }
                status = row.status if payload.kind == "feedback" else payload.kind
                changed = await session.execute(
                    update(SessionPlanRow)
                    .where(
                        SessionPlanRow.id == plan_id,
                        SessionPlanRow.user_id == user_id,
                        SessionPlanRow.revision == payload.expected_revision,
                    )
                    .values(status=status, revision=payload.expected_revision + 1)
                )
                if changed.rowcount != 1:
                    raise TurnConflict("Plan changed; reload")
                snapshot = await plan_snapshot(session, row)
                final_events = [*snapshot["events"], event][-10:]
                result = {
                    **snapshot,
                    "status": status,
                    "revision": payload.expected_revision + 1,
                    "events": final_events,
                    "events_truncated": payload.expected_revision + 1 > len(final_events),
                    "execution": event if payload.kind == "executed" else snapshot["execution"],
                }
                session.add(
                    PlanEventRow(
                        event_id=str(payload.event_id),
                        plan_id=plan_id,
                        payload=content,
                        receipt={"event": event, "result": result},
                        revision=payload.expected_revision + 1,
                    )
                )
            return result
        except IntegrityError:
            raise TurnConflict("Concurrent event; retry with the same event ID") from None

    async def update_profile(self, user_id, payload):
        content = payload.profile.model_dump(mode="json")
        try:
            async with self.sessions.begin() as session:
                current = await load_training_profile(session, user_id)
                if (
                    current["version"] == payload.expected_version + 1
                    and current["profile"] == content
                ):
                    return current
                if current["version"] != payload.expected_version:
                    raise TurnConflict("Profile changed; reload before saving")
                row = TrainingProfileRow(
                    user_id=user_id,
                    version=payload.expected_version + 1,
                    profile=content,
                    saved_at=utcnow().isoformat() + "Z",
                )
                session.add(row)
                result = profile_snapshot(row)
            return result
        except IntegrityError:
            current = await self.profile(user_id)
            if current["version"] == payload.expected_version + 1 and current["profile"] == content:
                return current
            raise TurnConflict("Profile changed; reload before saving") from None
