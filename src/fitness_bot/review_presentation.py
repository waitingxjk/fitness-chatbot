"""Source-bound inline facts and deterministic chart data for readable reviews."""

import math
import re
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

# Catalog codes such as "upper legs" are valid keys. Trust comes from exact
# lookup plus cited evidence, not from restricting identifier characters.
FACT_PATTERN = re.compile(r"\{\{fact:([^{}\r\n]+)\}\}")


def _number(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def _format(value):
    return f"{value:g}" if isinstance(value, (int, float)) else str(value)


def fact_slots(facts):
    slots = {}
    for period in ("recent", "baseline"):
        if period == "baseline" and facts.get("selection", {}).get("baseline_included") is False:
            continue
        window = facts.get(period, {})
        for key, field, suffix in (
            ("workouts", "completed_workouts", "次训练"),
            ("sets", "completed_sets", "个已完成组"),
        ):
            value = window.get(field)
            if _number(value):
                slots[f"{period}.{key}"] = {
                    "value": f"{_format(value)} {suffix}",
                    "evidence_id": f"{period}.frequency",
                }
        dates = window.get("training_dates", [])
        if dates:
            slots[f"{period}.dates"] = {
                "value": f"{dates[0]} 至 {dates[-1]}",
                "evidence_id": f"{period}.frequency",
            }
        for code, count in window.get("primary_body_part_sets", {}).items():
            if _number(count):
                label = (
                    "待归类动作"
                    if code == "unknown"
                    else facts.get("body_part_labels", {}).get(code, code)
                )
                slots[f"{period}.part.{code}"] = {
                    "value": f"{label} {_format(count)} 个已完成组",
                    "evidence_id": f"{period}.distribution",
                }
    for item in facts.get("observations", [])[:60]:
        key = item["id"]
        values = {"date": item.get("performed_at", "")[:10], "name": item.get("exercise_name", "")}
        count = item.get("completed_sets")
        total = (
            item.get("duration_seconds")
            if item.get("detail_type") == "duration"
            else item.get("repetitions")
        )
        unit = "秒" if item.get("detail_type") == "duration" else "次"
        if _number(count) and _number(total):
            values["dose"] = f"{_format(count)} 组，合计 {_format(total)} {unit}"
        if (
            item.get("detail_type") != "duration"
            and _number(item.get("load_value"))
            and item.get("load_unit")
        ):
            values["load"] = f"{_format(item['load_value'])} {item['load_unit']}"
        for name, value in values.items():
            if value:
                slots[f"{key}.{name}"] = {"value": value, "evidence_id": key}
    sessions = facts.get("cadence", {}).get("sessions", [])
    if sessions and not facts.get("cadence", {}).get("truncated"):
        slots["cadence.workouts"] = {
            "value": f"{len(sessions)} 次训练",
            "evidence_id": "cadence.spacing",
        }
    gaps = [
        i["start_gap_hours"]
        for i in facts.get("cadence", {}).get("intervals", [])
        if _number(i.get("start_gap_hours"))
    ]
    if gaps:
        slots["cadence.gaps"] = {
            "value": f"{min(gaps):g}–{max(gaps):g} 小时",
            "evidence_id": "cadence.spacing",
        }
    return slots


def bind_selected_count(text, facts, evidence_ids):
    """Bind only deictic references to the exact selected sample, never frequency/dose."""
    if facts.get("selection", {}).get("mode") != "recent_workouts":
        return text
    count = facts.get("recent", {}).get("completed_workouts")
    if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 99:
        return text
    slots = fact_slots(facts)
    key = next(
        (
            key
            for key in ("recent.workouts", "cadence.workouts")
            if key in slots
            and slots[key]["evidence_id"] in evidence_ids
            and slots[key]["value"] == f"{count} 次训练"
        ),
        None,
    )
    if key is None:
        return text
    digits = "零一二三四五六七八九"
    chinese = (
        digits[count]
        if count < 10
        else (
            (digits[count // 10] if count >= 20 else "")
            + "十"
            + (digits[count % 10] if count % 10 else "")
        )
    )
    forms = {str(count), chinese, *(["两"] if count == 2 else [])}
    pattern = (
        r"(这|本次分析的|所选的|可见的)\s*([0-9零一二两三四五六七八九十百]+)\s*次(?:已完成)?训练"
    )
    return re.sub(pattern, lambda m: m[1] + "{{fact:" + key + "}}" if m[2] in forms else m[0], text)


def resolve_fact_text(text, facts, evidence_ids):
    slots = fact_slots(facts)

    def replace(match):
        slot = slots.get(match[1])
        if slot is None or slot["evidence_id"] not in evidence_ids:
            raise ValueError("Inline fact must reference a cited server fact")
        return slot["value"]

    resolved = FACT_PATTERN.sub(replace, text)
    if "{{fact:" in resolved:
        raise ValueError("Malformed inline fact reference")
    return resolved


def review_charts(facts):
    cadence = facts.get("cadence", {})
    zone = ZoneInfo(facts.get("time_zone", "UTC"))
    sessions = cadence.get("sessions", [])
    charts = []
    points = []
    for item in sessions:
        at = datetime.fromisoformat(item["started_at"])
        points.append(
            {
                "label": at.astimezone(zone).strftime("%m-%d"),
                "at": item["started_at"],
                "value": 1,
                "source": item["source"],
            }
        )
    truncated = cadence.get("truncated", False)
    if len(points) >= 2:
        charts.append(
            {
                "id": "training-timeline",
                "kind": "timeline",
                "title": "训练节奏",
                "description": f"按实际日期间隔排列 · {facts.get('time_zone', 'UTC')}",
                "unit": "次",
                "points": points,
                "truncated": truncated,
            }
        )
    set_points = [
        {**point, "value": session["completed_sets"]}
        for point, session in zip(points, sessions, strict=True)
        if _number(session.get("completed_sets"))
    ]
    if set_points:
        charts.append(
            {
                "id": "session-sets",
                "kind": "columns",
                "title": "每次训练完成组数",
                "description": "按记录中的完成状态计数，不等同于有效组",
                "unit": "组",
                "points": set_points,
                "truncated": truncated,
            }
        )
    counts = Counter(facts.get("recent", {}).get("primary_body_part_sets", {}))
    if facts.get("selection", {}).get("baseline_included"):
        counts.update(facts.get("baseline", {}).get("primary_body_part_sets", {}))
    labels = facts.get("body_part_labels", {})
    parts = [
        {"label": "待归类动作" if code == "unknown" else labels.get(code, code), "value": count}
        for code, count in counts.most_common()
        if _number(count) and count > 0
    ]
    if parts:
        total = sum(p["value"] for p in parts)
        unknown = counts.get("unknown", 0)
        coverage = (
            f"已匹配动作目录 {_format(total - unknown)}/{_format(total)} 组；"
            f"待归类 {_format(unknown)} 组，不能代表完整部位分布。"
            if unknown
            else ""
        )
        charts.append(
            {
                "id": "body-parts",
                "kind": "bars",
                "title": "训练部位分布",
                "description": (
                    ("含对照期；" if facts.get("selection", {}).get("baseline_included") else "")
                    + coverage
                    + "按目录主部位统计已完成组；辅助肌群不重复计数"
                ),
                "unit": "组",
                "points": parts,
                "truncated": False,
            }
        )
    return charts
