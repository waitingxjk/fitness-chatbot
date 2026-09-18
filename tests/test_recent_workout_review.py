"""Review history means recent sessions unless the user explicitly selects dates."""

import json

import pytest
from fastapi.testclient import TestClient
from test_training_review import OfflineModel, upload

from fitness_bot.agent import CompanionAgent
from fitness_bot.app import create_app
from fitness_bot.catalog import Catalog
from fitness_bot.config import Settings
from fitness_bot.schemas import SearchPlan
from fitness_bot.training_decision import evidence_index, render_review
from fitness_bot.training_schemas import ReviewDraft, ReviewOptions


def client_app(model=None):
    return TestClient(
        create_app(
            Settings(_env_file=None), CompanionAgent(Catalog.load(), model or OfflineModel())
        )
    )


def test_default_uses_latest_seven_completed_workouts_even_when_old():
    with client_app() as client:
        for i in range(10):
            upload(client, f"w{i}", f"2025-01-{i + 1:02d}T01:00:00Z")
        facts = client.get("/v1/training/review").json()
        assert facts["snapshot_count"] == 7
        assert {s["workout_id"] for s in facts["recent"]["sources"]} == {
            f"w{i}" for i in range(3, 10)
        }
        assert facts["selection"] == {
            "mode": "recent_workouts",
            "requested_count": 7,
            "selected_count": 7,
            "insufficient_history": False,
            "baseline_included": False,
        }
        assert facts["recent"]["start_date"] == "2025-01-04"
        assert facts["recent"]["end_date_exclusive"] == "2025-01-11"
        assert facts["recent"]["workouts_per_week"] is None
        assert facts["baseline"]["completed_workouts"] == 0
        assert "baseline.frequency" not in evidence_index(facts)
        text = render_review(
            dict(
                facts=facts,
                findings=[],
                keep=["保留记录"],
                next_adjustments=["讨论训练安排"],
                question=None,
            )
        )
        assert "最近 7 次" in text and "2025-01-04" in text
        assert "此前 21 天" not in text and "数据范围：" not in text
        assert "每周折算" not in text
        assert len(facts["limitations"]) > 0  # Retained as structured, expandable detail.


def test_fewer_than_seven_and_empty_history_are_honest():
    with client_app() as client:
        empty = client.get("/v1/training/review").json()
        assert empty["selection"]["selected_count"] == 0
        upload(client, "old", "2020-01-01T01:00:00Z")
        facts = client.get("/v1/training/review").json()
        assert facts["snapshot_count"] == 1
        assert facts["selection"]["insufficient_history"]
        text = render_review(
            dict(facts=facts, findings=[], keep=["记录"], next_adjustments=["讨论"], question=None)
        )
        assert "仅有 1 次" in text
        assert "完成 0 次" not in text


def test_count_respects_cutoff_exact_limit_and_explicit_date_mode():
    with client_app() as client:
        for i in range(10):
            upload(client, f"w{i}", f"2026-09-{i + 1:02d}T01:00:00Z")
        facts = client.get(
            "/v1/training/review?selection_mode=recent_workouts&workout_count=3&end_date=2026-09-08"
        ).json()
        assert {s["workout_id"] for s in facts["recent"]["sources"]} == {"w4", "w5", "w6"}
        dated = client.get(
            "/v1/training/review?selection_mode=date_window&end_date=2026-09-08&recent_days=7"
        ).json()
        assert dated["selection"]["mode"] == "date_window"
        assert dated["recent"]["completed_workouts"] == 7
        # Existing explicitly dated API calls retain date semantics.
        assert (
            client.get("/v1/training/review?end_date=2026-09-08").json()["selection"]["mode"]
            == "date_window"
        )
        assert client.get("/v1/training/review?workout_count=0").status_code == 422
        assert client.get("/v1/training/review?workout_count=101").status_code == 422


def test_count_excludes_other_users_active_deleted_and_future_before_limiting():
    with client_app() as client:
        upload(client, "valid", "2025-01-01T01:00:00Z")
        payload = upload(client, "active", "2026-09-01T01:00:00Z")
        payload["version"] = 2
        payload["workout"]["status"] = "active"
        client.put("/v1/workouts/active", json=payload)
        payload = upload(client, "deleted", "2026-09-02T01:00:00Z")
        client.put(
            "/v1/workouts/deleted",
            json={"sourceId": payload["sourceId"], "version": 2, "deleted": True, "workout": None},
        )
        upload(client, "future", "2099-01-01T01:00:00Z")
        other = client.portal.call(client.app.state.identity.create_user, "other")
        upload(client, "private", "2026-09-03T01:00:00Z", headers={"X-User-Id": other["userId"]})
        facts = client.get("/v1/training/review?workout_count=1").json()
        assert facts["snapshot_count"] == 1
        assert facts["recent"]["sources"][0]["workout_id"] == "valid"


@pytest.mark.parametrize("options", [None, ReviewOptions()])
def test_generic_review_chat_uses_recent_sessions_and_persists_selection(options):
    class HistoryModel:
        async def plan(self, request, reference):
            return SearchPlan(kind="review", review_options=options)

        async def review(self, request):
            assert request.context.training_review["recent"]["completed_workouts"] == 7
            return ReviewDraft(keep=["继续记录训练"], next_adjustments=["结合反馈讨论后续安排"])

    with client_app(HistoryModel()) as client:
        for i in range(8):
            upload(client, f"w{i}", f"2025-01-{i + 1:02d}T01:00:00Z")
        events = [
            json.loads(line)
            for line in client.post(
                "/v1/chat", json={"message": "帮我分析下历史健身记录"}
            ).text.splitlines()
        ]
        assert events[-1]["type"] == "done", events
        response = events[-1]["response"]
        assert "最近 7 次" in response["text"]
        assert response["review"]["facts"]["selection"]["mode"] == "recent_workouts"
        saved = client.get(f"/v1/conversations/{events[-1]['conversation_id']}/turns").json()
        assert saved[0]["response"] == response
