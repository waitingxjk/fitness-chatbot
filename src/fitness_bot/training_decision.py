"""Constrain model interpretations and plans using server facts and catalog metadata."""

import math
import re
from datetime import UTC, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from fitness_bot.planning import (
    current_exercise_ids,
    plan_outcome,
    planning_metadata,
    planning_target,
)
from fitness_bot.training_schemas import TrainingProfile

RULES_VERSION = "training-decision-v1.1"
LOAD_LABELS = {
    "total": "总重量",
    "per_hand": "每只重量",
    "added": "附加重量",
    "assistance": "辅助重量",
    "machine_stack": "同一器械标示重量",
    "machine_level": "同一器械档位",
    "bodyweight": "自重 / 未加负重",
    "unknown": "口径未确认",
}
SOURCES = [
    {
        "id": "E1",
        "title": "ACSM 抗阻训练立场文件（摘要）",
        "url": "https://pubmed.ncbi.nlm.nih.gov/41843416/",
    },
    {
        "id": "E2",
        "title": "动作顺序系统综述（摘要）",
        "url": "https://pubmed.ncbi.nlm.nih.gov/32077380/",
    },
    {
        "id": "E3",
        "title": "自由重量与固定器械系统综述（摘要）",
        "url": "https://pubmed.ncbi.nlm.nih.gov/37582807/",
    },
    {"id": "E5", "title": "减量安排专家共识", "url": "https://pubmed.ncbi.nlm.nih.gov/37730925/"},
]
RULES = """研究与产品策略须区分。E1：目标决定训练侧重点，总体研究不证明个人最优剂量。
E2：优先安排的动作通常更有利于该动作力量提升，不承诺任意动作之间完全没有疲劳影响。
E3：力量变化有测试特异性，机器标示重量不能迁移为自由重量。
E5 是专家共识：结合反馈降低训练压力，不支持人人固定周数/百分比的减量处方。
P01 默认复盘最近7次已完成训练；明确日期请求才用日期窗口。数值由程序计算，不能重算、改写或伪造。
P02 按明确目标、已接受方案、近期训练和当天约束确定主题，不能只按最久未训练部位。
P03 默认只建议改变一个主要变量。没有可比表现、主观余力和稳定记录时不提加量。
P04 已确认周期与本次调整建议分开；一次疲劳不等于处于减量期，未知就是未知。
P05 完整方案使用真实目录动作，包含目的、组次/时长、余力、选重、休息和替代条件。
P06 优先保护重点动作；目录共同肌群只表示重叠，不能编造握力/腰背/技术疲劳边或百分比。
P07 准备、执行、组间休息、切换和缓冲全部计时；不足时删低优先级项目，不压缩休息。
P08 替代动作要重新校验；重量默认试做选择，不从不同机器或未知单双只口径搬用重量。
所有具体组次、休息、时间预算仍是待评估的产品提案，不宣称已经专业审阅或个体验证。
"""


def effective_profile(request):
    return TrainingProfile.model_validate(
        (request.context.training_profile or {}).get("profile", {})
    )


def plan_question(request):
    if planning_target(request) == "current_workout":
        if request.context.current_workout is None:
            return "请从 App 刷新本次训练快照，包含当前已完成和待完成的动作与组。"
        if request.context.remaining_minutes is None:
            return "在现有待完成内容之外，你还能为新增安排预留多少分钟？"
    profile = effective_profile(request)
    if profile.goal is None:
        return "你目前最希望改善的训练目标是什么：力量、增肌，还是保持整体体能？"
    if profile.experience is None:
        return "你是否已经熟悉常用力量动作，还是刚开始训练？"
    if profile.session_minutes is None and planning_target(request) != "current_workout":
        return "这次训练大约有多少分钟可以使用？"
    if not profile.equipment:
        return "这次可以使用哪些器械？"
    readiness = profile.readiness
    now = datetime.now(UTC)
    if readiness is None or not timedelta(0) <= now - readiness.observed_at <= timedelta(hours=36):
        return "请补充当前状态：有无疼痛、明显疲劳，以及最近睡眠如何？"
    if readiness.pain == "present":
        return (
            "先停止引发疼痛的动作；当前不生成负重训练方案。"
            "请说明不适情况，持续或明显不适时寻求专业评估。"
        )
    if (
        readiness.pain == "unknown"
        or readiness.fatigue == "unknown"
        or readiness.sleep == "unknown"
    ):
        return "请补充当前状态：有无疼痛、明显疲劳，以及最近睡眠如何？"
    return None


