import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from fitness_bot.agent import CompanionAgent
from fitness_bot.app import create_app
from fitness_bot.catalog import Catalog
from fitness_bot.config import Settings
from fitness_bot.schemas import SearchPlan
from fitness_bot.training_schemas import ReviewDraft, ReviewOptions


class ReviewModel:
    async def plan(self, request, reference):
        return SearchPlan(kind="review", review_options=ReviewOptions(scope="single"))

    async def review(self, request):
        facts = request.context.training_review
        assert facts["scope"] == "single"
        return ReviewDraft(
            findings=[
                dict(
                    conclusion="记录中的各组次数有所变化",
                    evidence_ids=["session.sequence"],
                    comparison="按本次记录的动作与组顺序观察",
                    possible_explanation="原因尚需结合主观反馈",
                    suggested_action="先确认当时感受",
                    missing_information=["真实组间休息时间"],
                )
            ],
            keep=["保留逐组记录"],
            next_adjustments=["补充主观反馈"],
        )


def client_app():
    return TestClient(
        create_app(Settings(_env_file=None), CompanionAgent(Catalog.load(), ReviewModel()))
    )


def upload(client, key, at, *, source="local-install-001", headers=None):
    p = json.loads((Path(__file__).parent / "fixtures/workout_upload.json").read_text())
    p["sourceId"] = source
    w = p["workout"]
    w.update(
        id=key,
        status="completed",
        startedAt=at,
        endedAt=at[:11] + "02:00:00Z",
        pausedDurationSeconds=300,
    )
    r = w["records"][0]
    r.update(workoutId=key, isDraft=False, occurredAt=at)
    s = r["sets"][0]
    s.update(isCompleted=True, repetitions=10, loadValue=20, rpe=7)
    second = copy.deepcopy(s)
    second.update(id="set-second", sequence=1, repetitions=8, rpe=8)
    r["sets"] = [second, s]  # Storage array order is not execution order.
    assert client.put(f"/v1/workouts/{key}", json=p, headers=headers).status_code == 200
    return p


def test_review_options_require_paired_single_selection():
    assert ReviewOptions().model_dump().get("scope") == "multiple"
    for kwargs in [
        dict(scope="single", workout_id="x"),
        dict(scope="multiple", source_id="s", workout_id="w"),
    ]:
        with pytest.raises(ValidationError):
            ReviewOptions(**kwargs)


def test_single_review_preserves_order_progression_and_unknown_rest():
    with client_app() as client:
        p = upload(client, "target", "2026-09-07T01:00:00Z")
        r = p["workout"]["records"][0]
        later = copy.deepcopy(r)
        later.update(id="later", sequence=1, occurredAt="2026-09-07T01:20:00Z")
        for s in later["sets"]:
            s.update(id="later-" + s["id"], exerciseRecordId="later")
        p["workout"]["records"] = [later, r]
        p["version"] = 2
        assert client.put("/v1/workouts/target", json=p).status_code == 200
        response = client.get(
            "/v1/training/review",
            params=dict(scope="single", source_id=p["sourceId"], workout_id="target"),
        )
        assert response.status_code == 200
        facts = response.json()
        assert facts["scope"] == "single"
        session = facts["session"]
        assert session["source"]["version"] == 2
        assert session["duration_seconds"] == 3600
        assert session["active_duration_seconds"] == 3300
        assert [r["sequence"] for r in session["records"]] == [0, 1]
        assert [s["repetitions"] for s in session["records"][0]["sets"]] == [10, 8]
        assert session["records"][0]["set_changes"][0]["repetitions_delta"] == -2
        assert session["records"][1]["gap_from_previous_entry_seconds"] == 1200
        assert all(r["rest_seconds"] is None for r in session["records"])
        assert "休息" in " ".join(facts["limitations"])
        assert session["overlaps"]
        assert facts["cycle"]["status"] == "unknown"
        assert facts["snapshot_count"] == 1


def test_single_selects_latest_but_never_substitutes_for_explicit_missing_target():
    with client_app() as client:
        upload(client, "old", "2026-08-01T01:00:00Z")
        upload(client, "new", "2026-09-07T01:00:00Z")
        assert (
            client.get("/v1/training/review?scope=single").json()["session"]["source"]["workout_id"]
            == "new"
        )
        assert (
            client.get("/v1/training/review?scope=single&end_date=2026-08-02").json()["session"][
                "source"
            ]["workout_id"]
            == "old"
        )
        assert (
            client.get(
                "/v1/training/review?scope=single&source_id=local-install-001&workout_id=missing"
            ).status_code
            == 404
        )
        other = client.portal.call(client.app.state.identity.create_user, "other")
        upload(client, "private", "2026-09-08T01:00:00Z", headers={"X-User-Id": other["userId"]})
        assert (
            client.get(
                "/v1/training/review?scope=single&source_id=local-install-001&workout_id=private"
            ).status_code
            == 404
        )
        assert (
            client.get("/v1/training/review?scope=multiple&source_id=s&workout_id=w").status_code
            == 422
        )


