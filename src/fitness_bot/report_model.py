"""Two model boundaries: plan a report, then fill its frozen text blocks."""

import json
import logging
import re
from contextlib import aclosing

from langchain_core.utils.json import parse_partial_json

from fitness_bot.presentation import (
    GeneratedBlock,
    GeneratedReport,
    RepairedText,
    compile_outline,
    generation_schema,
    outline_schema,
    validation_reason,
)
from fitness_bot.review_presentation import FACT_PATTERN, fact_slots, review_charts
from fitness_bot.training_decision import evidence_index

logger = logging.getLogger(__name__)

COMPOSE_INSTRUCTION = """先分析当前记录，再编排最终复盘的结构；
本次仅输出有序 outline，不写最终正文。
text 项说明该段需要回答什么、主要发现与调整方向，并引用允许的 evidence_ids。
先给核心判断，随后围绕有价值的发现组织内容，不凑固定章节，不逐条重复数据限制。
chart 项只能选择 available_charts 中的 chart_key，在相关分析后安排图表；不需要的图表不必使用。
同一图表最多出现一次，首项必须是 text，文字项最多四项；无数据只安排文字。
每个文字项最多引用八个证据，相邻文字项合并后的不同证据总数也不能超过八个。
每个主题的 text 后紧接对应 chart，不把全部图表堆到结尾。purpose 只说明分析任务，不写具体数值。
相邻文字项会合并为一个展示块。图表数据和唯一 ID 由程序分配，不输出数据值、URL、HTML 或自定义代码。
复盘只解释记录与调整方向，具体训练处方属于推荐能力。
"""
GENERATE_INSTRUCTION = """按照 presentation.outline 的顺序填充全部 markdown 块，输出 blocks 数组。
每个元素只能有 id 和 content，id 必须逐个使用给定的文字块 ID；不得跳过、重复、改写或改变顺序。
chart_ref 由程序插入，绝不写入正文，也不输出图表 ID、占位标记、图片链接或 HTML。
每块覆盖其 purposes，使用自然段、有信息的小标题、重点列表和适量加粗；避免机械的审计字段标签。
第一块先给核心判断，不重复程序已显示的统计范围。后续围绕相关图表讲清发现和关注方向。
只有该块 evidence_ids 对应的事实可作为依据。准确数字只通过 fact_slots 中的 {{fact:ID}}
引用，ID 格式是 fact_ 加八位小写十六进制字符，必须逐字复制该块 fact_ids 中的已有 ID。
例如给定 fact_a3f29c01 时写“记录包含 {{fact:fact_a3f29c01}}”，不要直接填数字。
source_key 只解释事实含义，绝不能用作引用；不得自行生成、缩短、改写 ID 或重算数字。
即使 purposes、图表、上下文或历史回复中已经出现数字，也不能复制到 content。
中文数量词也算数值：“两次”“三组”“七天”同样不能直接写，改为“这些训练”“这段记录”等。
没有可用槽位的数值改用定性描述，不要自造槽位；列表用无序列表，不写数字序号。
槽位 value 是完整短语（含部位或单位），嵌入后不要重复写“次训练”“组”或部位名称。
recent.sets 是所选训练的累计组数，不是每次训练的组数；不要把总量槽位套到单次训练。
事实值由程序替换。不要输出未记录的趋势或进步；只在影响判断时说明不确定性，不堆通用免责声明。
保留复盘职责边界，不在正文开具体未来组次负重处方。不要重复其它块内容。
"""


