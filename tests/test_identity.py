import json
import re
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from fitness_bot.agent import CompanionAgent
from fitness_bot.app import create_app
from fitness_bot.catalog import Catalog
from fitness_bot.config import Settings
from fitness_bot.schemas import SearchPlan


class Model:
    async def plan(self, request, reference):
        return SearchPlan(kind="chat")

    async def stream_reply(self, *args):
        yield "hello"


def make_app(**kwargs):
    return create_app(Settings(_env_file=None, **kwargs), CompanionAgent(Catalog.load(), Model()))


def test_disabled_identity_validation_and_case_sensitive_workouts():
    with TestClient(make_app()) as client:
        me = client.get("/v1/me").json()
        assert re.fullmatch("[0-9a-f]{32}", me["userId"])
        assert me["displayName"] == "local-user" and me["authEnabled"] is False
        for invalid in ["", " spaces", "a/b", "-bad", "x" * 65]:
            assert client.get("/v1/me", headers={"X-User-Id": invalid}).status_code == 422
        payload = json.loads((Path(__file__).parent / "fixtures/workout_upload.json").read_text())
        url = "/v1/workouts/" + payload["workout"]["id"]
        users = {
            name: client.portal.call(client.app.state.identity.create_user, name)["userId"]
            for name in ["Alice", "alice"]
        }
        for user in ["Alice", "alice"]:
            h = {"X-User-Id": users[user]}
            assert (
                client.get(url, params={"sourceId": payload["sourceId"]}, headers=h).status_code
                == 404
            )
            payload["workout"]["title"] = user
            assert client.put(url, json=payload, headers=h).json()["userId"] == users[user]
        for user in ["Alice", "alice"]:
            rows = client.get(
                "/v1/workouts",
                params={"sourceId": payload["sourceId"]},
                headers={"X-User-Id": users[user]},
            ).json()
            assert [row["workout"]["title"] for row in rows] == [user]


def test_enabled_tokens_and_public_assets():
    from fitness_bot.identity import utcnow

    with TestClient(make_app(auth_enabled=True)) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/playground/").status_code == 200
        for endpoint in ["/v1/me", "/v1/exercises", "/v1/conversations", "/v1/workouts?sourceId=x"]:
            assert client.get(endpoint).status_code == 401
            assert (
                client.get(endpoint, headers={"Authorization": "Bearer invalid"}).status_code == 401
            )
        user = client.portal.call(client.app.state.identity.create_user, "Alice")
        issued = client.portal.call(client.app.state.identity.issue_token, user["userId"])
        h = {"Authorization": "Bearer " + issued["token"]}
        assert client.get("/v1/me", headers=h).json() == {**user, "authEnabled": True}
        assert client.get("/v1/me", headers={**h, "X-User-Id": "alice"}).status_code == 403
        assert client.get("/v1/me", headers={**h, "X-User-Id": user["userId"]}).status_code == 200
        assert client.portal.call(client.app.state.identity.revoke_token, issued["tokenId"])
        assert client.get("/v1/me", headers=h).status_code == 401
        expired = client.portal.call(
            client.app.state.identity.issue_token, user["userId"], utcnow() - timedelta(seconds=1)
        )
        assert (
            client.get(
                "/v1/me", headers={"Authorization": "Bearer " + expired["token"]}
            ).status_code
            == 401
        )


def headers_for(client, user, enabled):
    user_id = client.portal.call(client.app.state.identity.create_user, user)["userId"]
    headers = {"X-User-Id": user_id}
    if enabled:
        token = client.portal.call(client.app.state.identity.issue_token, user_id)["token"]
        headers["Authorization"] = "Bearer " + token
    return headers


@pytest.mark.parametrize("auth_enabled", [False, True])
def test_chat_owner_and_request_replay(auth_enabled):
    with TestClient(make_app(auth_enabled=auth_enabled)) as client:
        a, b = headers_for(client, "a", auth_enabled), headers_for(client, "b", auth_enabled)
        payload = {
            "message": "private",
            "conversation_id": str(uuid4()),
            "request_id": str(uuid4()),
        }
        assert client.post("/v1/chat", json=payload, headers=a).status_code == 200
        assert client.get("/v1/conversations", headers=b).json() == []
        assert (
            client.get(
                "/v1/conversations/" + payload["conversation_id"] + "/turns", headers=b
            ).status_code
            == 404
        )
        assert client.post("/v1/chat", json=payload, headers=b).status_code == 404
        assert (
            client.post(
                "/v1/chat", json={**payload, "request_id": str(uuid4())}, headers=b
            ).status_code
            == 404
        )
        assert (
            client.post(
                "/v1/chat", json={**payload, "conversation_id": str(uuid4())}, headers=b
            ).status_code
            == 404
        )
        assert json.loads(client.post("/v1/chat", json=payload, headers=a).text)["replayed"]


@pytest.mark.parametrize("auth_enabled", [False, True])
def test_image_owners_are_independent(auth_enabled):
    from io import BytesIO

    from PIL import Image

    out = BytesIO()
    Image.new("RGB", (4, 4), "red").save(out, format="PNG")
    with TestClient(make_app(auth_enabled=auth_enabled)) as client:
        a, b = headers_for(client, "a", auth_enabled), headers_for(client, "b", auth_enabled)
        asset = client.post(
            "/v1/images", content=out.getvalue(), headers={**a, "Content-Type": "image/png"}
        ).json()
        assert client.get(asset["url"], headers=a).status_code == 200
        assert client.get(asset["url"], headers=b).status_code == 404
        assert client.post("/v1/chat", json={"images": [asset["id"]]}, headers=b).status_code == 404
        other = client.post(
            "/v1/images", content=out.getvalue(), headers={**b, "Content-Type": "image/png"}
        ).json()
        assert asset["id"] == other["id"]
        assert client.get(asset["url"], headers=b).status_code == 200
        assert client.post("/v1/chat", json={"images": [asset["id"]]}, headers=b).status_code == 200