def evidence_index(facts):
    result = {}
    periods = (
        ("recent",)
        if facts.get("selection", {}).get("baseline_included") is False
        else ("recent", "baseline")
    )
    for period in periods:
        window = facts.get(period, {})
        result[f"{period}.frequency"] = {
            "kind": "fact",
            "window": period,
            "metric": "frequency",
            "value": window,
        }
        result[f"{period}.distribution"] = {
            "kind": "fact",
            "window": period,
            "metric": "primary_body_part_sets",
            "value": window.get("primary_body_part_sets", {}),
        }
    for observation in facts.get("observations", []):
        result[observation["id"]] = {
            "kind": "fact",
            "metric": "recorded_performance",
            "value": observation,
        }
    for key, metric, value in (
        ("session.sequence", "session_sequence", facts.get("session")),
        ("session.timing", "session_timing", facts.get("session")),
        ("cadence.spacing", "training_spacing", facts.get("cadence")),
        ("cycle.context", "confirmed_cycle", facts.get("cycle")),
    ):
        if value is not None:
            result[key] = {"kind": "fact", "metric": metric, "value": value}
    return result


def validate_review(draft, facts):
    from fitness_bot.review_presentation import FACT_PATTERN, resolve_fact_text, review_charts

    evidence = evidence_index(facts)
    prose = [*draft.keep, *draft.next_adjustments, draft.question or ""]
    for finding in draft.findings:
        prose.extend(
            [
                finding.title,
                FACT_PATTERN.sub("", finding.conclusion),
                finding.comparison,
                finding.possible_explanation,
                finding.suggested_action,
                *finding.missing_information,
            ]
        )
    if any("{{fact:" in text for text in prose):
        raise ValueError("Inline facts are only supported in finding conclusions")
    # Do not treat a narrow, explicit denial of a claim as the claim itself.
    # Remove only that occurrence: a separate affirmative claim remains checked.
    claims = [
        re.sub(
            r"(?:不等于|不等同于|不能(?:直接)?(?:当作|视为|算作))有效组|"
            r"(?:不能|无法|不足以)(?:据此|直接)?(?:认定|判断|确认|证明|解释为)"
            r"(?:已经|已|正在)?(?:恢复训练|开始停训)",
            "",
            text,
        )
        for text in prose
    ]
    if any(
        re.search(r"\d|[二两三四五六七八九十百]+\s*(组|次|天|周)|有效组|comparison_eligible", text)
        for text in claims
    ):
        raise ValueError("Review prose must leave numbers and effective-set claims to server facts")
    if facts.get("synchronization_complete") is not True and any(
        "恢复训练" in text or "开始停训" in text for text in claims
    ):
        raise ValueError("Incomplete synchronization cannot establish a return or stop in training")
    findings = []
    for finding in draft.findings:
        if any(key not in evidence for key in finding.evidence_ids):
            raise ValueError("Unknown review evidence")
        findings.append(
            {
                **finding.model_dump(),
                "conclusion": resolve_fact_text(finding.conclusion, facts, finding.evidence_ids),
                "kind": "inference",
                "evidence": [evidence[key] for key in finding.evidence_ids],
            }
        )
    return {
        "facts": facts,
        "charts": review_charts(facts),
        "findings": findings,
        "keep": draft.keep,
        "next_adjustments": draft.next_adjustments,
        "question": draft.question,
        "rules_version": RULES_VERSION,
        "sources": SOURCES,
    }