class ReviewCompositionModel:
    async def compose_review(self, request):
        facts = request.context.training_review or {}
        extra = {
            "available_charts": [
                {"chart_key": c["id"], "title": c["title"], "description": c["description"]}
                for c in review_charts(facts)
            ]
        }
        for attempt in range(2):
            try:
                raw = await self._decision(
                    request,
                    outline_schema(facts),
                    COMPOSE_INSTRUCTION,
                    extra,
                    skill="training-review",
                )
                compile_outline(raw, facts)
                return raw
            except ValueError as exc:
                logger.warning(
                    "Report outline rejected request_id=%s attempt=%s reason=%s",
                    request.request_id,
                    attempt + 1,
                    validation_reason(exc),
                )
                if attempt:
                    raise
                extra["validation_feedback"] = validation_reason(exc)
        raise AssertionError("Unreachable")

    async def repair_review_block(self, request, expected, raw, reason):
        facts = request.context.training_review or {}
        slots = {
            key: value
            for key, value in (expected.get("facts") or fact_slots(facts)).items()
            if value["evidence_id"] in expected["evidence_ids"]
        }
        # Repair is a local edit, not another conversation/review turn. Do not let
        # unrelated history or uncited source records compete with the exact feedback.
        repair_request = request.model_copy(
            update={
                "message": "修正这一段中明确标出的校验问题，保留有依据的分析。",
                "history": [],
                "context": type(request.context)(),
            }
        )
        fragments = [
            part
            for part in re.split(r"(?<=[。！？；\n])", raw["content"])
            if re.search(
                r"\d|[二两三四五六七八九十百]+\s*(组|次|天|周)|有效组|恢复训练|开始停训",
                FACT_PATTERN.sub("", part),
            )
        ]
        evidence = evidence_index(facts)
        result = await self._decision(
            repair_request,
            RepairedText.model_json_schema(),
            """你现在只编辑给定草稿，输出仅有 content 字段的对象，不输出 blocks 或 ID。
invalid_fragments 是实际触发校验的句子，必须逐句纠正，不能原样返回或换一种数值说法。
直接写出的数字、中文数量词和范围都不允许；如“隔一两天”“两到三天”均改成“相邻训练间隔较短”。
需要保留的准确数值只能引用所提供 fact_slots 的 {{fact:ID}}；完整复制 fact_ 加八位短 ID。
source_key 只解释含义，不能用于引用。不造新 ID。
如果没有对应槽位，改用有依据的定性表述，不换算、不估算、不复述旧数值。
槽位 value 包含单位和可能的部位名称，嵌入后不重复这些词；累计数据不能写成单次数据。
只能依据 evidence 中的记录，不能新增事实或训练处方。保留原有主题与有效分析。
不把记录空档说成停训或恢复训练；未知时说“只能描述可见记录的节奏”。
不把已完成组称为有效组。不写 HTML、图片或图表标记，列表用无序列表。""",
            {
                "purposes": expected["purposes"],
                "evidence": {key: evidence[key] for key in expected["evidence_ids"]},
                "fact_slots": slots,
                "validation_feedback": reason,
                "invalid_fragments": fragments,
                "draft_to_repair": {"content": raw["content"]},
            },
            skill="training-review",
        )
        repair = RepairedText.model_validate(result)
        return {"id": expected["id"], "content": repair.content}

    async def _report_snapshots(self, request, schema, instruction, extra, *, skill):
        # A partial JSON parser repairs missing closing quotes/braces. Keep raw arguments
        # and a provider terminal signal so a repaired last block cannot become success.
        bound = self.decision_llm.bind_tools(
            [schema], tool_choice=schema["title"], parallel_tool_calls=False
        )
        raw, name, finished = "", None, False
        tool_index, tool_id = None, None
        async with aclosing(
            bound.astream(await self._decision_messages(request, instruction, extra, skill))
        ) as chunks:
            async for chunk in chunks:
                metadata = chunk.response_metadata
                if metadata.get("finish_reason") in {"length", "content_filter"}:
                    raise ValueError("Report provider did not complete generation")
                if metadata.get("status") in {"incomplete", "failed", "cancelled"}:
                    raise ValueError("Report provider did not complete generation")
                finished = finished or metadata.get("finish_reason") in {"stop", "tool_calls"}
                finished = finished or metadata.get("status") == "completed"
                for call in chunk.tool_call_chunks:
                    index = call.get("index")
                    if not isinstance(index, int) or index < 0:
                        raise ValueError("Report tool index is missing")
                    if tool_index is None:
                        tool_index = index
                    if index != tool_index:
                        raise ValueError("Report requires exactly one tool call")
                    if call.get("id"):
                        if tool_id is not None and tool_id != call["id"]:
                            raise ValueError("Report tool ID changed")
                        tool_id = call["id"]
                    if call.get("name"):
                        name = call["name"]
                    if name != schema["title"]:
                        raise ValueError("Unexpected report tool name")
                    raw += call.get("args") or ""
                    if len(raw) > 100_000:
                        raise ValueError("Report tool arguments exceed limit")
                    try:
                        partial = parse_partial_json(raw)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(partial, dict):
                        yield partial
        if not finished:
            raise ValueError("Report stream ended without provider completion")
        # Unlike parse_partial_json this does not repair truncated strings or objects.
        final = json.loads(raw)
        if not isinstance(final, dict):
            raise ValueError("Report arguments must be a JSON object")
        yield final

    async def stream_composed_review(self, request, presentation):
        expected = [b for b in presentation["outline"] if b["type"] == "markdown"]
        emitted, last = [], None
        async with aclosing(
            self._report_snapshots(
                request,
                generation_schema(presentation),
                GENERATE_INSTRUCTION,
                {"presentation": presentation, "fact_slots": presentation.get("facts", {})},
                skill="training-review",
            )
        ) as snapshots:
            async for snapshot in snapshots:
                last = snapshot
                values = snapshot.get("blocks", [])
                if not isinstance(values, list):
                    continue
                # Only a following array item proves the previous object has closed.
                for raw in values[len(emitted) : -1]:
                    if len(emitted) >= len(expected):
                        raise ValueError("Too many generated blocks")
                    block = GeneratedBlock.model_validate(raw)
                    if block.id != expected[len(emitted)]["id"]:
                        raise ValueError("Generated block ID or order changed")
                    emitted.append(block)
                    yield {"type": "block", "block": block.model_dump()}
        if last is None:
            raise ValueError("Report generation produced no content")
        final = GeneratedReport.model_validate(last)
        if len(final.blocks) != len(expected) or final.blocks[: len(emitted)] != emitted:
            raise ValueError("Generated report changed or omitted frozen blocks")
        for block in final.blocks[len(emitted) :]:
            if block.id != expected[len(emitted)]["id"]:
                raise ValueError("Generated block ID or order changed")
            emitted.append(block)
            yield {"type": "block", "block": block.model_dump()}
        yield {"type": "complete"}
