"""User-confirmed observation conditions; immutable and bound to snapshot versions.

Comparisons describe recorded performance. Progression signals are conservative product
eligibility rules, not a physiological diagnosis or proof of readiness to increase load.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from fitness_bot.storage import ObjectNotFound, TurnConflict, utcnow
from fitness_bot.training_storage import ObservationAttestationRow
from fitness_bot.workout_storage import WorkoutRow


def snapshot(row):
    return {"version": row.version, **row.payload, "saved_at": row.saved_at}


async def save_attestation(sessions, user_id, observation_id, payload, catalog):
    from fitness_bot.training_review import _id, _valid_set

    content = payload.model_dump(mode="json", exclude={"expected_version"})
    source = payload.source
    try:
        async with sessions.begin() as session:
            workout = await session.scalar(
                select(WorkoutRow)
                .where(
                    WorkoutRow.user_id == user_id,
                    WorkoutRow.source_id == source.source_id,
                    WorkoutRow.workout_id == source.workout_id,
                )
                .with_for_update()
            )
            if not workout or workout.deleted or workout.workout["status"] != "completed":
                raise ObjectNotFound("Completed workout not found")
            if workout.version != source.version:
                raise TurnConflict("Workout changed; reload before confirming")
            record = next(
                (
                    r
                    for r in workout.workout["records"]
                    if r["id"] == source.record_id and not r["isDraft"]
                ),
                None,
            )
            if not record:
                raise ObjectNotFound("Completed exercise record not found")
            exercise = catalog.get(record["exerciseId"])
            matches = [
                s
                for s in record["sets"]
                if s["isCompleted"]
                and _valid_set(s, record["detailType"])
                and _id(
                    [
                        source.model_dump(),
                        (s.get("loadType"), s.get("loadUnit"), s.get("loadValue")),
                    ]
                )
                == observation_id
            ]
            if not matches or not exercise or not exercise.is_active:
                raise ObjectNotFound("Observation not found")
            kind = matches[0].get("loadType")
            convention = payload.load_convention
            allowed = {
                "external": {"total", "per_hand", "added", "machine_stack"},
                "added": {"added"},
                "assistance": {"assistance"},
                "machineLevel": {"machine_level"},
                "bodyweight": {"bodyweight"},
                "none": {"bodyweight"},
            }
            if convention not in allowed.get(kind, set()):
                raise ValueError("Load convention does not match the recorded load type")
            if convention == "per_hand" and exercise.equipment_code not in {
                "dumbbell",
                "kettlebell",
            }:
                raise ValueError("Per-hand load requires dumbbell or kettlebell equipment")
            at = datetime.fromisoformat(record["occurredAt"])
            now = datetime.now(UTC)
            if at > now:
                raise ValueError("Cannot confirm future performance")
            if payload.effort and not at <= payload.effort.observed_at <= min(
                at + timedelta(hours=6), now
            ):
                raise ValueError("Effort observation must be during or shortly after this record")
            current = await session.scalar(
                select(ObservationAttestationRow)
                .where(
                    ObservationAttestationRow.user_id == user_id,
                    ObservationAttestationRow.observation_id == observation_id,
                )
                .order_by(ObservationAttestationRow.version.desc())
                .limit(1)
            )
            version = current.version if current else 0
            if current and version == payload.expected_version + 1 and current.payload == content:
                return snapshot(current)
            if version != payload.expected_version:
                raise TurnConflict("Confirmation changed; reload before saving")
            row = ObservationAttestationRow(
                user_id=user_id,
                observation_id=observation_id,
                version=version + 1,
                payload=content,
                saved_at=utcnow().isoformat() + "Z",
            )
            session.add(row)
            return snapshot(row)
    except IntegrityError:
        raise TurnConflict("Concurrent confirmation; reload before saving") from None


async def attach_evidence(session, user_id, observations, now, latest_records):
    if not observations:
        return []
    rows = await session.scalars(
        select(ObservationAttestationRow)
        .where(
            ObservationAttestationRow.user_id == user_id,
            ObservationAttestationRow.observation_id.in_([o["id"] for o in observations]),
        )
        .order_by(ObservationAttestationRow.version.desc())
    )
    latest = {}
    for row in rows:
        latest.setdefault(row.observation_id, row)
    for observation in observations:
        row = latest.get(observation["id"])
        observation["attestation"] = snapshot(row) if row else None
        if not row or not row.payload["confirmed"]:
            continue
        observation["load_convention"] = row.payload["load_convention"]
        observation["conditions"] = row.payload["conditions"]
        observation["comparison_eligible"] = not observation["set_refs_truncated"]
        effort = row.payload.get("effort")
        if effort:
            observation["effort_scale"] = effort["scale"]
            observation["effort_timing"] = effort["timing"]
            observation["effort"] = effort
    # Select the latest two performances per exercise, including unconfirmed ones:
    # a later decline or changed condition must not be skipped to cherry-pick a pair.
    by_exercise = {}
    for observation in sorted(observations, key=lambda o: o["performed_at"], reverse=True):
        by_exercise.setdefault(observation["exercise_id"], []).append(observation)
    signals = []
    for exercise_id, values in by_exercise.items():
        if len(values) < 2:
            continue
        recent, prior = values[:2]
        if [r["source"] for r in latest_records.get(exercise_id, [])] != [
            recent["source"],
            prior["source"],
        ] or any(o.get("progression_blocked") for o in (recent, prior)):
            continue
        if not all(o["comparison_eligible"] for o in (recent, prior)):
            continue
        if (
            recent["source"]["workout_id"] == prior["source"]["workout_id"]
            and recent["source"]["source_id"] == prior["source"]["source_id"]
        ):
            continue
        recent_at, prior_at = (datetime.fromisoformat(o["performed_at"]) for o in (recent, prior))
        if not prior_at < recent_at <= now or now - recent_at > timedelta(days=14):
            continue
        equal = [
            "detail_type",
            "load_type",
            "load_unit",
            "load_value",
            "load_convention",
            "conditions",
            "completed_sets",
        ]
        if any(recent.get(k) != prior.get(k) for k in equal) or recent["detail_type"] == "duration":
            continue
        if any(len(set(o["set_doses"])) != 1 for o in (recent, prior)):
            continue
        a, b = recent.get("effort", {}), prior.get("effort", {})
        if any(
            e.get("scale") != "RIR"
            or e.get("timing") != "last_working_set"
            or not e.get("familiar_with_scale")
            for e in (a, b)
        ):
            continue
        if not 2 <= b["value"] <= a["value"] <= 6 or recent["repetitions"] < prior["repetitions"]:
            continue
        signals.append(
            {
                "exercise_id": exercise_id,
                "latest_observation_id": recent["id"],
                "prior_observation_id": prior["id"],
                "repetitions_per_set": recent["set_doses"][0],
                "sets": recent["completed_sets"],
                "rir": a["value"],
                "kind": "product_eligibility",
                "limitation": (
                    "两次同口径记录和用户余力反馈支持讨论小幅调整，"
                    "不证明已恢复或必须加量；一次只改变一个变量。"
                ),
            }
        )
    return signals


async def revalidate_plan_evidence(session, user_id, plan):
    """Lock source rows through commit; confirmation writes take the same lock."""
    from fitness_bot.prescriptions import legacy_sets

    loads = [row["load"] for item in plan["items"] for row in legacy_sets(item)]
    required = {load.get("observation_id") for load in loads} - {None}
    verified = {
        load.get("observation_id") for load in loads if load.get("strategy") != "history_reference"
    } - {None}
    if signal := plan.get("progression_basis"):
        verified.update([signal["latest_observation_id"], signal["prior_observation_id"]])
        required.update(verified)
    observations = {
        e["value"]["id"]: e["value"]
        for e in plan["evidence"]
        if e["metric"] == "recorded_performance"
    }
    # History load observations are persisted even if the model omitted their IDs.
    observations.update({o["id"]: o for o in plan.get("load_evidence", [])})
    for oid in sorted(required, key=lambda key: str(observations.get(key, {}).get("source"))):
        observation = observations.get(oid)
        if not observation or (oid in verified and not observation.get("attestation")):
            raise TurnConflict("Load confirmation missing; generate a new plan")
        source = observation["source"]
        workout = await session.scalar(
            select(WorkoutRow)
            .where(
                WorkoutRow.user_id == user_id,
                WorkoutRow.source_id == source["source_id"],
                WorkoutRow.workout_id == source["workout_id"],
            )
            .with_for_update()
        )
        if not workout or workout.deleted or workout.version != source["version"]:
            raise TurnConflict("Training evidence changed during generation; please retry")
        if oid not in verified:
            # An unverified reference still requires the exact owned source snapshot.
            # It never acquires comparison/progression eligibility from plan acceptance.
            continue
        confirmation = await session.scalar(
            select(ObservationAttestationRow)
            .where(
                ObservationAttestationRow.user_id == user_id,
                ObservationAttestationRow.observation_id == oid,
            )
            .order_by(ObservationAttestationRow.version.desc())
            .limit(1)
            .with_for_update()
        )
        if (
            not workout
            or workout.deleted
            or workout.version != source["version"]
            or not confirmation
            or not confirmation.payload["confirmed"]
            or confirmation.version != observation["attestation"]["version"]
        ):
            raise TurnConflict("Training evidence changed during generation; please retry")
