"""Reproducible facts over complete date windows, never the bounded chat summary."""

import hashlib
import json
import math
from collections import Counter
from datetime import UTC, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from fitness_bot.catalog import Catalog
from fitness_bot.review_details import MAX_SESSIONS, cadence_details, session_details
from fitness_bot.storage import ObjectNotFound
from fitness_bot.training_schemas import ReviewOptions
from fitness_bot.workout_storage import WorkoutRow

MAX_OBSERVATIONS = 200
MAX_EVIDENCE = 200
MAX_SET_REFS = 100


@lru_cache(maxsize=1)
def review_catalog():
    return Catalog.load()


def _window(start, end):
    return {
        "start_date": start.isoformat(),
        "end_date_exclusive": end.isoformat(),
        "days": (end - start).days,
        "completed_workouts": 0,
        "completed_sets": 0,
        "valid_sets": 0,
        "invalid_sets": 0,
        "primary_body_part_sets": Counter(),
        "primary_body_part_workouts": Counter(),
        "training_dates": [],
        "date_gaps_days": [],
        "workouts_per_week": 0,
        "sets_per_week": 0,
        "sources": [],
        "sources_truncated": False,
    }


def _positive(value):
    return isinstance(value, (int, float)) and math.isfinite(value) and value > 0


def _valid_set(item, detail_type):
    if detail_type == "duration":
        return _positive(item.get("durationSeconds"))
    if not _positive(item.get("repetitions")):
        return False
    kind, unit, value = item.get("loadType"), item.get("loadUnit"), item.get("loadValue")
    if kind in {"none", "bodyweight"}:
        return value is None or value == 0
    if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        return False
    return unit == "level" if kind == "machineLevel" else unit in {"kg", "lb"}


def _id(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:24]


