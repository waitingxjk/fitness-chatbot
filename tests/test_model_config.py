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
