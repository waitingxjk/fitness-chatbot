import copy

import pytest

from fitness_bot.config import Settings
from fitness_bot.model import OpenAICompanionModel
from fitness_bot.schemas import ChatRequest


def finding(key="recent.frequency"):
    return dict(
        conclusion="记录中有重复安排",
        evidence_ids=[key],
        comparison="对照所选训练",
        possible_explanation="可能与当时安排有关",
        suggested_action="结合反馈再判断",
        missing_information=[],
    )


class SnapshotModel:
    def __init__(self, snapshots):
        self.snapshots = snapshots
        self.calls = []

    def with_structured_output(self, schema, **kwargs):
        self.schema = schema
        return self

    async def astream(self, messages):
        self.calls.append(messages)
        for snapshot in self.snapshots:
            yield copy.deepcopy(snapshot)

    async def ainvoke(self, messages):
        self.calls.append(messages)
        return copy.deepcopy(self.snapshots[-1])


def setup_model(snapshots):
    model = OpenAICompanionModel(Settings(_env_file=None, openai_api_key="test-key"))
    fake = SnapshotModel(snapshots)
    model.decision_llm = fake
    request = ChatRequest(
        message="分析历史",
        context={
            "training_review": {
                "recent": {},
                "baseline": {},
                "observations": [],
                "selection": {"baseline_included": False},
            }
        },
    )
    return model, fake, request


async def test_review_schema_only_allows_current_evidence_and_recovers_invalid_reference():
    draft = dict(
        findings=[finding("baseline.frequency"), finding()],
        keep=["保留记录"],
        next_adjustments=["结合反馈"],
    )
    model, fake, request = setup_model([draft])
    result = await model.review(request)
    assert isinstance(fake.schema, dict)
    assert fake.schema["$defs"]["ReviewFinding"]["properties"]["evidence_ids"]["items"]["enum"] == [
        "recent.frequency",
        "recent.distribution",
    ]
    assert len(result.findings) == 1
    assert result.findings[0].evidence_ids == ["recent.frequency"]


async def test_review_stream_emits_only_closed_valid_findings_and_keeps_them_once():
    valid = finding()
    invalid = finding("invented")
    snapshots = [
        {"findings": [{"conclusion": "未完"}]},
        {"findings": [invalid, {"conclusion": "下一条未完"}]},
        {"findings": [invalid, valid], "keep": ["保留"]},
        {"findings": [invalid, valid], "keep": ["保留记录"], "next_adjustments": ["结合反馈"]},
    ]
    model, fake, request = setup_model(snapshots)
    events = [event async for event in model.stream_review(request)]
    found = [e for e in events if e["type"] == "finding"]
    assert len(found) == 1 and found[0]["finding"].evidence_ids == ["recent.frequency"]
    assert events[-1]["type"] == "result"
    assert events[-1]["omitted_findings"] == 1
    assert len(events[-1]["draft"].findings) == 1
    assert len(fake.calls) == 1


async def test_all_invalid_findings_fall_back_to_facts_without_fake_evidence():
    model, _, request = setup_model(
        [dict(findings=[finding("made-up")], keep=["增加999组"], next_adjustments=["加重量"])]
    )
    events = [e async for e in model.stream_review(request)]
    assert not [e for e in events if e["type"] == "finding"]
    final = events[-1]
    assert final["omitted_findings"] == 1
    assert final["draft"].findings == []
    assert "999" not in str(final["draft"].model_dump())


async def test_truncated_stream_must_not_be_reported_as_complete():
    model, _, request = setup_model([{"findings": [finding()]}])
    with pytest.raises(ValueError):
        _ = [event async for event in model.stream_review(request)]


async def test_plan_generation_streams_closed_qualitative_fields_without_doses():
    from types import SimpleNamespace

    from fitness_bot.training_schemas import SessionDraft

    final = SessionDraft(
        theme="恢复安排",
        rationale="结合当前反馈调整",
        adjustment="rest",
        adjustment_reason="先确认状态",
        preparation="轻松准备",
        preparation_minutes=5,
        finish_minutes=2,
        buffer_minutes=3,
        feedback_questions=["目前感觉如何？"],
    ).model_dump(mode="json")
    model, fake, request = setup_model(
        [
            {"theme": "未完成"},
            {"theme": final["theme"], "rationale": "结合当前"},
            {"theme": final["theme"], "rationale": final["rationale"], "adjustment": "rest"},
            final,
        ]
    )
    chunks = []
    result = await model.session_plan_with_progress(
        request, SimpleNamespace(exercises=[]), on_progress=chunks.append
    )
    assert chunks == ["恢复安排", "结合当前反馈调整"]
    assert result.adjustment == "rest"
    assert len(fake.calls) == 1