async def load_training_review(session, user_id, options=None, catalog=None, *, now=None):
    options = options or ReviewOptions()
    from fitness_bot.custom_exercise_storage import personal_catalog

    catalog = await personal_catalog(session, user_id, catalog or review_catalog())
    now = now or datetime.now(UTC)
    count_based = options.scope == "multiple" and options.selection_mode == "recent_workouts"
    zone = ZoneInfo(options.time_zone)
    end = options.end_date or (now.astimezone(zone).date() + timedelta(days=1))
    split = end - timedelta(days=options.recent_days)
    start = split - timedelta(days=options.baseline_days)
    lower = datetime.combine(start, time(), zone).astimezone(UTC)
    upper = datetime.combine(end, time(), zone).astimezone(UTC)
    split_at = datetime.combine(split, time(), zone).astimezone(UTC)
    # Upload validation normalizes timestamps to UTC ISO strings. Prefix bounds
    # include exact lower midnight with or without fractional seconds, and exclude
    # exact upper midnight. JSON comparison works in both MySQL and SQLite.
    query = (
        select(WorkoutRow)
        .where(
            WorkoutRow.user_id == user_id,
            WorkoutRow.deleted.is_(False),
            WorkoutRow.workout["status"].as_string() == "completed",
            WorkoutRow.workout["startedAt"].as_string() < upper.strftime("%Y-%m-%dT%H:%M:%S"),
        )
        .order_by(
            func.replace(WorkoutRow.workout["startedAt"].as_string(), "Z", "").desc(),
            WorkoutRow.source_id,
            WorkoutRow.workout_id,
        )
    )
    selected = None
    if options.scope == "single":
        if options.workout_id:
            query = query.where(
                WorkoutRow.source_id == options.source_id,
                WorkoutRow.workout_id == options.workout_id,
            )
        candidates = await session.stream_scalars(query.execution_options(yield_per=100))
        async for candidate in candidates:
            if datetime.fromisoformat(candidate.workout["startedAt"]) <= now:
                selected = candidate
                break
        await candidates.close()
        if selected is None:
            raise ObjectNotFound("No matching completed workout for review")
        split = datetime.fromisoformat(selected.workout["startedAt"]).astimezone(zone).date()
        end, start = split + timedelta(days=1), split - timedelta(days=options.baseline_days)
        split_at = datetime.combine(split, time(), zone).astimezone(UTC)
        query = query.where(
            WorkoutRow.source_id == selected.source_id, WorkoutRow.workout_id == selected.workout_id
        )
    elif not count_based:
        query = query.where(
            WorkoutRow.workout["startedAt"].as_string() >= lower.strftime("%Y-%m-%dT%H:%M:%S")
        )
    rows = await session.stream_scalars(query.execution_options(yield_per=100))
    cadence_sessions = []
    recent, baseline = _window(split, end), _window(start, split)
    observations = []
    latest_records = {}
    observation_count = snapshot_count = unknown_exercises = 0
    limitations = [
        "仅覆盖当前账号已同步的已完成训练；无法确认设备同步是否完整，空档不等于停训。",
        "以训练开始日归属所选窗口；不同长度的日期窗口按每周频率对照。",
        "组数是记录为已完成的组数，不代表有效组；部位按目录主部位归属，不重复计入辅助肌群。",
        "只有用户逐条确认的记录带有负重口径和条件依据；其余历史即使单位相同也不能自动认定可比。",
        "计划执行关联与全部处方达标分开统计；没有已审阅动作模式标签，不推断模式分布。",
    ]
    async for row in rows:
        at = datetime.fromisoformat(row.workout["startedAt"])
        if at > now:
            continue
        if count_based and snapshot_count >= options.workout_count:
            break
        snapshot_count += 1
        window = recent if count_based or at >= split_at else baseline
        period = "recent" if count_based or at >= split_at else "baseline"
        sets_before = window["completed_sets"]
        window["completed_workouts"] += 1
        window["training_dates"].append(at.astimezone(zone).date().isoformat())
        source = {"source_id": row.source_id, "workout_id": row.workout_id, "version": row.version}
        if len(window["sources"]) < MAX_EVIDENCE:
            window["sources"].append(source)
        else:
            window["sources_truncated"] = True
        parts = set()
        for record in row.workout.get("records", []):
            if record.get("isDraft", False):
                continue
            ref = {**source, "record_id": record["id"]}
            candidates = latest_records.setdefault(record["exerciseId"], [])
            candidates.append({"source": ref, "performed_at": record["occurredAt"]})
            candidates.sort(
                key=lambda value: datetime.fromisoformat(value["performed_at"]), reverse=True
            )
            del candidates[2:]
            progression_blocked = not record["sets"] or any(
                not s.get("isCompleted") or not _valid_set(s, record["detailType"])
                for s in record["sets"]
            )
            exercise = catalog.get(record["exerciseId"])
            part = exercise.body_part_code if exercise else "unknown"
            groups = {}
            for item in record.get("sets", []):
                if not item.get("isCompleted", False):
                    continue
                window["completed_sets"] += 1
                window["primary_body_part_sets"][part] += 1
                parts.add(part)
                if not exercise:
                    unknown_exercises += 1
                if not _valid_set(item, record["detailType"]):
                    window["invalid_sets"] += 1
                    continue
                window["valid_sets"] += 1
                key = (item.get("loadType"), item.get("loadUnit"), item.get("loadValue"))
                if key not in groups:
                    ref = {**source, "record_id": record["id"]}
                    groups[key] = {
                        "id": _id([ref, key]),
                        "period": period,
                        "source": ref,
                        "exercise_id": record["exerciseId"],
                        "exercise_name": exercise.display_name_zh_hans
                        if exercise
                        else record["exerciseNameSnapshot"][:160],
                        "performed_at": record["occurredAt"],
                        "detail_type": record["detailType"],
                        "load_type": key[0],
                        "load_unit": key[1],
                        "load_value": key[2],
                        "load_convention": "unknown",
                        "comparison_eligible": False,
                        "progression_blocked": progression_blocked,
                        "completed_sets": 0,
                        "repetitions": 0,
                        "duration_seconds": 0,
                        "set_ids": [],
                        "set_doses": [],
                        "set_refs_truncated": False,
                        "effort_scale": "unknown",
                        "effort_timing": "unknown",
                    }
                observation = groups[key]
                observation["completed_sets"] += 1
                if record["detailType"] == "duration":
                    observation["duration_seconds"] += item["durationSeconds"]
                else:
                    observation["repetitions"] += item["repetitions"]
                if len(observation["set_ids"]) < MAX_SET_REFS:
                    observation["set_ids"].append(item["id"])
                    observation["set_doses"].append(
                        item.get("durationSeconds")
                        if record["detailType"] == "duration"
                        else item.get("repetitions")
                    )
                else:
                    observation["set_refs_truncated"] = True
            observation_count += len(groups)
            observations.extend(
                list(groups.values())[: max(0, MAX_OBSERVATIONS - len(observations))]
            )
        window["primary_body_part_workouts"].update(parts)
        if len(cadence_sessions) < MAX_SESSIONS:
            cadence_sessions.append(
                {
                    "source": source,
                    "started_at": row.workout["startedAt"],
                    "body_parts": sorted(parts),
                    "completed_sets": window["completed_sets"] - sets_before,
                }
            )
    await rows.close()
    if count_based:
        dates = sorted(set(recent["training_dates"]))
        if dates:
            split = datetime.fromisoformat(dates[0]).date()
            end = datetime.fromisoformat(dates[-1]).date() + timedelta(days=1)
        else:
            split = end
        start = split
        recent.update(
            start_date=split.isoformat(),
            end_date_exclusive=end.isoformat(),
            days=(end - split).days,
        )
        baseline = _window(split, split)
        limitations[1] = (
            "按最近已完成训练次数取样，日期仅描述样本跨度；未进行固定日期窗口或此前窗口对照。"
        )
    for window in (recent, baseline):
        unmatched = window["primary_body_part_sets"].get("unknown", 0)
        window["body_part_coverage"] = {
            "catalog_matched_sets": window["completed_sets"] - unmatched,
            "unclassified_sets": unmatched,
            "total_completed_sets": window["completed_sets"],
        }
        dates = sorted(set(window["training_dates"]))
        window["training_dates"] = dates
        window["date_gaps_days"] = [
            (datetime.fromisoformat(b) - datetime.fromisoformat(a)).days
            for a, b in zip(dates, dates[1:], strict=False)
        ]
        window["selection_mode"] = "recent_workouts" if count_based else "date_window"
        window["workouts_per_week"] = (
            None if count_based else round(window["completed_workouts"] * 7 / window["days"], 2)
        )
        window["sets_per_week"] = (
            None if count_based else round(window["completed_sets"] * 7 / window["days"], 2)
        )
    if options.scope == "multiple" and not count_based:
        await _add_plan_execution(session, user_id, recent, baseline)
    elif options.scope == "single":
        limitations[1] = "本次仅统计选中的已完成训练；此前窗口未纳入本次单次复盘。"
    limitations.append(
        "没有逐组开始与结束时间，无法计算实际组间或动作间休息；记录录入间隔不等于休息时间。"
    )
    if snapshot_count > MAX_SESSIONS:
        limitations.append("训练间隔仅列出最近部分训练；完整窗口计数未截断。")
    from fitness_bot.training_storage import load_training_profile

    profile = await load_training_profile(session, user_id)
    confirmed_cycle = profile["profile"].get("confirmed_cycle")
    cycle = {
        "status": "confirmed" if confirmed_cycle else "unknown",
        "confirmed_cycle": confirmed_cycle,
        "overlaps_review_window": bool(
            confirmed_cycle
            and confirmed_cycle["starts_on"] < end.isoformat()
            and confirmed_cycle["ends_on"]
            >= (split if options.scope == "single" else start).isoformat()
        ),
    }
    limitations.append("训练间隔只描述已同步记录的节奏，不据此判断恢复充分或已进入训练周期。")
    from fitness_bot.training_evidence import attach_evidence

    progression_signals = await attach_evidence(session, user_id, observations, now, latest_records)
    if not snapshot_count:
        limitations.insert(0, "当前账号在所选日期范围内没有可分析的已同步完成训练。")
    if recent["invalid_sets"] + baseline["invalid_sets"]:
        limitations.append("存在无效负重或次数/时长：保留完成组计数，但从剂量观察中排除。")
    if unknown_exercises:
        limitations.append(
            "部分动作的来源 ID 未匹配当前目录，归入待归类动作；这不表示动作本身没有训练部位。"
            "已匹配部分不能代表完整部位分布，不能据此断言某部位缺失或训练偏科。"
        )
    if observation_count > len(observations):
        limitations.append("详细动作证据仅展示最近部分；窗口总数仍覆盖全部匹配快照。")
    return {
        "rules_version": "training-review-v3",
        "selection": {
            "mode": "single" if options.scope == "single" else options.selection_mode,
            "requested_count": options.workout_count if count_based else None,
            "selected_count": snapshot_count,
            "insufficient_history": count_based and snapshot_count < options.workout_count,
            "baseline_included": options.scope == "multiple" and not count_based,
        },
        "scope": options.scope,
        "body_part_labels": {
            code: "待归类动作" if code == "unknown" else catalog.label("bodyPart", code)
            for code in set(recent["primary_body_part_sets"])
            | set(baseline["primary_body_part_sets"])
        },
        "session": session_details(selected, catalog, _valid_set) if selected else None,
        "cadence": cadence_details(cadence_sessions, snapshot_count),
        "cycle": cycle,
        "generated_at": now.isoformat(),
        "time_zone": options.time_zone,
        "recent": recent,
        "baseline": baseline,
        "snapshot_count": snapshot_count,
        "observations": observations,
        "progression_signals": progression_signals,
        "observation_count": observation_count,
        "observations_truncated": observation_count > len(observations),
        "synchronization_complete": None,
        "plan_completion_rate": None,
        "movement_pattern_distribution": None,
        "limitations": limitations,
    }


