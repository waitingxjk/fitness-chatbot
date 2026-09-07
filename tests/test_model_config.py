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
