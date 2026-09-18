"""Constrain review references and keep independently validated streamed sections."""

from pydantic import ValidationError

from fitness_bot.training_decision import evidence_index, validate_review
from fitness_bot.training_schemas import ReviewDraft, ReviewFinding

DEFAULT_KEEP = ["保留真实训练记录"]
DEFAULT_NEXT = ["结合训练感受进一步核对"]


def review_schema(facts):
    schema = ReviewDraft.model_json_schema()
    finding = schema["$defs"]["ReviewFinding"]
    for key in ("comparison", "possible_explanation", "suggested_action", "missing_information"):
        finding["properties"].pop(key, None)
    finding["required"] = ["conclusion", "evidence_ids"]
    schema["$defs"]["ReviewFinding"]["properties"]["evidence_ids"]["items"]["enum"] = list(
        evidence_index(facts)
    )
    return schema


def valid_finding(raw, facts):
    try:
        finding = ReviewFinding.model_validate(raw)
        validate_review(
            ReviewDraft(findings=[finding], keep=DEFAULT_KEEP, next_adjustments=DEFAULT_NEXT), facts
        )
    except (ValueError, ValidationError):
        return None
    return finding


def recover_review(raw, facts):
    # Require a complete response shape; a disconnected/truncated stream is an error.
    draft = ReviewDraft.model_validate(raw)
    findings = [valid for f in draft.findings if (valid := valid_finding(f, facts))]
    omitted = len(draft.findings) - len(findings)
    tail = draft.model_copy(update={"findings": []})
    try:
        validate_review(tail, facts)
    except ValueError:
        tail = ReviewDraft(keep=DEFAULT_KEEP, next_adjustments=DEFAULT_NEXT)
    result = tail.model_copy(update={"findings": findings})
    validate_review(result, facts)
    return result, omitted


def closed_findings(snapshot):
    """A subsequent array item or root key proves the preceding JSON object closed.

    Structured-output parsers may temporarily close an incomplete trailing object.
    Do not publish that object until a following item/key arrives or the stream ends.
    """
    values = snapshot.get("findings", [])
    if not isinstance(values, list):
        return []
    keys = list(snapshot)
    has_later_key = "findings" in keys and keys.index("findings") < len(keys) - 1
    return values if has_later_key else values[:-1]


def qualitative_sections(snapshot):
    """Only closed, dose-free theme/rationale strings may precede plan validation."""
    import re

    keys = list(snapshot)
    result = {}
    for key in ("theme", "rationale"):
        text = snapshot.get(key)
        if (
            key not in keys[:-1]
            or not isinstance(text, str)
            or not text.strip()
            or len(text) > 1000
        ):
            continue
        if re.search(
            r"(?i)\d|kg|lb|公斤|千克|磅|%|％|[一二两三四五六七八九十百]+\s*(组|次|分钟|秒)", text
        ):
            continue
        result[key] = text
    return result
