import asyncio
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fitness_bot.app import create_app
from fitness_bot.config import Settings


def test_bootstrap_generates_internal_id_once_and_rejects_unknown_headers():
    settings = Settings(_env_file=None)
    with TestClient(create_app(settings)) as client:
        request = {"installationId": "phone-install", "displayName": "小明"}
        result = client.post("/v1/users/bootstrap", json=request)
        assert result.status_code == 200
        user = result.json()
        assert re.fullmatch("[0-9a-f]{32}", user["userId"])
        assert user == {"userId": user["userId"], "displayName": "小明", "authEnabled": False}
        assert (
            client.post("/v1/users/bootstrap", json={**request, "displayName": "different"}).json()
            == user
        )
        assert client.get("/v1/me", headers={"X-User-Id": user["userId"]}).json() == user
        assert client.get("/v1/me", headers={"X-User-Id": "made-up-user"}).status_code == 404
        other = client.post("/v1/users/bootstrap", json={"installationId": "another-phone"}).json()
        assert other["userId"] != user["userId"]
        assert other["displayName"] is None
    with TestClient(create_app(settings)) as client:
        assert client.post("/v1/users/bootstrap", json=request).json() == user
        default = client.get("/v1/me").json()
        assert re.fullmatch("[0-9a-f]{32}", default["userId"])
        assert default["userId"] != user["userId"]
        assert client.get("/v1/me", headers={"X-User-Id": "local-user"}).json() == default


def test_authenticated_bootstrap_only_binds_token_user():
    with TestClient(create_app(Settings(_env_file=None, auth_enabled=True))) as client:
        request = {"installationId": "phone-install"}
        assert client.post("/v1/users/bootstrap", json=request).status_code == 401
        principal = client.portal.call(client.app.state.identity.create_user, "甲")
        other = client.portal.call(client.app.state.identity.create_user, "乙")
        issued = client.portal.call(client.app.state.identity.issue_token, principal["userId"])
        other_token = client.portal.call(client.app.state.identity.issue_token, other["userId"])
        h = {"Authorization": "Bearer " + issued["token"]}
        result = client.post("/v1/users/bootstrap", json=request, headers=h)
        assert result.json() == {**principal, "authEnabled": True}
        assert client.post("/v1/users/bootstrap", json=request, headers=h).json() == result.json()
        assert (
            client.post(
                "/v1/users/bootstrap",
                json=request,
                headers={"Authorization": "Bearer " + other_token["token"]},
            ).status_code
            == 409
        )
        with pytest.raises(ValueError, match="not found"):
            client.portal.call(client.app.state.identity.issue_token, "unknown-user")


async def test_concurrent_bootstrap_does_not_create_extra_users(tmp_path):
    from sqlalchemy import func, select

    from fitness_bot.identity import IdentityStore, User
    from fitness_bot.storage import ConversationStore

    store = ConversationStore(f"sqlite+aiosqlite:///{tmp_path / 'bootstrap.db'}")
    await store.initialize()
    identity = IdentityStore(store)
    async with store.sessions() as session:
        before = await session.scalar(select(func.count()).select_from(User))
    users = await asyncio.gather(*(identity.bootstrap("same-install") for _ in range(8)))
    assert len({user["userId"] for user in users}) == 1
    async with store.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(User)) == before + 1
    await store.close()


def test_bootstrap_preserves_unique_legacy_install_and_refuses_ambiguous_source():
    with TestClient(create_app(Settings(_env_file=None))) as client:
        default_id = client.get("/v1/me").json()["userId"]
        payload = json.loads((Path(__file__).parent / "fixtures/workout_upload.json").read_text())
        url = "/v1/workouts/" + payload["workout"]["id"]
        assert client.put(url, json=payload).status_code == 200
        result = client.post("/v1/users/bootstrap", json={"installationId": payload["sourceId"]})
        assert result.status_code == 200 and result.json()["userId"] == default_id
        payload["sourceId"] = "ambiguous-install"
        assert client.put(url, json=payload).status_code == 200
        second = client.post("/v1/users/bootstrap", json={"installationId": "second"}).json()
        assert (
            client.put(url, json=payload, headers={"X-User-Id": second["userId"]}).status_code
            == 200
        )
        assert (
            client.post(
                "/v1/users/bootstrap", json={"installationId": payload["sourceId"]}
            ).status_code
            == 409
        )
