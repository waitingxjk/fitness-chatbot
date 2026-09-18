"""Portable report planning and compilation. Models never supply chart measurements or IDs."""

import re
from copy import deepcopy
from secrets import token_hex
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from fitness_bot.review_presentation import (
    FACT_PATTERN,
    bind_selected_count,
    fact_slots,
    review_charts,
)
from fitness_bot.training_decision import evidence_index


class TextIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["text"]
    purpose: str = Field(min_length=1, max_length=400)
    evidence_ids: list[str] = Field(min_length=1, max_length=8)


class ChartIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["chart"]
    chart_key: str = Field(min_length=1, max_length=80)


class OutlineDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outline: list[Annotated[TextIntent | ChartIntent, Field(discriminator="type")]] = Field(
        min_length=1, max_length=7
    )


class GeneratedBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=80)
    content: str = Field(min_length=1, max_length=1600)


class RepairedText(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=1600)


class GeneratedReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    blocks: list[GeneratedBlock] = Field(min_length=1, max_length=4)


def outline_schema(facts):
    schema = OutlineDraft.model_json_schema()
    schema["$defs"]["TextIntent"]["properties"]["evidence_ids"]["items"]["enum"] = list(
        evidence_index(facts)
    )
    available = [c["id"] for c in review_charts(facts)]
    # The sentinel is rejected by the compiler; no-data outlines must use text only.
    schema["$defs"]["ChartIntent"]["properties"]["chart_key"]["enum"] = available or ["unavailable"]
    return schema


def generation_schema(compiled):
    schema = GeneratedReport.model_json_schema()
    ids = [b["id"] for b in compiled["outline"] if b["type"] == "markdown"]
    schema["$defs"]["GeneratedBlock"]["properties"]["id"]["enum"] = ids
    schema["properties"]["blocks"].update(minItems=len(ids), maxItems=len(ids))
    return schema


def validation_reason(exc):
    """Stable diagnostics without provider input, user content or raw validation errors."""
    if isinstance(exc, ValidationError):
        return "invalid_schema"
    return {
        "Unknown outline evidence": "unknown_evidence",
        "Unavailable or duplicate chart": "invalid_chart",
        "Too many merged evidence references": "merged_evidence_limit",
        "Review prose must leave numbers and effective-set claims to server facts": (
            "unbound_numbers"
        ),
        "Inline fact must reference a cited server fact": "uncited_fact",
        "Malformed inline fact reference": "malformed_fact",
        "Inline facts are only supported in finding conclusions": "unparsed_fact",
        "Repair changed frozen block ID": "repair_changed_id",
        "Model did not return a valid training decision": "missing_structured_output",
        "Incomplete synchronization cannot establish a return or stop in training": (
            "unverified_training_status"
        ),
    }.get(str(exc), "invalid_report_contract")


def fallback_outline(facts):
    evidence = evidence_index(facts)
    topics = {
        "training-timeline": ("核心判断与记录节奏", ["recent.frequency", "cadence.spacing"]),
        "session-sets": ("已完成组数的分布与变化", ["recent.frequency", "cadence.spacing"]),
        "body-parts": ("部位覆盖和优先关注方向", ["recent.distribution"]),
    }
    items = []
    for chart in review_charts(facts):
        purpose, keys = topics[chart["id"]]
        if facts.get("selection", {}).get("baseline_included"):
            keys = [*keys, "baseline.frequency", "baseline.distribution"]
        items.extend(
            [
                {
                    "type": "text",
                    "purpose": purpose,
                    "evidence_ids": [key for key in keys if key in evidence],
                },
                {"type": "chart", "chart_key": chart["id"]},
            ]
        )
    return {
        "outline": items
        or [
            {
                "type": "text",
                "purpose": "结合已有记录给出核心判断；无记录则说明范围。",
                "evidence_ids": list(evidence)[:8],
            }
        ]
    }