def test_multiple_review_includes_within_day_spacing_and_confirmed_cycle():
    with client_app() as client:
        upload(client, "a", "2026-09-07T01:00:00Z")
        upload(client, "b", "2026-09-07T13:00:00Z")
        upload(client, "c", "2026-09-09T01:00:00Z")
        profile = client.get("/v1/training/profile").json()
        cycle = dict(
            phase="maintain", starts_on="2026-09-01", ends_on="2026-09-30", reason="用户明确指定"
        )
        profile["profile"]["confirmed_cycle"] = cycle
        assert (
            client.put(
                "/v1/training/profile",
                json=dict(expected_version=profile["version"], profile=profile["profile"]),
            ).status_code
            == 200
        )
        facts = client.get("/v1/training/review?end_date=2026-09-10").json()
        assert facts["scope"] == "multiple"
        assert [v["start_gap_hours"] for v in facts["cadence"]["intervals"]] == [12, 36]
        assert len(facts["cadence"]["body_part_intervals"]["chest"]) == 2
        assert facts["cycle"]["status"] == "confirmed"
        assert facts["cycle"]["confirmed_cycle"] == cycle


def test_single_review_chat_uses_server_facts_and_persists_report():
    with client_app() as client:
        upload(client, "target", "2026-09-07T01:00:00Z")
        response = client.post(
            "/v1/chat",
            json={"message": "复盘上次训练", "context": {"training_review": {"forged": True}}},
        )
        events = [json.loads(line) for line in response.text.splitlines()]
        assert events[-1]["type"] == "done", events
        result = events[-1]["response"]
        assert result["review"]["facts"]["scope"] == "single"
        assert "单次训练复盘" in result["text"]
        assert "动作顺序" in result["text"]
        assert "休息" in result["text"]
        assert result["review"]["findings"][0]["evidence"][0]["metric"] == "session_sequence"
        turns = client.get(f"/v1/conversations/{events[-1]['conversation_id']}/turns").json()
        assert turns[0]["response"]["review"] == result["review"]
        assert "forged" not in turns[0]["context"]["training_review"]


def test_explicit_chat_selection_cannot_be_replaced_by_classifier_defaults():
    class MultipleModel(ReviewModel):
        async def plan(self, request, reference):
            return SearchPlan(kind="review", review_options=ReviewOptions())

    with TestClient(
        create_app(Settings(_env_file=None), CompanionAgent(Catalog.load(), MultipleModel()))
    ) as client:
        upload(client, "chosen", "2026-09-06T01:00:00Z")
        upload(client, "latest", "2026-09-07T01:00:00Z")
        response = client.post(
            "/v1/chat",
            json={
                "message": "分析这次",
                "context": {
                    "review_options": {
                        "scope": "single",
                        "source_id": "local-install-001",
                        "workout_id": "chosen",
                    }
                },
            },
        )
        events = [json.loads(line) for line in response.text.splitlines()]
        assert events[-1]["type"] == "done", events
        assert (
            events[-1]["response"]["review"]["facts"]["session"]["source"]["workout_id"] == "chosen"
        )


def test_subsecond_timestamps_are_chronological():
    with client_app() as client:
        upload(client, "whole", "2026-09-07T01:00:00Z")
        upload(client, "fraction", "2026-09-07T01:00:00.500000Z")
        facts = client.get("/v1/training/review?end_date=2026-09-08").json()
        assert [s["source"]["workout_id"] for s in facts["cadence"]["sessions"]] == [
            "whole",
            "fraction",
        ]
        assert (
            client.get("/v1/training/review?scope=single").json()["session"]["source"]["workout_id"]
            == "fraction"
        )


def test_duration_records_ignore_legacy_repetition_and_load_fields():
    from fitness_bot.review_details import review_heading

    with client_app() as client:
        p = upload(client, "duration", "2026-09-07T01:00:00Z")
        r = p["workout"]["records"][0]
        r["detailType"] = "duration"
        for s in r["sets"]:
            s.update(durationSeconds=30 + s["sequence"] * 10, repetitions=-1, loadValue=999)
        p["version"] = 2
        assert client.put("/v1/workouts/duration", json=p).status_code == 200
        facts = client.get("/v1/training/review?scope=single").json()
        change = facts["session"]["records"][0]["set_changes"][0]
        assert change["duration_seconds_delta"] == 10
        assert "repetitions_delta" not in change
        assert "recorded_load_delta" not in change
        assert "30 秒 → 40 秒" in " ".join(review_heading(facts))


