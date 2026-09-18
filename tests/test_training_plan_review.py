from datetime import UTC, datetime

from fastapi.testclient import TestClient
from test_training_plans import PlanModel, event, generate
from test_training_profile import profile
from test_training_review import upload

from fitness_bot.agent import CompanionAgent
from fitness_bot.app import create_app
from fitness_bot.catalog import Catalog
from fitness_bot.config import Settings
from fitness_bot.schemas import SearchPlan
from fitness_bot.training_schemas import SessionInputs


def test_dated_accepted_plan_execution_is_counted_but_not_assumed_full_completion():
    class DatedModel(PlanModel):
        async def plan(self, request, reference):
            return SearchPlan(
                kind="session_plan",
                session_inputs=SessionInputs(target_date="2026-09-08"),
                parent_plan_id=self.parent,
            )

    model = DatedModel()
    with TestClient(
        create_app(Settings(_env_file=None), CompanionAgent(Catalog.load(), model))
    ) as client:
        client.put(
            "/v1/training/profile",
            json=profile(
                session_minutes=50,
                readiness={
                    "observed_at": datetime.now(UTC).isoformat(),
                    "pain": "none",
                    "fatigue": "normal",
                    "sleep": "normal",
                },
            ),
        )
        first = generate(client)["response"]["session_plan"]
        client.post(f"/v1/training/plans/{first['id']}/events", json=event("accepted", 0))
        model.parent = first["id"]
        successor = generate(client)["response"]["session_plan"]
        client.post(f"/v1/training/plans/{successor['id']}/events", json=event("accepted", 0))
        payload = upload(client, "actual", "2026-09-08T01:00:00Z")
        client.post(
            f"/v1/training/plans/{successor['id']}/events",
            json=event(
                "executed", 1, workout={"source_id": payload["sourceId"], "workout_id": "actual"}
            ),
        )
        result = client.get("/v1/training/review?end_date=2026-09-09").json()
        summary = result["recent"]["plan_execution"]
        assert summary["accepted_plans"] == 1
        assert summary["linked_executions"] == 1
        assert summary["execution_link_rate"] == 1
        assert summary["planned_working_sets"] == 10
        assert summary["recorded_working_sets"] == 1
        assert result["plan_completion_rate"] is None


def test_explicit_client_timezone_survives_model_range_override():
    from fitness_bot.training_schemas import ReviewDraft, ReviewOptions

    class ReviewModel:
        async def plan(self, request, reference):
            return SearchPlan(kind="review", review_options=ReviewOptions(recent_days=14))

        async def review(self, request):
            assert request.context.training_review["time_zone"] == "UTC"
            return ReviewDraft(findings=[], keep=["记录训练"], next_adjustments=["确认状态"])

    with TestClient(
        create_app(Settings(_env_file=None), CompanionAgent(Catalog.load(), ReviewModel()))
    ) as client:
        client.put("/v1/training/profile", json=profile())
        response = client.post(
            "/v1/chat",
            json={"message": "复盘最近两周", "context": {"review_options": {"time_zone": "UTC"}}},
        )
        assert '"type": "done"' in response.text
