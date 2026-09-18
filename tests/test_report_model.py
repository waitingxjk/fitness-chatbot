import copy
import json

import pytest
from test_presentation import facts, outline
from test_review_streaming import setup_model as base_setup_model

from fitness_bot.presentation import compile_outline


def setup_model(snapshots):
    model, fake, request = base_setup_model(snapshots)
    # These tests isolate frozen-outline semantics; raw provider closure is exercised
    # independently below with actual LangChain tool-call chunks.
    model._report_snapshots = model._decision_snapshots
    return model, fake, request


async def test_model_composes_with_allowed_chart_keys_and_separate_fact_data():
    model, fake, request = setup_model([outline()])
    request.context.training_review = facts()
    raw = await model.compose_review(request)
    assert raw == outline()
    properties = fake.schema["$defs"]
    assert properties["ChartIntent"]["properties"]["chart_key"]["enum"] == [
        "training-timeline",
        "session-sets",
        "body-parts",
    ]
    payload = json.loads(fake.calls[0][-1].content)
    assert "available_charts" in payload
    assert "分配" in fake.calls[0][0].content


async def test_generation_emits_closed_blocks_in_order_and_resolves_only_cited_facts():
    compiled = compile_outline(outline(), facts())
    blocks = [b for b in compiled["outline"] if b["type"] == "markdown"]
    fact_id = next(
        key for key, slot in compiled["facts"].items() if slot["source_key"] == "recent.workouts"
    )
    raw = [
        {"id": blocks[0]["id"], "content": f"共有 {{{{fact:{fact_id}}}}}。先看安排的连续性。"},
        {"id": blocks[1]["id"], "content": "## 留意部位分布\n\n结合当前训练目标检查重点。"},
    ]
    model, fake, request = setup_model(
        [
            {"blocks": [raw[0], {"id": raw[1]["id"], "content": "未完成"}]},
            {"blocks": raw},
        ]
    )
    request.context.training_review = facts()
    events = [e async for e in model.stream_composed_review(request, compiled)]
    assert [e["block"]["id"] for e in events if e["type"] == "block"] == [b["id"] for b in blocks]
    assert events[-1]["type"] == "complete"
    payload = json.loads(fake.calls[0][-1].content)
    assert payload["presentation"]["outline"] == compiled["outline"]
    assert payload["fact_slots"][fact_id]["value"] == "2 次训练"
    assert "不得" in fake.calls[0][0].content


@pytest.mark.parametrize("kind", ["wrong_id", "missing", "rewrite"])
async def test_generation_rejects_broken_frozen_contract(kind):
    compiled = compile_outline(outline(), facts())
    blocks = [b for b in compiled["outline"] if b["type"] == "markdown"]
    raw = [{"id": b["id"], "content": "结合记录观察。"} for b in blocks]
    first = {"blocks": [copy.deepcopy(raw[0]), {"id": raw[1]["id"]}]}
    if kind == "wrong_id":
        raw[1]["id"] = "invented"
    if kind == "missing":
        raw.pop()
    if kind == "rewrite":
        raw[0]["content"] = "改写前文。"
    if kind == "uncited_fact":
        raw[1]["content"] = "{{fact:recent.workouts}}"
    model, _, request = setup_model([first, {"blocks": raw}])
    request.context.training_review = facts()
    with pytest.raises(ValueError):
        _ = [e async for e in model.stream_composed_review(request, compiled)]


