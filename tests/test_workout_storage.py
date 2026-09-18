import asyncio
import json
from pathlib import Path

import pytest
from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from fitness_bot.storage import ConversationStore
from fitness_bot.workout_schemas import WorkoutUpload
from fitness_bot.workout_storage import WorkoutConflict, WorkoutStore

WORKOUT_ID = "local-workout-001"


def upload(version=1, *, deleted=False):
    payload = json.loads((Path(__file__).parent / "fixtures/workout_upload.json").read_text())
    payload["version"] = version
    if deleted:
        payload.update(deleted=True, workout=None)
    return WorkoutUpload.model_validate(payload)


@pytest.fixture
async def stores(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'workouts.db'}"
    owners = [ConversationStore(url), ConversationStore(url)]
    await owners[0].initialize()
    try:
        yield [WorkoutStore(owner) for owner in owners]
    finally:
        for owner in owners:
            await owner.close()


async def test_concurrent_first_uploads_have_one_stable_receipt(stores):
    receipts = await asyncio.gather(
        *(stores[index % 2].upload(WORKOUT_ID, upload()) for index in range(12))
    )
    assert all(receipt == receipts[0] for receipt in receipts)
    assert len(await stores[0].list("local-install-001")) == 1


async def test_concurrent_same_version_different_contents_only_one_wins(stores):
    other = upload()
    other.workout.title = "a different snapshot"
    outcomes = await asyncio.gather(
        stores[0].upload(WORKOUT_ID, upload()),
        stores[1].upload(WORKOUT_ID, other),
        return_exceptions=True,
    )
    assert sum(isinstance(value, dict) for value in outcomes) == 1
    assert sum(isinstance(value, WorkoutConflict) for value in outcomes) == 1


async def test_out_of_order_concurrent_updates_cannot_resurrect_deleted_workout(stores):
    await stores[0].upload(WORKOUT_ID, upload())
    versions = [4, 8, 3, 12, 2, 6, 11, 5, 10, 7, 9]
    outcomes = await asyncio.gather(
        *(
            stores[index % 2].upload(WORKOUT_ID, upload(version, deleted=version == 12))
            for index, version in enumerate(versions)
        ),
        return_exceptions=True,
    )
    assert all(isinstance(value, dict | WorkoutConflict) for value in outcomes)
    stored = await stores[1].read("local-install-001", WORKOUT_ID)
    assert stored["version"] == 12
    assert stored["deleted"] is True
    assert stored["workout"] is None


async def test_update_commit_failure_preserves_previous_version(stores):
    receipt = await stores[0].upload(WORKOUT_ID, upload())

    def fail_commit(session):
        raise OperationalError("commit", {}, Exception("disconnected"))

    event.listen(Session, "before_commit", fail_commit)
    try:
        with pytest.raises(OperationalError):
            await stores[0].upload(WORKOUT_ID, upload(2, deleted=True))
    finally:
        event.remove(Session, "before_commit", fail_commit)
    stored = await stores[1].read("local-install-001", WORKOUT_ID)
    assert stored["version"] == receipt["version"]
    assert stored["uploadedAt"] == receipt["uploadedAt"]
    assert stored["deleted"] is False


@pytest.mark.parametrize("code", [1205, 1213])
async def test_mysql_deadlock_or_lock_timeout_retries_entire_transaction(stores, monkeypatch, code):
    original = stores[0]._upload
    attempts = []

    async def once(workout_id, payload, user_id):
        attempts.append(payload.version)
        if len(attempts) == 1:
            raise OperationalError("update", {}, Exception(code, "transient lock failure"))
        return await original(workout_id, payload, user_id)

    monkeypatch.setattr(stores[0], "_upload", once)
    receipt = await stores[0].upload(WORKOUT_ID, upload())
    assert receipt["version"] == 1
    assert attempts == [1, 1]


async def test_tombstone_can_be_first_upload_and_list_paginates(stores):
    for workout_id in ["local-c", "local-a", "local-b"]:
        await stores[0].upload(workout_id, upload(3, deleted=True))
    assert [row["workoutId"] for row in await stores[1].list("local-install-001", limit=1)] == [
        "local-a"
    ]
    assert [
        row["workoutId"] for row in await stores[1].list("local-install-001", limit=2, offset=1)
    ] == ["local-b", "local-c"]