async def _add_plan_execution(session, user_id, recent, baseline):
    from fitness_bot.training_storage import PlanEventRow, SessionPlanRow

    accepted = list(
        await session.scalars(
            select(SessionPlanRow)
            .join(
                PlanEventRow,
                PlanEventRow.plan_id == SessionPlanRow.id,
            )
            .where(
                SessionPlanRow.user_id == user_id,
                PlanEventRow.payload["kind"].as_string() == "accepted",
            )
        )
    )
    completed_ids = {r.id for r in accepted if r.status == "executed"}
    superseded = {r.plan.get("parent_plan_id") for r in accepted} - completed_ids
    for window in (recent, baseline):
        summary = {
            "accepted_plans": 0,
            "linked_executions": 0,
            "skipped_plans": 0,
            "planned_working_sets": 0,
            "recorded_working_sets": 0,
            "execution_link_rate": None,
            "plan_ids": [],
            "limitation": "关联实际训练不代表完成了全部规定组次；只比较有明确日期的已接受方案。",
        }
        for row in accepted:
            target = row.plan.get("target_date")
            if (
                row.id in superseded
                or not target
                or not window["start_date"] <= target < window["end_date_exclusive"]
            ):
                continue
            summary["accepted_plans"] += 1
            summary["planned_working_sets"] += row.plan["working_sets"]
            summary["skipped_plans"] += row.status == "skipped"
            if len(summary["plan_ids"]) < MAX_EVIDENCE:
                summary["plan_ids"].append(row.id)
            execution = await session.scalar(
                select(PlanEventRow)
                .where(
                    PlanEventRow.plan_id == row.id,
                    PlanEventRow.payload["kind"].as_string() == "executed",
                )
                .limit(1)
            )
            if execution:
                summary["linked_executions"] += 1
                summary["recorded_working_sets"] += execution.receipt["event"]["actual"][
                    "completed_sets"
                ]
        if summary["accepted_plans"]:
            summary["execution_link_rate"] = round(
                summary["linked_executions"] / summary["accepted_plans"], 3
            )
        summary["plan_ids_truncated"] = summary["accepted_plans"] > len(summary["plan_ids"])
        window["plan_execution"] = summary
