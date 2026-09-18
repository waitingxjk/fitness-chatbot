import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from fitness_bot.app import create_app
from fitness_bot.config import Settings

FIXTURE = Path(__file__).parent / "fixtures" / "workout_upload.json"
WORKOUT_ID = "local-workout-001"
URL = f"/v1/workouts/{WORKOUT_ID}"


@pytest.fixture
def upload():
    return json.loads(FIXTURE.read_text())


@pytest.fixture
def client():
    with TestClient(create_app(Settings(_env_file=None))) as client:
        yield client


def test_upload_commit_read_restart_and_idempotency(upload):
    settings = Settings(_env_file=None)
    with TestClient(create_app(settings)) as client:
        first = client.put(URL, json=upload)
        assert first.status_code == 200
        receipt = first.json()
        assert set(receipt) == {"userId", "sourceId", "workoutId", "version", "uploadedAt"}
        assert receipt["version"] == 1
        assert receipt["uploadedAt"].endswith("Z")
    with TestClient(create_app(settings)) as client:
        # Equivalent UTC spelling is normalized before idempotency comparison.
        upload["workout"]["startedAt"] = "2026-09-07T01:00:00+00:00"
        assert client.put(URL, json=upload).json() == receipt
        saved = client.get(URL, params={"sourceId": upload["sourceId"]}).json()
        assert saved["workout"]["records"] == upload["workout"]["records"]
        assert saved["uploadedAt"] == receipt["uploadedAt"]
        assert client.get("/v1/workouts", params={"sourceId": upload["sourceId"]}).json() == [saved]
        assert client.get(URL, params={"sourceId": "another-device"}).status_code == 404
        assert client.get("/v1/workouts", params={"sourceId": "another-device"}).json() == []


def test_versions_replace_whole_aggregate_and_keep_tombstones(client, upload):
    assert client.put(URL, json=upload).status_code == 200
    different = copy.deepcopy(upload)
    different["workout"]["title"] = "changed"
    assert client.put(URL, json=different).status_code == 409
    different["version"] = 2
    different["workout"]["records"] = []
    assert client.put(URL, json=different).status_code == 200
    assert (
        client.get(URL, params={"sourceId": upload["sourceId"]}).json()["workout"]["records"] == []
    )
    tombstone = {"sourceId": upload["sourceId"], "version": 3, "deleted": True, "workout": None}
    receipt = client.put(URL, json=tombstone).json()
    assert client.put(URL, json=tombstone).json() == receipt
    assert client.put(URL, json=upload).status_code == 409
    assert client.put(URL, json=different).status_code == 409
    saved = client.get(URL, params={"sourceId": upload["sourceId"]}).json()
    assert saved["deleted"] is True
    assert saved["workout"] is None
    upload["sourceId"] = "another-device"
    assert client.put(URL, json=upload).status_code == 200


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("sourceId",), ""),
        (("version",), 0),
        (("version",), True),
        (("version",), 1.5),
        (("deleted",), True),
        (("workout",), None),
        (("workout", "id"), "mismatch"),
        (("workout", "status"), "discarded"),
        (("workout", "startedAt"), "2026-09-07T01:00:00"),
        (("workout", "startedAt"), "0"),
        (("workout", "startedAt"), "1788739200"),
        (("workout", "startedAt"), "0001-01-01T00:00:00+01:00"),
        (("workout", "startedAt"), "9999-12-31T23:59:59-01:00"),
        (("workout", "records", 0, "workoutId"), "mismatch"),
        (("workout", "records", 0, "detailType"), "unknown"),
        (("workout", "records", 0, "detailPayload"), []),
        (("workout", "records", 0, "sets", 0, "exerciseRecordId"), "mismatch"),
        (("workout", "records", 0, "sets", 0, "loadType"), "unknown"),
        (("workout", "records", 0, "sets", 0, "loadUnit"), "ton"),
        (("workout", "records", 0, "sets", 0, "difficulty"), "unknown"),
        (("workout", "records", 0, "sets", 0, "loadValue"), "NaN"),
    ],
)
def test_structure_validation(client, upload, path, value):
    node = upload
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    assert client.put(URL, json=upload).status_code == 422


def test_duplicate_child_ids_are_rejected(client, upload):
    upload["workout"]["records"].append(copy.deepcopy(upload["workout"]["records"][0]))
    assert client.put(URL, json=upload).status_code == 422


def test_nonfinite_opaque_json_rejected_without_server_error(client, upload):
    body = json.dumps(upload).replace('"legacyRating": 3', '"legacyRating": NaN')
    assert (
        client.put(URL, content=body, headers={"Content-Type": "application/json"}).status_code
        == 422
    )


def test_commit_failure_returns_503_without_acknowledging_or_persisting(client, upload):
    def fail_commit(session):
        raise SQLAlchemyError("commit failed")

    event.listen(Session, "before_commit", fail_commit)
    try:
        failed = client.put(URL, json=upload)
        assert failed.status_code == 503
        assert "uploadedAt" not in failed.json()
    finally:
        event.remove(Session, "before_commit", fail_commit)
    assert client.get(URL, params={"sourceId": upload["sourceId"]}).status_code == 404
    assert client.put(URL, json=upload).status_code == 200


def test_idempotency_preserves_opaque_json_value_types(client, upload):
    upload["workout"]["records"][0]["detailPayload"] = {"legacyFlag": True}
    assert client.put(URL, json=upload).status_code == 200
    upload["workout"]["records"][0]["detailPayload"] = {"legacyFlag": 1}
    assert client.put(URL, json=upload).status_code == 409