def validate_session_plan(draft, request, catalog, *, target_date=None, original_plan=None):
    if question := plan_question(request):
        raise ValueError(f"Missing planning inputs: {question}")
    profile = effective_profile(request)
    is_current = planning_target(request) == "current_workout"
    time_budget = request.context.remaining_minutes if is_current else profile.session_minutes
    existing_ids = current_exercise_ids(request)

    # A title may repeat the user's exact time cap; actual duration is still computed
    # below. Other numeric dose/load directions remain structured-only.
    def checked_title_time(match):
        token = match.group(1)
        digits = {c: n for n, c in enumerate("零一二三四五六七八九")}
        digits["两"] = 2
        if token.isascii() and token.isdigit():
            value = int(token)
        else:
            value = current = 0
            for char in token:
                if char in digits:
                    current = digits[char]
                elif char in {"十", "百"}:
                    value += (current or 1) * (10 if char == "十" else 100)
                    current = 0
            value += current
        if value != time_budget:
            raise ValueError("Title time must match the available time")
        return ""

    title_for_check = re.sub(
        r"([0-9零一二两三四五六七八九十百]+)\s*分钟", checked_title_time, draft.theme
    )
    # Dose/load directives belong to structured fields, not uncontrolled prose.
    # This is a consistency check, not a claim to solve semantic model safety.
    narrative = [
        title_for_check,
        draft.rationale,
        draft.adjustment_reason,
        draft.preparation,
        *draft.feedback_questions,
    ]
    for item in draft.items:
        narrative.extend([item.purpose, item.alternative_condition])
        if re.search(r"(?i)kg|lb|公斤|千克|磅|%|％|百分之", item.effort):
            raise ValueError("Dose/load narrative must use structured fields")
    if any(
        re.search(
            r"(?i)\d|kg|lb|公斤|千克|磅|%|％|百分之|"
            r"(?<![后第])[一二三四五六七八九十百]+\s*(组|分钟|秒)|[二三四五六七八九十百]+\s*次",
            text,
        )
        for text in narrative
    ):
        raise ValueError("Dose/load narrative must use structured fields")
    facts = request.context.training_review or {}
    evidence = evidence_index(facts)
    if any(key not in evidence for key in draft.evidence_ids):
        raise ValueError("Unknown plan evidence")
    low_readiness = profile.readiness.fatigue == "high" or profile.readiness.sleep == "poor"
    if low_readiness and draft.adjustment not in {"reduce", "rest"}:
        raise ValueError("High fatigue requires reduced demands or rest")
    progression = None
    if draft.adjustment.startswith("increase"):
        progression = next(
            (
                s
                for s in facts.get("progression_signals", [])
                if s["exercise_id"] == draft.progression_exercise_id
            ),
            None,
        )
        if not progression:
            raise ValueError("Progression requires comparable evidence for the specific exercise")
        required = {progression["latest_observation_id"], progression["prior_observation_id"]}
        if not required <= set(draft.evidence_ids):
            raise ValueError("Progression must cite both performance records")
        candidate = next(
            (i for i in draft.items if i.exercise_id == draft.progression_exercise_id), None
        )
        if (
            not candidate
            or candidate.role != "priority"
            or candidate.load_strategy != "history"
            or candidate.load_observation_id != progression["latest_observation_id"]
        ):
            raise ValueError("Progression must retain the evidenced priority exercise and load")
        expected_sets = progression["sets"] + (draft.adjustment == "increase_sets")
        expected_reps = progression["repetitions_per_set"] + (draft.adjustment == "increase_reps")
        mixed_progression = candidate.set_prescriptions and any(
            row.repetitions != expected_reps
            for row in candidate.set_prescriptions
            if row.kind == "working"
        )
        if (
            candidate.sets != expected_sets
            or candidate.repetitions != expected_reps
            or mixed_progression
        ):
            raise ValueError("Progression may change only one variable by one step")
        phase = profile.confirmed_cycle
        day = target_date or datetime.now(ZoneInfo(profile.time_zone)).date()
        if (
            phase
            and phase.starts_on <= day <= phase.ends_on
            and phase.phase in {"deload", "recovery"}
        ):
            raise ValueError("Progression conflicts with the confirmed cycle")
    elif draft.progression_exercise_id is not None:
        raise ValueError("Progression exercise requires an increase adjustment")
    if draft.adjustment == "rest" and draft.items:
        raise ValueError("Rest plan cannot contain work sets")
    if draft.adjustment != "rest" and not draft.items and not is_current:
        raise ValueError("Training plan needs work items")
    original = original_plan or next(
        (
            p["plan"]
            for p in request.context.session_plans
            if p["plan"]["id"] == str(request.context.plan_to_modify)
        ),
        None,
    )
    if draft.adjustment == "reduce" and original:
        original_items = {i["exercise"]["id"]: i for i in original["items"]}
        if sum(i.sets for i in draft.items) >= original["working_sets"] and not (
            is_current and not draft.items
        ):
            raise ValueError("Reduced plan must decrease work sets from the original")
        for item in draft.items:
            previous = original_items.get(item.exercise_id)
            if previous is None or item.sets > previous["sets"]:
                raise ValueError("Reduced plan cannot add new or more demanding work")
            if item.repetitions and item.repetitions > (previous["repetitions"] or 0):
                raise ValueError("Reduced plan cannot increase repetitions")
            if item.duration_seconds and item.duration_seconds > (
                previous["duration_seconds"] or 0
            ):
                raise ValueError("Reduced plan cannot increase duration")
            if item.target_rir_min < previous.get("target_rir_min", 2):
                raise ValueError("Reduced plan cannot increase effort")
    items = []
    seen = set()
    for item in draft.items:
        if item.exercise_id in existing_ids:
            raise ValueError(
                "Exercise already exists in the current workout; only new exercises allowed"
            )
        exercise = catalog.get(item.exercise_id)
        if exercise is None or not exercise.is_active or exercise.id in seen:
            raise ValueError("Plan requires unique active catalog exercises")
        seen.add(exercise.id)
        if exercise.equipment_code not in profile.equipment:
            raise ValueError("Unavailable equipment")
        if (exercise.detail_type == "duration") != (item.duration_seconds is not None):
            raise ValueError("Dose incompatible with catalog detail type")
        load = {
            "strategy": "trial",
            "value": None,
            "unit": None,
            "explanation": (
                "通过轻负重试做选择可控制且保留余力的重量；不直接搬用历史或替代器械重量。"
            ),
        }
        if item.load_strategy == "history":
            observation = next(
                (o for o in facts.get("observations", []) if o["id"] == item.load_observation_id),
                None,
            )
            if (
                not observation
                or observation["exercise_id"] != exercise.id
                or not observation.get("comparison_eligible")
            ):
                raise ValueError("Historical load requires a comparable same-exercise observation")
            load = {
                "strategy": "history",
                "value": observation["load_value"],
                "unit": observation["load_unit"],
                "observation_id": observation["id"],
                "explanation": "同一动作和已确认口径的历史记录，仅作为当天试做起点。",
            }
            load["convention"] = observation["load_convention"]
            if (
                progression
                and item.exercise_id == draft.progression_exercise_id
                and draft.adjustment == "increase_load"
            ):
                if observation["load_type"] != "external" or observation["load_unit"] not in {
                    "kg",
                    "lb",
                }:
                    raise ValueError("Load increase requires an external-weight observation")
                load["strategy"] = "trial_increment"
                load["explanation"] = (
                    "先以所列历史重量试做；条件一致且余力足够时，仅试用器械可提供的最小增量。"
                    "次数和组数保持不变，无法维持动作控制或目标余力时退回起点。"
                )
        alternatives = []
        discarded_alternatives = []
        for alternative_id in item.alternative_exercise_ids:
            alternate = catalog.get(alternative_id)
            if (
                not alternate
                or not alternate.is_active
                or alternate.equipment_code not in profile.equipment
            ):
                discarded_alternatives.append(
                    {"exercise_id": alternative_id, "reason": "unavailable"}
                )
                continue
            if (
                alternate.target_muscle_code != exercise.target_muscle_code
                or alternate.detail_type != exercise.detail_type
            ):
                discarded_alternatives.append(
                    {"exercise_id": alternative_id, "reason": "incompatible_target_or_dose"}
                )
                continue
            alternatives.append(
                {
                    "exercise_id": alternate.id,
                    "name": alternate.display_name_zh_hans,
                    "requires_replan": True,
                    "load_transfer_allowed": False,
                }
            )
        from fitness_bot.prescriptions import model_sets, summarize_sets

        rows = model_sets(item, exercise, load)
        summary = summarize_sets(
            rows, seconds_per_rep=item.seconds_per_rep, switch_seconds=item.switch_seconds
        )
        items.append(
            {
                **item.model_dump(
                    exclude={
                        "exercise_id",
                        "load_strategy",
                        "load_observation_id",
                        "alternative_exercise_ids",
                    }
                ),
                "exercise": exercise.model_dump(mode="json", by_alias=True),
                "equipment_label": catalog.label("equipment", exercise.equipment_code),
                "body_part_label": catalog.label("bodyPart", exercise.body_part_code),
                "effort": (
                    f"保留约 {item.target_rir_min}–{item.target_rir_max} 次余力，"
                    "以动作控制和当天感受为准。"
                ),
                "alternative_condition": (
                    "仅在器械不可用、场地或偏好改变时提出替换；需重新校验技能、剂量和用时。"
                    "出现疼痛时先停止相关动作，不自动换动作继续。"
                ),
                "load": load,
                "alternatives": alternatives,
                "discarded_alternatives": discarded_alternatives,
                **summary,
                "set_prescriptions": rows,
            }
        )
    if draft.adjustment == "reduce" and original:
        from fitness_bot.prescriptions import legacy_sets

        for item in items:
            rows = item["set_prescriptions"]
            before = legacy_sets(original_items[item["exercise"]["id"]])
            if len(rows) > len(before):
                raise ValueError("Reduced plan cannot add warmup or working sets")
            for field in ("repetitions", "duration_seconds"):
                if sum(row.get(field) or 0 for row in rows) > sum(
                    row.get(field) or 0 for row in before
                ):
                    raise ValueError("Reduced plan cannot increase total dose including warmups")
                if max(row.get(field) or 0 for row in rows) > max(
                    row.get(field) or 0 for row in before
                ):
                    raise ValueError("Reduced plan cannot increase any set dose including warmups")
            if min(row["target_rir_min"] for row in rows) < min(
                row["target_rir_min"] for row in before
            ):
                raise ValueError("Reduced plan cannot increase effort including warmups")
    ranks = {"priority": 0, "assistance": 1, "supplement": 2}
    if [ranks[i["role"]] for i in items] != sorted(ranks[i["role"]] for i in items):
        raise ValueError("Protect priority items before assistance and supplements")
    preferred = [i for i in items if i["exercise"]["id"] in profile.priority_exercise_ids]
    if preferred and items[0] not in preferred:
        raise ValueError("Configured priority exercise must come first when included")
    if items and items[0]["role"] != "priority":
        raise ValueError("Identify and protect the session priority")
    fixed = draft.preparation_minutes + draft.finish_minutes if items else 0
    buffer_minutes = draft.buffer_minutes if items else 0

    def duration():
        return fixed + sum(i["estimated_seconds"] for i in items) / 60

    removed = []
    while duration() + buffer_minutes > time_budget:
        removable = next((i for i in reversed(items) if i["role"] != "priority"), None)
        if removable is None:
            raise ValueError("Time budget cannot fit preparation, priority work and rest")
        items.remove(removable)
        removed.append(
            {
                "exercise_id": removable["exercise"]["id"],
                "name": removable["exercise"]["displayNameZhHans"],
                "reason": "时间不足，优先保留重点动作和组间休息。",
            }
        )
    checks = [
        {
            "type": "priority",
            "explanation": "先安排本次重点动作；顺序原则关联 E2，具体方案是产品提案。",
        },
        {
            "type": "unknown",
            "explanation": (
                "尚无经审阅的握力、腰背稳定和技术疲劳标签，不能保证后续项目完全不受影响。"
            ),
        },
    ]
    for index, first in enumerate(items):
        a = first["exercise"]
        muscles_a = {a["targetMuscleCode"], *a["secondaryMuscleCodes"]}
        for second in items[index + 1 :]:
            b = second["exercise"]
            overlap = muscles_a & {b["targetMuscleCode"], *b["secondaryMuscleCodes"]}
            if overlap:
                checks.append(
                    {
                        "type": "catalog_muscle_overlap",
                        "first": a["id"],
                        "second": b["id"],
                        "muscles": sorted(overlap),
                        "explanation": (
                            "目录肌群有重叠，后续动作需按实际用力调整；这不是精确疲劳预测。"
                        ),
                    }
                )
    today = target_date or datetime.now(ZoneInfo(profile.time_zone)).date()
    confirmed = profile.confirmed_cycle
    if confirmed and not confirmed.starts_on <= today <= confirmed.ends_on:
        confirmed = None
    return {
        **planning_metadata(request),
        "outcome": plan_outcome(draft.adjustment, items),
        "id": str(uuid4()),
        "version": 1,
        "status": "proposed",
        "theme": draft.theme,
        "rationale": draft.rationale
        if not removed
        else (
            "依据本次目标和时间上限，最终保留"
            + "、".join(i["exercise"]["displayNameZhHans"] for i in items)
            + "；保留重点项目与休息，移除低优先级内容。"
            + draft.adjustment_reason
        ),
        "proposal_rationale": draft.rationale,
        "evidence_ids": draft.evidence_ids,
        "evidence": [evidence[key] for key in draft.evidence_ids],
        "load_evidence": [
            o
            for o in facts.get("observations", [])
            if o["id"] in {i["load"].get("observation_id") for i in items}
        ],
        "rules_version": RULES_VERSION,
        "catalog_version": catalog.version,
        "profile_version": (request.context.training_profile or {}).get("version", 0),
        "profile_snapshot": profile.model_dump(mode="json"),
        "confirmed_cycle": confirmed.model_dump(mode="json") if confirmed else None,
        "suggested_adjustment": draft.adjustment,
        "progression_basis": progression,
        "adjustment_reason": draft.adjustment_reason,
        "preparation": draft.preparation,
        "preparation_minutes": draft.preparation_minutes if items else 0,
        "finish_minutes": draft.finish_minutes if items else 0,
        "buffer_minutes": buffer_minutes,
        "items": items,
        "working_sets": sum(i["sets"] for i in items),
        "estimated_minutes_min": math.ceil(duration()),
        "estimated_minutes_max": math.ceil(duration() + buffer_minutes),
        "removed_items": removed,
        "ordering_checks": checks,
        "feedback_questions": [
            "实际完成哪些动作和组次？",
            "最后工作组的余力或主观用力如何？",
            "有无疼痛、提前结束或跳过项目，原因是什么？",
        ],
        "sources": SOURCES,
        "limitations": [
            "这是依据研究原则和产品规则生成的候选方案，具体剂量尚未经过专业个体审阅。",
            "当天再次确认状态；出现疼痛或明显不适时停止相关动作。",
        ],
    }