def test_review_flags_ambiguous_order_and_truncation(monkeypatch):
    import fitness_bot.review_details as details

    with client_app() as client:
        p = upload(client, "ambiguous", "2026-09-07T01:00:00Z")
        r = p["workout"]["records"][0]
        r["sets"][0]["sequence"] = 0
        duplicate = copy.deepcopy(r)
        duplicate["id"] = "duplicate"
        for s in duplicate["sets"]:
            s.update(id="dup-" + s["id"], exerciseRecordId="duplicate")
        p["workout"]["records"].append(duplicate)
        p["version"] = 2
        assert client.put("/v1/workouts/ambiguous", json=p).status_code == 200
        facts = client.get("/v1/training/review?scope=single").json()
        session = facts["session"]
        assert session["order_ambiguous"]
        assert session["records"][0]["set_order_ambiguous"]
        assert session["records"][0]["set_changes"] == []
        assert session["overlaps"] == []
        assert "先后未确认" in " ".join(details.review_heading(facts))
        monkeypatch.setattr(details, "MAX_RECORDS", 1)
        monkeypatch.setattr(details, "MAX_SETS", 1)
        session = client.get("/v1/training/review?scope=single").json()["session"]
        assert session["records_truncated"] and session["records"][0]["sets_truncated"]
        assert session["record_count"] == 2
        rendered = " ".join(details.review_heading({"scope": "single", "session": session}))
        assert rendered.count("仅展示部分") == 2


def test_cadence_bounds_do_not_truncate_totals_and_cycle_includes_baseline(monkeypatch):
    import fitness_bot.training_review as review

    monkeypatch.setattr(review, "MAX_SESSIONS", 2)
    with client_app() as client:
        for i in range(3):
            upload(client, str(i), f"2026-09-0{i + 1}T01:00:00Z")
        saved = client.get("/v1/training/profile").json()
        saved["profile"]["confirmed_cycle"] = dict(
            phase="maintain", starts_on="2026-08-20", ends_on="2026-08-25", reason="明确记录"
        )
        client.put(
            "/v1/training/profile",
            json=dict(expected_version=saved["version"], profile=saved["profile"]),
        )
        facts = client.get("/v1/training/review?end_date=2026-09-05").json()
        assert facts["cadence"]["truncated"]
        assert len(facts["cadence"]["sessions"]) == 2
        assert facts["snapshot_count"] == facts["cadence"]["total_workouts"] == 3
        assert facts["cycle"]["overlaps_review_window"]


def test_single_excludes_active_deleted_future_and_handles_inconsistent_times():
    with client_app() as client:
        assert client.get("/v1/training/review?scope=single").status_code == 404
        p = upload(client, "bad-time", "2026-09-07T13:00:00Z")
        facts = client.get("/v1/training/review?scope=single").json()
        assert facts["session"]["duration_seconds"] is None
        assert facts["session"]["active_duration_seconds"] is None
        p["workout"]["status"] = "active"
        p["version"] = 2
        assert client.put("/v1/workouts/bad-time", json=p).status_code == 200
        assert (
            client.get(
                "/v1/training/review?scope=single&source_id=local-install-001&workout_id=bad-time"
            ).status_code
            == 404
        )
        p["workout"] = None
        p["deleted"] = True
        p["version"] = 3
        assert client.put("/v1/workouts/bad-time", json=p).status_code == 200
        assert (
            client.get(
                "/v1/training/review?scope=single&source_id=local-install-001&workout_id=bad-time"
            ).status_code
            == 404
        )
        upload(client, "future", "2099-09-07T01:00:00Z")
        assert client.get("/v1/training/review?scope=single&end_date=2099-09-08").status_code == 404


def test_single_chat_cutoff_inherits_saved_timezone_when_only_scope_is_explicit():
    with client_app() as client:
        upload(client, "local-day", "2026-09-07T01:00:00Z")
        upload(client, "next-local-day", "2026-09-07T20:00:00Z")
        saved = client.get("/v1/training/profile").json()
        saved["profile"]["time_zone"] = "Asia/Singapore"
        assert (
            client.put(
                "/v1/training/profile",
                json=dict(expected_version=saved["version"], profile=saved["profile"]),
            ).status_code
            == 200
        )
        response = client.post(
            "/v1/chat",
            json={
                "message": "复盘上次训练",
                "context": {"review_options": {"scope": "single", "end_date": "2026-09-08"}},
            },
        )
        events = [json.loads(line) for line in response.text.splitlines()]
        assert events[-1]["type"] == "done", events
        facts = events[-1]["response"]["review"]["facts"]
        assert facts["time_zone"] == "Asia/Singapore"
        assert facts["session"]["source"]["workout_id"] == "local-day"
