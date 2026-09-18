"""Bounded, source-backed review details; entry timestamps never imply rest."""

from collections import Counter
from datetime import datetime

MAX_SESSIONS = 200
MAX_RECORDS = 100
MAX_SETS = 100


def source_ref(row):
    return {"source_id": row.source_id, "workout_id": row.workout_id, "version": row.version}


def gap_seconds(first, second):
    if not first or not second:
        return None
    value = (datetime.fromisoformat(second) - datetime.fromisoformat(first)).total_seconds()
    return value if value >= 0 else None


def session_details(row, catalog, valid_set):
    workout = row.workout
    duration = gap_seconds(workout.get("startedAt"), workout.get("endedAt"))
    paused = workout.get("pausedDurationSeconds", 0)
    active = duration - paused if duration is not None and 0 <= paused <= duration else None
    ordered = sorted(
        (r for r in workout.get("records", []) if not r.get("isDraft")),
        key=lambda r: (r["sequence"], r["id"]),
    )
    record_counts = Counter(r["sequence"] for r in ordered)
    records, overlaps = [], []
    for record in ordered[:MAX_RECORDS]:
        exercise = catalog.get(record["exerciseId"])
        items = sorted(record.get("sets", []), key=lambda s: (s["sequence"], s["id"]))
        set_counts = Counter(s["sequence"] for s in items)
        sets = [
            {
                "id": s["id"],
                "sequence": s["sequence"],
                "completed": s.get("isCompleted", False),
                "valid": valid_set(s, record["detailType"]),
                "load_type": s.get("loadType"),
                "load_value": s.get("loadValue"),
                "load_unit": s.get("loadUnit"),
                "repetitions": s.get("repetitions") if record["detailType"] != "duration" else None,
                "duration_seconds": s.get("durationSeconds")
                if record["detailType"] == "duration"
                else None,
                "rpe": s.get("rpe"),
                "difficulty": s.get("difficulty"),
            }
            for s in items[:MAX_SETS]
        ]
        changes = []
        for a, b in zip(sets, sets[1:], strict=False):
            if not all(
                s["valid"] and s["completed"] and set_counts[s["sequence"]] == 1 for s in (a, b)
            ):
                continue
            change = {"from_set_id": a["id"], "to_set_id": b["id"]}
            fields = (
                ("duration_seconds",) if record["detailType"] == "duration" else ("repetitions",)
            )
            for field in fields:
                if a[field] is not None and b[field] is not None:
                    change[field + "_delta"] = b[field] - a[field]
            if (
                (a["load_type"], a["load_unit"]) == (b["load_type"], b["load_unit"])
                and record["detailType"] != "duration"
                and a["load_value"] is not None
                and b["load_value"] is not None
            ):
                change["recorded_load_delta"] = b["load_value"] - a["load_value"]
            changes.append(change)
        muscles = (
            [exercise.target_muscle_code, *exercise.secondary_muscle_codes] if exercise else []
        )
        item = {
            "id": record["id"],
            "sequence": record["sequence"],
            "detail_type": record["detailType"],
            "set_order_ambiguous": any(n > 1 for n in set_counts.values()),
            "exercise_id": record["exerciseId"],
            "exercise_name": exercise.display_name_zh_hans
            if exercise
            else record["exerciseNameSnapshot"][:160],
            "recorded_at": record["occurredAt"],
            "primary_body_part": exercise.body_part_code if exercise else "unknown",
            "muscles": muscles,
            "sets": sets,
            "set_changes": changes,
            "sets_truncated": len(items) > MAX_SETS,
            "gap_from_previous_entry_seconds": gap_seconds(
                records[-1]["recorded_at"], record["occurredAt"]
            )
            if records
            and record_counts[records[-1]["sequence"]] == record_counts[record["sequence"]] == 1
            else None,
            "rest_seconds": None,
        }
        if (
            records
            and record_counts[records[-1]["sequence"]] == record_counts[record["sequence"]] == 1
        ):
            common = sorted(set(records[-1]["muscles"]) & set(muscles))
            if common:
                overlaps.append(
                    {
                        "first_record_id": records[-1]["id"],
                        "second_record_id": item["id"],
                        "muscles": common,
                    }
                )
        records.append(item)
    return {
        "source": source_ref(row),
        "title": workout.get("title"),
        "started_at": workout["startedAt"],
        "ended_at": workout.get("endedAt"),
        "duration_seconds": duration,
        "active_duration_seconds": active,
        "record_count": len(ordered),
        "order_ambiguous": any(n > 1 for n in record_counts.values()),
        "records_truncated": len(ordered) > MAX_RECORDS,
        "records": records,
        "overlaps": overlaps,
    }


def cadence_details(sessions, total):
    ordered = sorted(
        sessions,
        key=lambda s: (
            datetime.fromisoformat(s["started_at"]),
            s["source"]["source_id"],
            s["source"]["workout_id"],
        ),
    )

    def interval(a, b):
        return {
            "from_source": a["source"],
            "to_source": b["source"],
            "start_gap_seconds": gap_seconds(a["started_at"], b["started_at"]),
            "start_gap_hours": round(gap_seconds(a["started_at"], b["started_at"]) / 3600, 2),
        }

    parts = {}
    for item in ordered:
        for part in item["body_parts"]:
            parts.setdefault(part, []).append(item)
    return {
        "sessions": ordered,
        "intervals": [interval(a, b) for a, b in zip(ordered, ordered[1:], strict=False)],
        "body_part_intervals": {
            part: [interval(a, b) for a, b in zip(items, items[1:], strict=False)]
            for part, items in parts.items()
        },
        "total_workouts": total,
        "truncated": total > len(sessions),
    }


def review_heading(facts):
    if facts.get("scope") != "single":
        gaps = facts.get("cadence", {}).get("intervals", [])
        return (
            [
                "**训练间隔**（相邻训练开始时间）："
                + "、".join(f"{i['start_gap_hours']} 小时" for i in gaps[:20])
            ]
            if gaps
            else []
        )
    item = facts["session"]
    lines = [
        f"**单次训练复盘**：{item['title']} · {item['started_at']}",
        "**动作顺序**"
        + ("（存在重复序号，先后未确认）：" if item["order_ambiguous"] else "（按记录序号）：")
        + ("、" if item["order_ambiguous"] else " → ").join(
            r["exercise_name"] for r in item["records"]
        )
        + ("（仅展示部分动作）" if item["records_truncated"] else ""),
    ]
    for record in item["records"]:
        doses = [
            str(s["repetitions"]) + " 次"
            if record["detail_type"] != "duration"
            else str(s["duration_seconds"]) + " 秒"
            for s in record["sets"]
            if s["completed"] and s["valid"]
        ]
        label = "组序未确认：" if record["set_order_ambiguous"] else "按组记录："
        lines.append(
            record["exercise_name"]
            + " · "
            + label
            + ("、" if record["set_order_ambiguous"] else " → ").join(doses)
            + ("（仅展示部分组）" if record["sets_truncated"] else "")
        )
    if item["duration_seconds"] is not None:
        active = item["active_duration_seconds"]
        active_label = active if active is not None else "未知"
        lines.append(
            f"记录时长：{item['duration_seconds']} 秒；扣除记录暂停后的时长：{active_label} 秒。"
        )
    return lines
