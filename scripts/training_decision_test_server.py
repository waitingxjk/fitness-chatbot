"""Synthetic, offline browser QA fixture. No private MySQL data or model calls."""

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn

from fitness_bot.agent import CompanionAgent
from fitness_bot.app import create_app
from fitness_bot.catalog import Catalog
from fitness_bot.config import Settings
from fitness_bot.schemas import SearchPlan
from fitness_bot.training_schemas import RecommendationDraft, ReviewDraft, SessionDraft

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from test_training_decision import draft  # noqa: E402


class SyntheticModel:
    async def plan(self, request, reference):
        if "一个" in request.message:
            return SearchPlan(
                kind="recommendation", query="卧推", equipment="dumbbell", recommendation_count=1
            )
        return SearchPlan(kind="review" if "复盘" in request.message else "session_plan")

    async def recommendation(self, request, cards):
        return RecommendationDraft(
            items=[
                {
                    "exercise_id": cards[0].exercise.id,
                    "purpose": "补充推的动作，按目标余力试做。",
                    "set_prescriptions": [
                        {"kind": "warmup", "repetitions": 12, "rest_seconds": 60},
                        {"kind": "working", "repetitions": 8, "rest_seconds": 120},
                        {"kind": "working", "repetitions": 6, "rest_seconds": 120},
                    ],
                }
            ]
        )

    async def stream_reply(self, *args):
        yield "建议尝试下面的动作，展开卡片可查看每组目标。"

    async def review(self, request):
        return ReviewDraft(
            findings=[],
            keep=["继续同步实际训练"],
            next_adjustments=["先确认同步情况"],
            question="最近的训练都同步了吗？",
        )

    async def session_plan(self, request, catalog):
        return SessionDraft.model_validate(draft())


if __name__ == "__main__":
    with TemporaryDirectory(prefix="training-qa-") as directory:
        settings = Settings(
            _env_file=None,
            database_url=f"sqlite+aiosqlite:///{directory}/test.db",
            openai_api_key=None,
            image_directory=Path(directory) / "uploads",
        )
        app = create_app(settings, CompanionAgent(Catalog.load(), SyntheticModel()))
        app.state.demo_mode = True
        uvicorn.run(
            app,
            host="127.0.0.1",
            port=18082,
        )
