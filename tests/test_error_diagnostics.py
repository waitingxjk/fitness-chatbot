import json
import logging

from fastapi.testclient import TestClient

from fitness_bot.agent import CompanionAgent
from fitness_bot.app import create_app
from fitness_bot.catalog import Catalog
from fitness_bot.config import Settings


def test_failure_log_identifies_code_location_without_sensitive_exception_text(caplog):
    class BrokenModel:
        async def plan(self, *args):
            raise ValueError("private-training-content-and-credential")

    with TestClient(
        create_app(Settings(_env_file=None), CompanionAgent(Catalog.load(), BrokenModel()))
    ) as c:
        with caplog.at_level(logging.WARNING, logger="fitness_bot.app"):
            response = c.post("/v1/chat", json={"message": "测试请求"})
        assert json.loads(response.text.splitlines()[-1])["type"] == "error"
        assert "failure_site=agent._understand:" in caplog.text
        assert "private-training-content-and-credential" not in caplog.text
