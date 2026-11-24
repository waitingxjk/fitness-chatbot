from copy import deepcopy
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from test_training_profile import OfflineModel
from test_training_review import upload

from fitness_bot.agent import CompanionAgent
from fitness_bot.app import create_app
from fitness_bot.catalog import Catalog
from fitness_bot.config import Settings


@pytest.fixture
def client(monkeypatch):
    # These fixtures deliberately describe early September; keep the 14-day
    # progression window deterministic instead of expiring as wall time passes.
    class ReviewClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 9, tzinfo=UTC).astimezone(tz)

    monkeypatch.setattr("fitness_bot.training_review.datetime", ReviewClock)
    with TestClient(
        create_app(Settings(_env_file=None), CompanionAgent(Catalog.load(), OfflineModel()))
    ) as connection:
        yield connection


def facts(client, headers=None):
    return client.get("/v1/training/review?end_date=2026-09-09", headers=headers).json()


def attestation(observation, **changes):
    return {
        "expected_version": 0,
        "source": observation["source"],
        "confirmed": True,
        "load_convention": "per_hand",
        "conditions": "same bench and dumbbells, full controlled range",
        "effort": {
            "scale": "RIR",
            "value": 3,
            "timing": "last_working_set",
            "observed_at": observation["performed_at"],
            "familiar_with_scale": True,
        },
        **changes,
    }


def confirm(client, observation, **changes):
    return client.put(
        f"/v1/training/observations/{observation['id']}/attestation",
        json=attestation(observation, **changes),
    )


def test_attestation_is_explicit_versioned_revocable_and_scoped(client):
    upload(client, "one", "2026-09-07T01:00:00Z")
    observation = facts(client)["observations"][0]
    saved = confirm(client, observation)
    assert saved.status_code == 200
    assert confirm(client, observation).json() == saved.json()
    assert confirm(client, observation, conditions="other").status_code == 409
    updated = facts(client)["observations"][0]
    assert updated["comparison_eligible"] is True
    assert updated["attestation"]["version"] == 1
    other = client.portal.call(client.app.state.identity.create_user, "other")
    assert (
        client.put(
            f"/v1/training/observations/{observation['id']}/attestation",
            json=attestation(observation),
            headers={"X-User-Id": other["userId"]},
        ).status_code
        == 404
    )
    assert confirm(client, observation, expected_version=1, confirmed=False).status_code == 200
    assert facts(client)["observations"][0]["comparison_eligible"] is False


def test_snapshot_replacement_and_invalid_conventions_do_not_reuse_confirmation(client):
    payload = upload(client, "one", "2026-09-07T01:00:00Z")
    observation = facts(client)["observations"][0]
    assert confirm(client, observation, load_convention="machine_level").status_code == 422
    assert confirm(client, observation).status_code == 200
    payload["version"] = 2
    client.put("/v1/workouts/one", json=payload)
    assert confirm(client, observation).status_code == 409
    assert facts(client)["observations"][0]["comparison_eligible"] is False


def test_progression_requires_two_same_condition_sessions_and_specific_effort(client):
    upload(client, "older", "2026-09-03T01:00:00Z")
    upload(client, "newer", "2026-09-07T01:00:00Z")
    observations = facts(client)["observations"]
    assert confirm(client, observations[0]).status_code == 200
    assert facts(client)["progression_signals"] == []
    assert confirm(client, observations[1]).status_code == 200
    signals = facts(client)["progression_signals"]
    assert len(signals) == 1
    assert signals[0]["latest_observation_id"] == observations[0]["id"]
    effort = deepcopy(attestation(observations[0])["effort"])
    effort["timing"] = "session"
    assert confirm(client, observations[0], expected_version=1, effort=effort).status_code == 200
    assert facts(client)["progression_signals"] == []


def test_units_and_conditions_never_merge(client):
    upload(client, "older", "2026-09-03T01:00:00Z", unit="lb")
    upload(client, "newer", "2026-09-07T01:00:00Z")
    for observation in facts(client)["observations"]:
        assert confirm(client, observation).status_code == 200
    assert facts(client)["progression_signals"] == []


@pytest.mark.parametrize("kind, convention", [("added", "added"), ("assistance", "assistance")])
def test_supported_load_types_can_be_confirmed(client, kind, convention):
    payload = upload(client, "one", "2026-09-07T01:00:37.123456Z")
    payload["version"] = 2
    payload["workout"]["records"][0]["sets"][0]["loadType"] = kind
    assert client.put("/v1/workouts/one", json=payload).status_code == 200
    observation = facts(client)["observations"][0]
    assert confirm(client, observation, load_convention=convention).status_code == 200


def test_newer_invalid_or_unfinished_record_blocks_older_progression_pair(client):
    upload(client, "older", "2026-09-03T01:00:00Z")
    upload(client, "newer", "2026-09-06T01:00:00Z")
    for observation in facts(client)["observations"]:
        assert confirm(client, observation).status_code == 200
    assert facts(client)["progression_signals"]
    upload(client, "invalid", "2026-09-07T01:00:00Z", load=-10)
    assert facts(client)["progression_signals"] == []


@pytest.mark.parametrize("change", ["revocation", "workout_revision"])
def test_evidence_changes_during_generation_cannot_be_committed(client, change):
    import json
    from datetime import UTC, datetime

    from sqlalchemy import select
    from test_training_decision import draft
    from test_training_profile import profile

    from fitness_bot.schemas import SearchPlan
    from fitness_bot.training_evidence import save_attestation
    from fitness_bot.training_schemas import ObservationAttestation, SessionDraft
    from fitness_bot.training_storage import ObservationAttestationRow
    from fitness_bot.workout_storage import WorkoutRow

    upload(client, "one", "2026-09-07T01:00:00Z")
    observation = facts(client)["observations"][0]
    assert confirm(client, observation).status_code == 200
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

    class MutatingModel:
        async def plan(self, request, reference):
            return SearchPlan(kind="session_plan")

        async def session_plan(self, request, catalog):
            store = client.app.state.store
            async with store.sessions() as session:
                row = await session.scalar(select(ObservationAttestationRow))
                user_id = row.user_id
            if change == "revocation":
                await save_attestation(
                    store.sessions,
                    user_id,
                    observation["id"],
                    ObservationAttestation.model_validate(
                        attestation(observation, expected_version=1, confirmed=False)
                    ),
                    catalog,
                )
            else:
                async with store.sessions.begin() as session:
                    row = await session.scalar(select(WorkoutRow))
                    row.version += 1
            data = draft()
            data["items"][0].update(load_strategy="history", load_observation_id=observation["id"])
            return SessionDraft.model_validate(data)

    client.app.state.agent = CompanionAgent(Catalog.load(), MutatingModel())
    response = client.post("/v1/chat", json={"message": "安排训练"})
    events = [json.loads(line) for line in response.text.splitlines()]
    assert events[-1]["type"] == "error"
    assert all(e["type"] != "done" for e in events)
    assert all(e["type"] != "text_delta" for e in events)
    assert client.get("/v1/training/plans").json() == []


def test_invalid_second_latest_record_is_not_skipped_for_an_older_pair(client):
    upload(client, "oldest", "2026-09-03T01:00:00Z")
    upload(client, "invalid", "2026-09-05T01:00:00Z", load=-10)
    upload(client, "latest", "2026-09-07T01:00:00Z")
    for observation in facts(client)["observations"]:
        assert confirm(client, observation).status_code == 200
    assert facts(client)["progression_signals"] == []