@pytest.mark.parametrize("failure", [None, "truncated_json", "no_finish", "length", "second_tool"])
async def test_actual_tool_stream_requires_closed_json_and_provider_completion(failure):
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessageChunk
    from langchain_core.outputs import ChatGenerationChunk

    compiled = compile_outline(outline(), facts())
    ids = [b["id"] for b in compiled["outline"] if b["type"] == "markdown"]
    raw = json.dumps(
        {"blocks": [{"id": key, "content": "先关注记录中的安排。"} for key in ids]},
        ensure_ascii=False,
    )
    if failure == "truncated_json":
        raw = raw[:-4]
    consumed = []
    fragments = [raw[i : i + 10] for i in range(0, len(raw), 10)]

    class ToolChat(GenericFakeChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

        def _stream(self, messages, stop=None, run_manager=None, **kwargs):
            for i, fragment in enumerate(fragments):
                consumed.append(fragment)
                yield ChatGenerationChunk(
                    message=AIMessageChunk(
                        content="",
                        tool_call_chunks=[
                            {
                                "index": 1 if failure != "second_tool" or i == 0 else 2,
                                "name": "GeneratedReport" if i == 0 else None,
                                "id": "tool-report" if i == 0 else None,
                                "args": fragment,
                            }
                        ],
                    )
                )
            if failure != "no_finish":
                yield ChatGenerationChunk(
                    message=AIMessageChunk(
                        content="",
                        response_metadata={
                            "finish_reason": "length" if failure == "length" else "tool_calls"
                        },
                    )
                )

    model, _, request = base_setup_model([])
    model.decision_llm = ToolChat(messages=iter([]))
    request.context.training_review = facts()
    events = []

    async def consume():
        async for event in model.stream_composed_review(request, compiled):
            events.append(event)
            if event["type"] == "block" and len(events) == 1:
                assert len(consumed) < len(fragments)

    if failure:
        with pytest.raises(ValueError):
            await consume()
        assert not any(e["type"] == "complete" for e in events)
    else:
        await consume()
        assert events[-1]["type"] == "complete"


async def test_composition_retries_invalid_outline_with_safe_feedback():
    model, fake, request = setup_model([outline()])
    request.context.training_review = facts()
    calls = []

    async def decision(request, schema, instruction, extra=None, **kwargs):
        calls.append(extra)
        return {"outline": []} if len(calls) == 1 else outline()

    model._decision = decision
    assert await model.compose_review(request) == outline()
    assert len(calls) == 2
    assert "validation_feedback" in calls[1]


async def test_block_repair_gets_only_its_own_slots_and_fixed_id():
    compiled = compile_outline(outline(), facts())
    expected = {**compiled["outline"][2], "facts": compiled["facts"]}
    raw = {"id": expected["id"], "content": "胸部 6 组。"}
    model, fake, request = setup_model([{"content": raw["content"]}])
    request.context.training_review = facts()
    result = await model.repair_review_block(request, expected, raw, "unbound_numbers")
    assert result == raw
    payload = json.loads(fake.calls[0][-1].content)
    assert payload["validation_feedback"] == "unbound_numbers"
    assert payload["fact_slots"]
    assert all(s["evidence_id"] in expected["evidence_ids"] for s in payload["fact_slots"].values())
    assert "recent.workouts" not in payload["fact_slots"]
    assert set(payload["fact_slots"]) == set(expected["fact_ids"])
    assert set(fake.schema["properties"]) == {"content"}
    assert "id" not in payload["draft_to_repair"]


async def test_repair_receives_concrete_failure_and_no_unrelated_history():
    from fitness_bot.schemas import Message

    compiled = compile_outline(outline(), facts())
    expected = compiled["outline"][0]
    model, fake, request = setup_model([{"content": "相邻训练之间的间隔较短。"}])
    request.context.training_review = facts()
    request.history = [
        Message(role="user", text="旧话题"),
        Message(role="assistant", text="旧结论"),
    ]
    raw = {"id": expected["id"], "content": "节奏接近隔一两天练一次。"}
    repaired = await model.repair_review_block(request, expected, raw, "unbound_numbers")
    assert repaired == {"id": expected["id"], "content": "相邻训练之间的间隔较短。"}
    messages = fake.calls[0]
    assert len(messages) == 2
    payload = json.loads(messages[-1].content)
    assert payload["invalid_fragments"] == ["节奏接近隔一两天练一次。"]
    assert set(payload["evidence"]) == set(expected["evidence_ids"])
    assert payload["context"]["training_review"] is None
    assert "旧结论" not in str(messages)