async def test_legacy_migration_preserves_data_and_never_readopts(tmp_path):
    from io import BytesIO

    from PIL import Image
    from sqlalchemy import text

    from fitness_bot.identity import IdentityStore
    from fitness_bot.images import ImageStore
    from fitness_bot.storage import Base, ChatTurn, ChatTurnMedia, Conversation, ConversationStore
    from fitness_bot.workout_storage import WorkoutStore

    store = ConversationStore(f"sqlite+aiosqlite:///{tmp_path / 'legacy.db'}")
    async with store.engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(
                c, tables=[Conversation.__table__, ChatTurn.__table__, ChatTurnMedia.__table__]
            )
        )
        await conn.execute(
            text(
                "CREATE TABLE workout_snapshots (source_id VARCHAR(191), workout_id VARCHAR(191), "
                "version BIGINT, deleted BOOLEAN, workout JSON, uploaded_at VARCHAR(32), "
                "PRIMARY KEY (source_id, workout_id))"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO workout_snapshots VALUES "
                "('device', 'workout', 8, 1, 'null', '2026-01-01T00:00:00.000001Z')"
            )
        )
    cid = str(uuid4())
    async with store.sessions.begin() as session:
        session.add(Conversation(id=cid, title="legacy"))
    out = BytesIO()
    Image.new("RGB", (4, 4), "blue").save(out, format="PNG")
    images = ImageStore(tmp_path / "images")
    asset = images.save(out.getvalue(), "image/png")
    await store.initialize("original", images.directory)
    assert len(await store.conversations(user_id="original")) == 1
    saved = await WorkoutStore(store).read("device", "workout", user_id="original")
    assert saved["version"] == 8 and saved["deleted"]
    assert saved["uploadedAt"] == "2026-01-01T00:00:00.000001Z"
    assert await IdentityStore(store).owns_image("original", asset["id"])
    # A new orphan file after the first migration must never be adopted at restart.
    (images.directory / ("a" * 64)).write_bytes(b"orphan")
    await store.initialize("changed", images.directory)
    await store.initialize("original", images.directory)
    assert await IdentityStore(store).find_user("changed") is None
    other_id = (await IdentityStore(store).create_user("changed"))["userId"]
    assert await store.conversations(user_id=other_id) == []
    assert await WorkoutStore(store).read("device", "workout", user_id=other_id) is None
    assert not await IdentityStore(store).owns_image("changed", asset["id"])
    assert not await IdentityStore(store).owns_image("original", "a" * 64)
    assert len(await store.conversations(user_id="original")) == 1
    await store.close()


def test_token_cli_hash_only_and_revoke(tmp_path, monkeypatch):
    import sqlite3
    import subprocess
    import sys

    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'cli.db'}")
    created = subprocess.run(
        [sys.executable, "scripts/manage_tokens.py", "create-user", "--display-name", "Admin"],
        text=True,
        capture_output=True,
    )
    assert created.returncode == 0, created.stderr
    user = json.loads(created.stdout)
    issued = subprocess.run(
        [
            sys.executable,
            "scripts/manage_tokens.py",
            "issue",
            "--user-id",
            user["userId"],
            "--expires-in-days",
            "2",
        ],
        text=True,
        capture_output=True,
    )
    assert issued.returncode == 0, issued.stderr
    result = json.loads(issued.stdout)
    assert len(result["token"]) >= 43
    with sqlite3.connect(tmp_path / "cli.db") as conn:
        row = conn.execute("SELECT token_hash, expires_at FROM user_access_tokens").fetchone()
        assert row[0] != result["token"] and len(row[0]) == 64
        assert row[1] is not None
    revoked = subprocess.run(
        [sys.executable, "scripts/manage_tokens.py", "revoke", "--token-id", result["tokenId"]],
        text=True,
        capture_output=True,
    )
    assert revoked.returncode == 0, revoked.stderr
    assert result["token"] not in revoked.stdout + revoked.stderr


async def test_concurrent_migration_runs_once(tmp_path):
    import asyncio

    from fitness_bot.identity import IdentityStore
    from fitness_bot.storage import Base, ConversationStore

    store = ConversationStore(f"sqlite+aiosqlite:///{tmp_path / 'concurrent.db'}")
    async with store.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    identity = IdentityStore(store)
    await asyncio.gather(identity.migrate("original"), identity.migrate("original"))
    await store.close()


@pytest.mark.parametrize("auth_enabled", [False, True])
def test_chat_attachment_not_found_does_not_disclose_existence(auth_enabled):
    from io import BytesIO

    from PIL import Image

    out = BytesIO()
    Image.new("RGB", (4, 4), "green").save(out, format="PNG")
    with TestClient(make_app(auth_enabled=auth_enabled)) as client:
        owner = headers_for(client, "owner", auth_enabled)
        other = headers_for(client, "other", auth_enabled)
        asset = client.post(
            "/v1/images", content=out.getvalue(), headers={**owner, "Content-Type": "image/png"}
        ).json()
        for image_id in [asset["id"], "0" * 64]:
            response = client.post("/v1/chat", json={"images": [image_id]}, headers=other)
            assert response.status_code == 404
            assert response.json() == {"detail": "图片不存在。"}
        client.app.state.images.path(asset["id"]).unlink()
        assert (
            client.post("/v1/chat", json={"images": [asset["id"]]}, headers=owner).status_code
            == 422
        )
        assert (
            client.post("/v1/chat", json={"images": [asset["id"]]}, headers=other).status_code
            == 404
        )
