"""Deliver a frozen outline in order, independently of HTML or Flutter rendering."""

import logging
from contextlib import aclosing
from copy import deepcopy

from fitness_bot.presentation import GeneratedBlock, validate_generated_block, validation_reason
from fitness_bot.training_decision import render_review, validate_review
from fitness_bot.training_schemas import ReviewDraft

logger = logging.getLogger(__name__)


async def deliver_review(model, request, presentation, writer):
    facts = request.context.training_review or {}
    review = validate_review(ReviewDraft(keep=[], next_adjustments=[]), facts)
    intro = render_review(review)
    writer({"type": "report_layout", **deepcopy(presentation)})
    layout = presentation["outline"]
    blocks, text, cursor, complete, omitted = [], "", 0, False, 0
    current = None
    repaired = 0

    def start():
        return {"id": layout[cursor]["id"], "type": "markdown", "content": "", "parts": []}

    def emit_delta(block, delta, parts=None):
        nonlocal text
        prefix = "\n\n" if text and not block["content"] else ""
        block["content"] += delta
        parts = parts if parts is not None else [{"type": "text", "text": delta}]
        block["parts"].extend(deepcopy(parts))
        writer({"type": "block_delta", "block_id": block["id"], "delta": delta, "parts": parts})
        text += prefix + delta
        writer({"type": "text_delta", "delta": prefix + delta})

    current = start()
    writer({"type": "block_start", "block": deepcopy(current)})
    emit_delta(current, intro)
    async with aclosing(model.stream_composed_review(request, presentation)) as events:
        async for event in events:
            if complete:
                raise ValueError("Report event after completion")
            if event["type"] == "complete":
                complete = True
                continue
            if event["type"] != "block" or cursor >= len(layout):
                raise ValueError("Unexpected generated report event")
            expected = layout[cursor]
            raw = GeneratedBlock.model_validate(event["block"])
            if raw.id != expected["id"] or expected["type"] != "markdown":
                raise ValueError("Generated block does not match outline")
            block = None
            try:
                block, finding = validate_generated_block(
                    raw, expected, facts, presentation.get("facts")
                )
            except ValueError as exc:
                reason = validation_reason(exc)
                logger.warning(
                    "Report block rejected request_id=%s reason=%s", request.request_id, reason
                )
                if hasattr(model, "repair_review_block"):
                    try:
                        fixed = await model.repair_review_block(
                            request,
                            {**expected, "facts": presentation.get("facts")},
                            raw.model_dump(),
                            reason,
                        )
                        block, finding = validate_generated_block(
                            fixed, expected, facts, presentation.get("facts")
                        )
                        repaired += 1
                    except ValueError as repair_exc:
                        logger.warning(
                            "Report repair rejected request_id=%s reason=%s",
                            request.request_id,
                            validation_reason(repair_exc),
                        )
            if block is None:
                omitted += 1
                block = {"content": "这部分分析暂未生成，已保留可核对的训练数据。"}
            else:
                review["findings"].append(finding)
            separator = "\n\n" if current["content"] else ""
            parts = ([{"type": "text", "text": separator}] if separator else []) + block.get(
                "parts", [{"type": "text", "text": block["content"]}]
            )
            emit_delta(current, separator + block["content"], parts)
            writer({"type": "block_end", "block_id": current["id"]})
            blocks.append(deepcopy(current))
            cursor += 1
            while cursor < len(layout) and layout[cursor]["type"] == "chart_ref":
                reference = deepcopy(layout[cursor])
                blocks.append(reference)
                writer({"type": "block", "block": deepcopy(reference)})
                cursor += 1
            if cursor < len(layout):
                current = start()
                writer({"type": "block_start", "block": deepcopy(current)})
    if not complete or cursor != len(layout):
        raise ValueError("Report stream ended before the frozen outline was complete")
    if not review["findings"]:
        raise ValueError("Report contains no validated analysis")
    review["omitted_findings"] = omitted
    review["repaired_findings"] = repaired
    expected_text = "\n\n".join(b["content"] for b in blocks if b["type"] == "markdown")
    if text != expected_text:
        raise ValueError("Compatibility text differs from content blocks")
    return review, text, {**presentation, "blocks": blocks}


class PartialReport:
    """Accumulate only emitted report events for failure/cancellation history."""

    def __init__(self):
        self.report = None

    def record(self, event):
        kind = event["type"]
        if kind == "report_layout":
            self.report = {
                key: deepcopy(event[key])
                for key in ("schema_version", "outline", "artifacts", "datasets")
            }
            self.report["blocks"] = []
            self.report["facts"] = deepcopy(event.get("facts", {}))
        elif self.report is not None:
            if kind in {"block_start", "block"}:
                self.report["blocks"].append(deepcopy(event["block"]))
            elif kind == "block_delta":
                for block in self.report["blocks"]:
                    if block["id"] == event["block_id"] and block["type"] == "markdown":
                        block["content"] += event["delta"]
                        if "parts" in block:
                            block["parts"].extend(
                                deepcopy(
                                    event.get("parts", [{"type": "text", "text": event["delta"]}])
                                )
                            )
                        break

    def text(self, legacy_text):
        if self.report is None:
            return legacy_text
        return "\n\n".join(
            block["content"]
            for block in self.report["blocks"]
            if block["type"] == "markdown" and block["content"]
        )

    def response(self, text, conversation_id):
        if self.report is None:
            return None
        return {
            **deepcopy(self.report),
            "kind": "review",
            "text": self.text(text),
            "cards": [],
            "conversation_id": str(conversation_id),
            "incomplete": True,
        }
