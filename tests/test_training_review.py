import copy
import json
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from fitness_bot.agent import CompanionAgent
from fitness_bot.app import create_app
from fitness_bot.catalog import Catalog
from fitness_bot.config import Settings
from fitness_bot.schemas import SearchPlan


class OfflineModel:
    async def plan(self, request, reference):
        return SearchPlan(kind="chat")

    async def stream_reply(self, *args):
        yield "test"


def upload(client, key, at, *, unit="kg", load=20, completed=True, headers=None):
    payload = json.loads((Path(__file__).parent / "fixtures/workout_upload.json").read_text())
    w = payload["workout"]
    w.update(id=key, status="completed", startedAt=at, endedAt=at)
    r = w["records"][0]
    r.update(workoutId=key, occurredAt=at, isDraft=False)
    r["sets"][0].update(
        loadType="external", loadValue=load, loadUnit=unit, isCompleted=completed, repetitions=8
    )
    response = client.put(f"/v1/workouts/{key}", json=payload, headers=headers)
    assert response.status_code == 200
    return payload


def test_complete_date_window_facts_not_limited_to_recent_30():
    with TestClient(
        create_app(Settings(_env_file=None), CompanionAgent(Catalog.load(), OfflineModel()))
    ) as client:
        for i in range(33):
            upload(client, f"recent-{i}", "2026-09-07T01:00:00Z")
        upload(client, "baseline", "2026-08-20T01:00:00Z")
        upload(client, "outside", "2026-08-10T01:00:00Z")
        response = client.get("/v1/training/review?end_date=2026-09-09&time_zone=UTC")
        assert response.status_code == 200
        facts = response.json()
        assert facts["recent"]["completed_workouts"] == 33
        assert facts["recent"]["completed_sets"] == 33
        assert facts["baseline"]["completed_workouts"] == 1
        assert facts["recent"]["workouts_per_week"] == 33
        assert facts["baseline"]["workouts_per_week"] == 0.33
        assert facts["recent"]["start_date"] == "2026-09-02"
        assert facts["baseline"]["start_date"] == "2026-08-12"
        assert facts["snapshot_count"] == 34
        assert facts["synchronization_complete"] is None
        assert facts["plan_completion_rate"] is None


def test_boundaries_units_invalid_sets_and_source_references():
    with TestClient(
        create_app(Settings(_env_file=None), CompanionAgent(Catalog.load(), OfflineModel()))
    ) as client:
        upload(client, "recent-start", "2026-09-01T16:00:00Z")
        upload(client, "baseline-end", "2026-09-01T15:59:59.999999Z", unit="lb")
        upload(client, "end", "2026-09-08T16:00:00Z")
        upload(client, "invalid", "2026-09-07T01:00:00Z", load=-10)
        upload(client, "unfinished", "2026-09-07T02:00:00Z", completed=False)
        payload = upload(client, "draft", "2026-09-07T03:00:00Z")
        payload["version"] = 2
        payload["workout"]["records"][0]["isDraft"] = True
        client.put("/v1/workouts/draft", json=payload)
        facts = client.get(
            "/v1/training/review?end_date=2026-09-09&time_zone=Asia%2FSingapore"
        ).json()
        assert facts["recent"]["completed_sets"] == 2
        assert facts["recent"]["valid_sets"] == 1
        assert facts["recent"]["invalid_sets"] == 1
        assert facts["baseline"]["completed_sets"] == 1
        observations = facts["observations"]
        assert {o["load_unit"] for o in observations} == {"kg", "lb"}
        assert {o["source"]["workout_id"] for o in observations} == {"recent-start", "baseline-end"}
        assert all(o["source"]["record_id"] and o["set_ids"] for o in observations)
        assert all(o["load_convention"] == "unknown" for o in observations)
        assert all(o["comparison_eligible"] is False for o in observations)
        assert sum(facts["recent"]["primary_body_part_sets"].values()) == 2


def test_review_is_user_scoped_and_ignores_deleted_active_snapshots():
    with TestClient(
        create_app(Settings(_env_file=None), CompanionAgent(Catalog.load(), OfflineModel()))
    ) as client:
        upload(client, "mine", "2026-09-07T01:00:00Z")
        other = client.portal.call(client.app.state.identity.create_user, "other")
        upload(client, "theirs", "2026-09-07T01:00:00Z", headers={"X-User-Id": other["userId"]})
        payload = upload(client, "deleted", "2026-09-07T01:00:00Z")
        client.put(
            "/v1/workouts/deleted",
            json={"sourceId": payload["sourceId"], "version": 2, "deleted": True, "workout": None},
        )
        payload = upload(client, "active", "2026-09-07T01:00:00Z")
        payload["version"] = 2
        payload["workout"]["status"] = "active"
        client.put("/v1/workouts/active", json=payload)
        facts = client.get("/v1/training/review?end_date=2026-09-09").json()
        assert facts["snapshot_count"] == 1
        assert {o["source"]["workout_id"] for o in facts["observations"]} == {"mine"}


def test_server_facts_override_echo_and_replay_ignores_recomputed_context():
    with TestClient(
        create_app(Settings(_env_file=None), CompanionAgent(Catalog.load(), OfflineModel()))
    ) as client:
        upload(client, "mine", "2026-09-07T01:00:00Z")
        request = {
            "message": "复盘",
            "context": {
                "review_options": {"end_date": "2026-09-09"},
                "training_review": {"forged": True},
            },
        }
        first = client.post("/v1/chat", json=request)
        assert first.status_code == 200
        done = json.loads(first.text.splitlines()[-1])
        turns = client.get(f"/v1/conversations/{done['conversation_id']}/turns").json()
        facts = turns[0]["context"]["training_review"]
        assert facts["snapshot_count"] == 1 and "forged" not in facts
        request.update(request_id=done["request_id"], conversation_id=done["conversation_id"])
        request["context"] = copy.deepcopy(turns[0]["context"])
        upload(client, "new", "2026-09-08T01:00:00Z")
        replay = json.loads(client.post("/v1/chat", json=request).text.splitlines()[-1])
        assert replay["replayed"] is True


def test_review_input_validation_and_empty_data():
    with TestClient(
        create_app(Settings(_env_file=None), CompanionAgent(Catalog.load(), OfflineModel()))
    ) as client:
        assert client.get("/v1/training/review?time_zone=NotAZone").status_code == 422
        assert client.get("/v1/training/review?recent_days=0").status_code == 422
        facts = client.get("/v1/training/review").json()
        assert facts["snapshot_count"] == 0
        assert facts["observations"] == []
        assert "当前账号" in " ".join(facts["limitations"])
        assert datetime.fromisoformat(facts["generated_at"]).tzinfo == UTC