def render_review(review):
    facts = review["facts"]
    recent, baseline = facts["recent"], facts["baseline"]
    from fitness_bot.review_details import review_heading

    selection = facts.get("selection", {})
    if facts.get("scope") == "single":
        lines = review_heading(facts)
        lines.append("实际组间、动作间休息未记录，无法由录入间隔判断。")
    elif selection.get("mode") == "recent_workouts":
        count = selection["selected_count"]
        requested = selection["requested_count"]
        if count:
            dates = recent["training_dates"]
            scope = f"最近 {requested} 次已完成训练"
            if selection["insufficient_history"]:
                scope += f"（当前已同步记录仅有 {count} 次，全部纳入）"
            lines = [
                f"本次分析{scope}，记录日期 {dates[0]} 至 {dates[-1]}；"
                f"共 {recent['completed_sets']} 个已完成组。"
            ]
        else:
            lines = ["当前账号还没有已同步的已完成训练可供分析。"]
    else:
        lines = [
            f"本次复盘按 {facts['time_zone']} 的训练开始日统计，"
            f"截止日期 {recent['end_date_exclusive']}（不含当天）。",
            f"**记录事实**：最近 {recent['days']} 天完成 {recent['completed_workouts']} 次训练、"
            f"{recent['completed_sets']} 组；此前 {baseline['days']} 天完成 "
            f"{baseline['completed_workouts']} 次、{baseline['completed_sets']} 组。",
            f"按每周折算，训练频率为 {recent['workouts_per_week']} 次"
            f"与 {baseline['workouts_per_week']} 次。",
        ]
        lines.extend(review_heading(facts))
    cycle = facts.get("cycle", {})
    confirmed = cycle.get("confirmed_cycle")
    if confirmed:
        lines.append(
            f"用户确认周期：{confirmed['phase']} · "
            f"{confirmed['starts_on']} 至 {confirmed['ends_on']}"
            f"（与本次日期{'重叠' if cycle.get('overlaps_review_window') else '不重叠'}）。"
        )
    for label, window in (("最近窗口", recent), ("此前窗口", baseline)):
        execution = window.get("plan_execution", {})
        if execution.get("accepted_plans"):
            lines.append(
                f"**计划执行事实**：{label}有 {execution['accepted_plans']} 份已接受方案，"
                f"其中 {execution['linked_executions']} 份已关联实际训练，"
                f"{execution['skipped_plans']} 份记录为跳过。"
                "关联训练不代表全部规定组次已达标。"
            )
    for finding in review["findings"]:
        if finding.get("title"):
            lines.append("## " + finding["title"])
        lines.append(finding["conclusion"])
        # Legacy reports may carry these fields; render useful prose without field labels.
        for key in ("comparison", "possible_explanation", "suggested_action"):
            if finding.get(key):
                lines.append(finding[key])
    if review["keep"]:
        lines.append("可以继续保留：" + "；".join(review["keep"]))
    if review["next_adjustments"]:
        lines.append("接下来优先考虑：" + "；".join(review["next_adjustments"]))
    if review.get("omitted_findings"):
        lines.append("部分分析未能关联到有效记录依据，已省略；以上仅展示通过核对的内容。")
    if review["question"]:
        lines.append(review["question"])
    return "\n\n".join(lines)


