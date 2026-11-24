"""Exercise the real model boundary with local, captured model messages only."""

import importlib
import importlib.util
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessageChunk

from fitness_bot.config import Settings
from fitness_bot.model import SYSTEM_PROMPT, OpenAICompanionModel
from fitness_bot.schemas import ChatRequest, SearchPlan
from fitness_bot.training_decision import RULES
from fitness_bot.training_schemas import RecommendationDraft, ReviewDraft, SemanticSessionDraft


class CaptureModel:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def with_structured_output(self, schema, *, method):
        self.schema = schema
        assert method == "function_calling"
        return self

    async def ainvoke(self, messages):
        self.calls.append(messages)
        if isinstance(getattr(self, "schema", None), dict) and hasattr(self.response, "model_dump"):
            return self.response.model_dump(mode="json")
        return self.response

    async def astream(self, messages):
        self.calls.append(messages)
        yield AIMessageChunk(content="已收到")


def model_with_capture(response):
    model = OpenAICompanionModel(Settings(_env_file=None, openai_api_key="test-key"))
    capture = CaptureModel(response)
    model.decision_llm = capture
    return model, capture


def skill_module():
    assert importlib.util.find_spec("fitness_bot.skills") is not None
    return importlib.import_module("fitness_bot.skills")


@pytest.mark.parametrize(
    ("method", "skill", "response", "contract"),
    [
        (
            "review",
            "training-review",
            ReviewDraft(keep=["保留已同步记录"], next_adjustments=["核对缺失信息"]),
            "从 fact_slots 选择 {{fact:KEY}}",
        ),
        (
            "recommendation",
            "training-recommendation",
            RecommendationDraft(),
            "items 只能选 retrieved_cards 中的真实 ID",
        ),
        (
            "session_preview",
            "training-recommendation",
            {"theme": "动作候选", "rationale": "补充当前条件后确定", "items": []},
            "不能提供组数、次数、重量、时长、休息或加量指示",
        ),
        (
            "session_plan",
            "training-recommendation",
            SemanticSessionDraft(
                theme="休息候选",
                rationale="根据当前状态调整",
                adjustment="rest",
                adjustment_reason="当前状态待恢复",
                preparation="轻松准备",
                preparation_minutes=5,
                finish_minutes=2,
                buffer_minutes=3,
                feedback_questions=["当前感觉如何？"],
            ),
            "increase_* 仅限 progression_signals 指定的动作",
        ),
    ],
)
async def test_decisions_include_packaged_expert_and_preserve_contracts(
    method, skill, response, contract
):
    model, capture = model_with_capture(response)
    facts = {"scope": "single", "session": {"title": "当前训练"}, "observations": []}
    request = ChatRequest(message="根据已有资料继续", context={"training_review": facts})
    args = {
        "review": (),
        "recommendation": ([],),
        "session_preview": ([],),
        "session_plan": (SimpleNamespace(exercises=[]),),
    }
    await getattr(model, method)(request, *args[method])
    assert len(capture.calls) == 1  # Recommendation does not require a model review first.
    messages = capture.calls[0]
    system = messages[0].content
    assert f"name: {skill}" in system
    assert skill_module().load_skill(skill) in system
    assert SYSTEM_PROMPT in system
    assert RULES in system
    assert contract in system
    payload = json.loads(messages[-1].content)
    assert payload["context"]["training_review"] == facts
    if method == "review":
        assert "fact_slots" in payload
        assert "默认复盘最近7次已完成训练" in system
    other = "training-recommendation" if skill == "training-review" else "training-review"
    assert f"name: {other}" not in system


def test_skills_are_read_as_allowlisted_package_resources(monkeypatch):
    skills = skill_module()
    calls = []
    files = importlib.resources.files

    def capture_files(package):
        calls.append(package)
        return files(package)

    monkeypatch.setattr(importlib.resources, "files", capture_files)
    for name in ("training-review", "training-recommendation"):
        document = skills.load_skill(name)
        assert f"name: {name}" in document
        assert "version:" in document
        for section in (
            "## Inputs",
            "## Method",
            "## Outputs",
            "## Evidence limits",
            "## Examples",
        ):
            assert section in document
    assert calls == ["fitness_bot.skills", "fitness_bot.skills"]


@pytest.mark.parametrize("name", ["unknown", "../training-review", "/tmp/SKILL.md"])
def test_unknown_skill_fails_before_resource_access(monkeypatch, name):
    skills = skill_module()

    def unexpected_access(*args):
        pytest.fail("Unknown skill must not read a resource")

    monkeypatch.setattr(importlib.resources, "files", unexpected_access)
    with pytest.raises(ValueError, match="Unknown expert skill"):
        skills.load_skill(name)


@pytest.mark.parametrize("kind", ["chat", "search"])
async def test_generic_conversation_and_search_do_not_load_experts(monkeypatch, kind):
    skills = skill_module()

    def unexpected_access(*args):
        pytest.fail("Generic conversation and search must not load expert resources")

    monkeypatch.setattr(importlib.resources, "files", unexpected_access)
    model, capture = model_with_capture(SearchPlan(kind=kind))
    model.planner = capture
    model.llm = capture
    request = ChatRequest(message="解释这个动作")
    plan = await model.plan(request, {})
    assert [part async for part in model.stream_reply(request, plan, [], [])] == ["已收到"]
    assert skills is not None


async def test_classifier_teaches_single_multiple_and_server_reference_boundaries():
    model, capture = model_with_capture(SearchPlan(kind="review"))
    model.planner = capture
    await model.plan(ChatRequest(message="复盘上次训练"), {})
    prompt = capture.calls[0][0].content
    assert 'scope="single"' in prompt
    assert 'scope="multiple"' in prompt
    assert "默认 multiple" in prompt
    assert "最近一次已完成训练" in prompt
    assert "source_id/workout_id" in prompt
    assert "只能从服务端" in prompt
    assert "成对" in prompt


async def test_classifier_distinguishes_workout_counts_from_explicit_dates():
    model, capture = model_with_capture(SearchPlan(kind="review"))
    model.planner = capture
    await model.plan(ChatRequest(message="帮我分析下历史健身记录"), {})
    prompt = capture.calls[0][0].content
    assert "默认最近 7 次已完成训练" in prompt
    assert 'selection_mode="recent_workouts"' in prompt
    assert 'selection_mode="date_window"' in prompt
    assert "不能把“最近七次”转成 recent_days=7" in prompt