def compile_outline(raw, facts):
    draft = raw if isinstance(raw, OutlineDraft) else OutlineDraft.model_validate(raw)
    if not isinstance(draft.outline[0], TextIntent):
        raise ValueError("A report must start with text")
    if sum(isinstance(item, TextIntent) for item in draft.outline) > 4:
        raise ValueError("Too many text sections")
    evidence = evidence_index(facts)
    charts = {c["id"]: c for c in review_charts(facts)}
    allocated = set()

    def allocate(prefix):
        # Check even across resource types. IDs are scoped to this report, not global keys.
        for _ in range(100):
            suffix = token_hex(4)
            if suffix not in allocated:
                allocated.add(suffix)
                return f"{prefix}_{suffix}"
        raise ValueError("Unable to allocate unique report ID")

    result = {"schema_version": 1, "outline": [], "artifacts": {}, "datasets": {}, "facts": {}}
    seen = set()
    for item in draft.outline:
        if isinstance(item, TextIntent):
            if set(item.evidence_ids) - evidence.keys():
                raise ValueError("Unknown outline evidence")
            if result["outline"] and result["outline"][-1]["type"] == "markdown":
                block = result["outline"][-1]
                block["purposes"].append(item.purpose)
                block["evidence_ids"] = list(
                    dict.fromkeys([*block["evidence_ids"], *item.evidence_ids])
                )
                if len(block["evidence_ids"]) > 8:
                    raise ValueError("Too many merged evidence references")
            else:
                result["outline"].append(
                    {
                        "id": allocate("block"),
                        "type": "markdown",
                        "purposes": [item.purpose],
                        "evidence_ids": list(dict.fromkeys(item.evidence_ids)),
                    }
                )
        else:
            if item.chart_key not in charts or item.chart_key in seen:
                raise ValueError("Unavailable or duplicate chart")
            seen.add(item.chart_key)
            chart = charts[item.chart_key]
            artifact_id, dataset_id = allocate("chart"), allocate("data")
            result["outline"].append(
                {"id": allocate("block"), "type": "chart_ref", "artifact_id": artifact_id}
            )
            result["artifacts"][artifact_id] = {
                "format": "chart-spec/v1",
                "chart_type": chart["kind"],
                "title": chart["title"],
                "description": chart["description"],
                "unit": chart["unit"],
                "dataset_id": dataset_id,
                "encoding": {"label": "label", "value": "value", "at": "at"},
                "truncated": chart["truncated"],
            }
            result["datasets"][dataset_id] = {"rows": deepcopy(chart["points"])}
    cited = {key for b in result["outline"] for key in b.get("evidence_ids", [])}
    for key, slot in fact_slots(facts).items():
        if slot["evidence_id"] in cited:
            result["facts"][allocate("fact")] = {"source_key": key, **deepcopy(slot)}
    for block in result["outline"]:
        if block["type"] == "markdown":
            block["fact_ids"] = [
                key
                for key, slot in result["facts"].items()
                if slot["evidence_id"] in block["evidence_ids"]
            ]
    return result


def validate_generated_block(raw, expected, facts, registry=None):
    from fitness_bot.training_decision import validate_review
    from fitness_bot.training_schemas import ReviewDraft, ReviewFinding

    block = raw if isinstance(raw, GeneratedBlock) else GeneratedBlock.model_validate(raw)
    if block.id != expected["id"]:
        raise ValueError("Generated block order or ID differs from the frozen outline")
    if re.search(r"!\s*\[|<\s*/?[A-Za-z][^>]*>", block.content):
        raise ValueError("Report media must use artifact references, not images or HTML")
    content = block.content
    if registry is not None:

        def resolve_id(match):
            key = match[1]
            if key not in registry or key not in expected.get("fact_ids", []):
                raise ValueError("Inline fact must reference a cited server fact")
            return "{{fact:" + registry[key]["source_key"] + "}}"

        content = FACT_PATTERN.sub(resolve_id, content)
    content = bind_selected_count(content, facts, expected["evidence_ids"])
    finding = ReviewFinding(
        conclusion=content,
        evidence_ids=expected["evidence_ids"],
    )
    review = validate_review(ReviewDraft(findings=[finding], keep=[], next_adjustments=[]), facts)
    result = {
        "id": block.id,
        "type": "markdown",
        "content": review["findings"][0]["conclusion"],
    }
    if registry is not None:
        ids = {slot["source_key"]: key for key, slot in registry.items()}
        parts, cursor = [], 0
        for match in FACT_PATTERN.finditer(content):
            if match.start() > cursor:
                parts.append({"type": "text", "text": content[cursor : match.start()]})
            parts.append({"type": "fact_ref", "fact_id": ids[match[1]]})
            cursor = match.end()
        if cursor < len(content):
            parts.append({"type": "text", "text": content[cursor:]})
        result["parts"] = parts
    return result, review["findings"][0]