def render_session(plan):
    is_current = plan.get("planning_target") == "current_workout"
    lines = [
        f"**{plan['theme']}**",
        plan["rationale"],
        "本次调整建议：" + plan["adjustment_reason"],
        "当前确认阶段："
        + (
            plan["confirmed_cycle"]["phase"]
            if plan["confirmed_cycle"]
            else "未知；本次调整不代表已进入某个周期。"
        ),
        f"准备约 {plan['preparation_minutes']} 分钟：{plan['preparation']}",
    ]
    if is_current:
        lines.insert(
            2,
            (
                f"本次训练已记录 {plan['existing_completed_sets']} 组完成、"
                f"{plan['existing_pending_sets']} 组待完成；原有记录完整保留。"
            ),
        )
        lines.append(f"新增安排可用 {plan['remaining_minutes']} 分钟，独立于原有待完成内容。")
    if plan["items"]:
        from fitness_bot.prescriptions import legacy_sets

        efforts = list(
            dict.fromkeys(
                f"保留约 {row['target_rir_min']}–{row['target_rir_max']} 次余力"
                for item in plan["items"]
                for row in legacy_sets(item)
            )
        )
        lines.append("用力目标：" + "；".join(efforts) + "。各组目标以卡片设置为准。")
        lines.append(
            "下方仅列新增动作。确认后由 App 校验最新快照并追加，原有动作和组保持不变。"
            if is_current
            else (
                "动作已按执行顺序排在下方。展开卡片可查看或调整每组设置，"
                "确认整份计划后保存供下次训练使用。"
            )
        )
    else:
        lines.append(
            "本次建议停止新增训练；已有记录保持不变，App 不自动结束训练。"
            if is_current and plan.get("outcome") == "rest"
            else "当前无需新增动作；已有完成和待完成记录保持不变。"
            if is_current
            else "本次建议休息，不创建待开始的训练。"
        )
    lines.append(
        ("新增部分" if is_current else "整份计划")
        + f"预计共 {plan['estimated_minutes_min']}–{plan['estimated_minutes_max']} 分钟，"
        "包含准备、执行、组间休息、切换、收尾与缓冲。"
    )
    if plan["removed_items"]:
        lines.append(
            "时间受限，已移除："
            + "、".join(i["name"] for i in plan["removed_items"])
            + "。未压缩保留动作的组间休息。"
        )
    lines.append("排序依据：" + " ".join(c["explanation"] for c in plan["ordering_checks"][:2]))
    lines.append("器械或时间变化时，请重新生成方案；替代动作需要重新试做选重。")
    lines.append("训练后反馈：" + "；".join(plan["feedback_questions"]))
    lines.append(
        "依据：[动作顺序综述](https://pubmed.ncbi.nlm.nih.gov/32077380/)；[减量专家共识](https://pubmed.ncbi.nlm.nih.gov/37730925/)。具体剂量是待评估的产品提案。"
    )
    return "\n\n".join(lines)