async def test_real_langchain_tool_parser_delivers_review_before_final_json():
    import json

    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessageChunk
    from langchain_core.outputs import ChatGenerationChunk

    raw = json.dumps(
        {
            "findings": [finding()],
            "keep": ["保留记录"],
            "next_adjustments": ["结合主观反馈继续核对训练安排"],
        },
        ensure_ascii=False,
    )
    fragments = [raw[i : i + 5] for i in range(0, len(raw), 5)]
    consumed = []

    class ToolChat(GenericFakeChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

        def _stream(self, messages, stop=None, run_manager=None, **kwargs):
            for index, fragment in enumerate(fragments):
                consumed.append(fragment)
                yield ChatGenerationChunk(
                    message=AIMessageChunk(
                        content="",
                        tool_call_chunks=[
                            {
                                "name": "ReviewDraft" if index == 0 else None,
                                "args": fragment,
                                "id": "call-review" if index == 0 else None,
                                "index": 0,
                            }
                        ],
                    )
                )

    model, _, request = setup_model([])
    model.decision_llm = ToolChat(messages=iter([]))
    events = []
    async for event in model.stream_review(request):
        events.append(event)
        if event["type"] == "finding":
            assert len(consumed) < len(fragments)
    assert events[-1]["type"] == "result"
    assert len(events[-1]["draft"].findings) == 1


def test_invalid_evidence_does_not_abort_http_review_and_saved_report():
    import json
    from types import SimpleNamespace

    from fastapi.testclient import TestClient
    from test_training_review import upload

    from fitness_bot.agent import CompanionAgent
    from fitness_bot.app import create_app
    from fitness_bot.catalog import Catalog
    from fitness_bot.schemas import SearchPlan

    invalid = finding("invented-reference")
    invalid["conclusion"] = "不应展示的错误结论"
    valid = finding()
    valid["conclusion"] = "记录提供了训练线索"
    model, _, _ = setup_model(
        [dict(findings=[invalid, valid], keep=["保留记录"], next_adjustments=["结合反馈"])]
    )

    async def plan(*args):
        return SearchPlan(kind="review")

    model.plan = plan
    # Exercise compatibility with adapters exposing the previous review interface.
    legacy_model = SimpleNamespace(plan=model.plan, stream_review=model.stream_review)
    with TestClient(
        create_app(Settings(_env_file=None), CompanionAgent(Catalog.load(), legacy_model))
    ) as client:
        upload(client, "record", "2025-01-01T01:00:00Z")
        events = [
            json.loads(line)
            for line in client.post("/v1/chat", json={"message": "再试一下"}).text.splitlines()
        ]
        assert events[-1]["type"] == "done", events
        response = events[-1]["response"]
        assert response["review"]["omitted_findings"] == 1
        assert "不应展示的错误结论" not in response["text"]
        assert "记录提供了训练线索" in response["text"]
        assert "已省略" in response["text"]
        assert "".join(e["delta"] for e in events if e["type"] == "text_delta") == response["text"]
        turns = client.get(f"/v1/conversations/{events[-1]['conversation_id']}/turns").json()
        assert turns[0]["response"] == response


def test_plan_progress_does_not_show_prescriptions():
    from fitness_bot.review_streaming import qualitative_sections

    assert qualitative_sections({"theme": "卧推3组", "rationale": "用20kg", "items": []}) == {}
    assert qualitative_sections({"theme": "卧推三组", "rationale": "休息两分钟", "items": []}) == {}
    assert qualitative_sections({"theme": "未结束的标题"}) == {}


async def test_closing_review_stream_closes_provider_stream_immediately():
    closed = []

    class ClosingProvider(SnapshotModel):
        async def astream(self, messages):
            try:
                yield {"findings": [finding()], "keep": ["保留记录"]}
                pytest.fail("Provider must not resume generation after close")
            finally:
                closed.append(True)

    model, _, request = setup_model([])
    model.decision_llm = ClosingProvider([])
    stream = model.stream_review(request)
    assert (await anext(stream))["type"] == "finding"
    await stream.aclose()
    assert closed == [True]


async def test_narrative_inline_facts_stream_and_final_text_match():
    import json

    from fitness_bot.training_decision import render_review, validate_review

    lead = {
        "conclusion": "这些记录共包含 {{fact:recent.workouts}}。**先观察可持续的训练节奏。**",
        "evidence_ids": ["recent.frequency"],
    }
    topic = {
        "title": "留意训练之间的安排",
        "conclusion": "把当时的时间安排与训练反馈放在一起看。",
        "evidence_ids": ["recent.frequency"],
    }
    final = {"findings": [lead, topic], "keep": [], "next_adjustments": []}
    model, fake, request = setup_model(
        [
            {"findings": [lead, {"title": "留意"}]},
            final,
        ]
    )
    facts = request.context.training_review
    facts["selection"].update(
        mode="recent_workouts", selected_count=7, requested_count=7, insufficient_history=False
    )
    facts["recent"].update(
        completed_workouts=7, completed_sets=21, training_dates=["2025-01-01", "2025-01-20"]
    )
    events = [event async for event in model.stream_review(request)]
    draft = events[-1]["draft"]
    report = validate_review(draft, facts)
    text = render_review(report)
    assert "7 次训练" in text and "{{fact:" not in text
    assert "## 留意训练之间的安排" in text
    assert len([e for e in events if e["type"] == "finding"]) == 2
    partial = draft.model_copy(update={"findings": draft.findings[:1]})
    assert text.startswith(render_review(validate_review(partial, facts)))
    payload = json.loads(fake.calls[0][-1].content)
    assert payload["fact_slots"]["recent.workouts"] == {
        "value": "7 次训练",
        "evidence_id": "recent.frequency",
    }


async def test_unknown_inline_fact_drops_only_that_finding():
    model, _, request = setup_model(
        [
            {
                "findings": [
                    {"conclusion": "记录 {{fact:missing}}", "evidence_ids": ["recent.frequency"]},
                    {"conclusion": "先观察训练安排。", "evidence_ids": ["recent.frequency"]},
                ],
                "keep": [],
                "next_adjustments": [],
            }
        ]
    )
    result = [event async for event in model.stream_review(request)]
    assert result[-1]["omitted_findings"] == 1
    assert len(result[-1]["draft"].findings) == 1
