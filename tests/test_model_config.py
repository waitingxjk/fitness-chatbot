from fitness_bot.config import Settings
from fitness_bot.model import OpenAICompanionModel


def test_low_reasoning_is_sent_to_model():
    settings = Settings(
        _env_file=None,
        openai_api_key="test-key",
        openai_model="gpt-5.4-mini",
        openai_reasoning_effort="low",
    )
    model = OpenAICompanionModel(settings)
    payload = model.llm._get_request_payload("你好")
    assert payload["model"] == "gpt-5.4-mini"
    assert payload["reasoning_effort"] == "low"


def test_reasoning_omitted_by_default(monkeypatch):
    monkeypatch.delenv("OPENAI_REASONING_EFFORT", raising=False)
    model = OpenAICompanionModel(Settings(_env_file=None, openai_api_key="test-key"))
    assert "reasoning_effort" not in model.llm._get_request_payload("你好")


def test_responses_api_uses_configured_deployment_and_tool_payload(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.4-mini-1")
    monkeypatch.setenv("OPENAI_USE_RESPONSES_API", "true")
    settings = Settings(_env_file=None, openai_api_key="test-key")
    model = OpenAICompanionModel(settings)
    payload = model.llm._get_request_payload("你好")
    assert payload["model"] == "gpt-5.4-mini-1"
    assert "input" in payload
    assert "messages" not in payload
    assert payload["max_output_tokens"] == 1200
    assert model.decision_llm._get_request_payload("你好")["max_output_tokens"] == 6000


async def test_responses_text_blocks_are_streamed_without_reasoning_or_tool_content():
    from langchain_core.messages import AIMessageChunk

    from fitness_bot.schemas import ChatRequest, SearchPlan

    class ResponsesLLM:
        async def astream(self, messages):
            yield AIMessageChunk(content=[{"type": "reasoning", "summary": []}])
            yield AIMessageChunk(content=[{"type": "text", "text": "连接", "index": 0}])
            yield AIMessageChunk(content=[{"type": "text", "text": "成功", "index": 0}])
            yield AIMessageChunk(content="。")

    model = OpenAICompanionModel(Settings(_env_file=None, openai_api_key="test-key"))
    model.llm = ResponsesLLM()
    chunks = [
        chunk
        async for chunk in model.stream_reply(
            ChatRequest(message="你好"), SearchPlan(kind="chat"), [], []
        )
    ]
    assert chunks == ["连接", "成功", "。"]


async def test_followup_prompt_includes_full_previous_exercise():
    import json

    from langchain_core.messages import AIMessageChunk

    from fitness_bot.catalog import Catalog
    from fitness_bot.schemas import ChatRequest, SearchPlan

    class CaptureLLM:
        async def astream(self, messages):
            self.payload = json.loads(messages[-1].content)
            yield AIMessageChunk(content="步骤说明")

    model = OpenAICompanionModel(Settings(_env_file=None, openai_api_key="test-key"))
    model.llm = CaptureLLM()
    exercise = Catalog.load().get("0289")
    chunks = [
        chunk
        async for chunk in model.stream_reply(
            ChatRequest(message="第一个怎么做"), SearchPlan(kind="chat"), [], [exercise]
        )
    ]
    assert chunks == ["步骤说明"]
    assert model.llm.payload["cards"] == []
    reference = model.llm.payload["previous_results"][0]
    assert reference["id"] == "0289"
    assert reference["instructionStepsZhHans"] == exercise.instruction_steps_zh_hans


async def test_model_receives_saved_workouts_when_client_recent_exercises_are_empty():
    import json

    from langchain_core.messages import AIMessageChunk

    from fitness_bot.schemas import ChatRequest, SearchPlan

    class CaptureLLM:
        async def astream(self, messages):
            self.payload = json.loads(messages[-1].content)
            yield AIMessageChunk(content="分析已同步训练")

    model = OpenAICompanionModel(Settings(_env_file=None, openai_api_key="test-key"))
    model.llm = CaptureLLM()
    saved = {"total_workouts": 1, "workouts": [{"title": "测试训练"}], "truncated": False}
    request = ChatRequest(message="分析历史", context={"workout_history": saved})
    assert request.context.recent_exercises == []
    assert [part async for part in model.stream_reply(request, SearchPlan(kind="chat"), [], [])]
    assert model.llm.payload["context"]["workout_history"] == saved


async def test_planning_discussion_serializes_followup_dates_ids_and_readiness():
    import json
    from datetime import UTC, datetime
    from uuid import uuid4

    from langchain_core.messages import AIMessageChunk

    from fitness_bot.schemas import ChatRequest, SearchPlan

    class CaptureLLM:
        async def astream(self, messages):
            self.payload = json.loads(messages[-1].content)
            yield AIMessageChunk(content="先按已有历史继续讨论。")

    model = OpenAICompanionModel(Settings(_env_file=None, openai_api_key="test-key"))
    model.llm = CaptureLLM()
    parent = uuid4()
    plan = SearchPlan(
        kind="session_plan",
        parent_plan_id=parent,
        session_inputs={
            "target_date": "2026-09-09",
            "readiness": {
                "observed_at": datetime(2026, 9, 8, 1, tzinfo=UTC),
                "pain": "none",
                "fatigue": "normal",
                "sleep": "normal",
            },
        },
    )
    result = [part async for part in model.stream_reply(ChatRequest(message="可以"), plan, [], [])]
    assert result == ["先按已有历史继续讨论。"]
    sent = model.llm.payload["retrieval"]
    assert sent["parent_plan_id"] == str(parent)
    assert sent["session_inputs"]["target_date"] == "2026-09-09"
    assert sent["session_inputs"]["readiness"]["observed_at"] == "2026-09-08T01:00:00Z"
