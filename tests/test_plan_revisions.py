from copy import deepcopy
from uuid import uuid4

from test_training_plans import event, generate
from test_training_plans import setup as _plan_setup

setup = _plan_setup


def edit_payload(plan, revision=0):
    return {
        "revision_id": str(uuid4()),
        "expected_revision": revision,
        "items": [
            {
                "exercise_id": item["exercise"]["id"],
                "set_prescriptions": deepcopy(item["set_prescriptions"]),
            }
            for item in plan["items"]
        ],
    }


def test_user_edits_create_immutable_child_and_idempotent_receipt(setup):
    client, _ = setup
    original = generate(client)["response"]["session_plan"]
    data = edit_payload(original)
    sets = data["items"][0]["set_prescriptions"]
    sets[0]["repetitions"] = 9
    sets[0]["load"] = {"strategy": "user", "value": 15, "unit": "kg", "convention": "per_hand"}
    response = client.post(f"/v1/training/plans/{original['id']}/revision", json=data)
    assert response.status_code == 200, response.text
    child = response.json()
    assert child["plan"]["id"] == data["revision_id"]
    assert child["plan"]["version"] == original["version"] + 1
    assert child["plan"]["parent_plan_id"] == original["id"]
    assert child["plan"]["items"][0]["set_prescriptions"][0]["repetitions"] == 9
    assert child["plan"]["items"][0]["set_prescriptions"][0]["load"]["value"] == 15
    assert child["plan"]["user_edited"] is True
    assert child["status"] == "proposed"
    before = client.get(f"/v1/training/plans/{original['id']}").json()
    assert before["plan"] == original
    assert before["revision"] == 1
    assert before["superseded_by"] == child["plan"]["id"]
    assert client.post(f"/v1/training/plans/{original['id']}/revision", json=data).json() == child
    data["items"][0]["set_prescriptions"][0]["repetitions"] = 10
    assert (
        client.post(f"/v1/training/plans/{original['id']}/revision", json=data).status_code == 409
    )
    assert (
        client.post(
            f"/v1/training/plans/{original['id']}/events", json=event("accepted", 0)
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/v1/training/plans/{child['plan']['id']}/events", json=event("accepted", 0)
        ).status_code
        == 200
    )


def test_invalid_or_stale_edit_leaves_original_untouched(setup):
    client, _ = setup
    original = generate(client)["response"]["session_plan"]
    data = edit_payload(original)
    data["items"][0]["set_prescriptions"][0]["load"] = {
        "strategy": "user",
        "value": 2,
        "unit": "level",
        "convention": "machine_level",
    }
    url = f"/v1/training/plans/{original['id']}/revision"
    assert client.post(url, json=data).status_code == 422
    data = edit_payload(original, revision=1)
    assert client.post(url, json=data).status_code == 409
    assert client.get(f"/v1/training/plans/{original['id']}").json()["revision"] == 0
    data = edit_payload(original)
    data["items"][1]["exercise_id"] = data["items"][0]["exercise_id"]
    assert client.post(url, json=data).status_code == 422
    data = edit_payload(original)
    data["items"][0]["exercise_id"] = "fake"
    assert client.post(url, json=data).status_code == 422


def test_accepted_plan_can_be_adjusted_without_changing_original_prescription(setup):
    client, _ = setup
    original = generate(client)["response"]["session_plan"]
    assert (
        client.post(
            f"/v1/training/plans/{original['id']}/events", json=event("accepted", 0)
        ).status_code
        == 200
    )
    data = edit_payload(original, revision=1)
    data["items"] = data["items"][:2]
    response = client.post(f"/v1/training/plans/{original['id']}/revision", json=data)
    assert response.status_code == 200, response.text
    child = response.json()["plan"]
    assert child["working_sets"] == 6
    assert child["estimated_minutes_max"] < original["estimated_minutes_max"]
    assert child["progression_basis"] is None
    assert client.get(f"/v1/training/plans/{original['id']}").json()["plan"] == original


def test_edit_rejects_unknown_exercises_and_excess_time(setup):
    client, _ = setup
    original = generate(client)["response"]["session_plan"]
    data = edit_payload(original)
    data["items"][0]["set_prescriptions"] *= 4
    for row in data["items"][0]["set_prescriptions"]:
        row["rest_seconds"] = 900
    assert (
        client.post(f"/v1/training/plans/{original['id']}/revision", json=data).status_code == 422
    )


def test_other_account_cannot_read_or_revise_plan(setup):
    client, _ = setup
    original = generate(client)["response"]["session_plan"]
    other = client.portal.call(client.app.state.identity.create_user, "other")["userId"]
    headers = {"X-User-Id": other}
    assert client.get(f"/v1/training/plans/{original['id']}", headers=headers).status_code == 404
    assert (
        client.post(
            f"/v1/training/plans/{original['id']}/revision",
            json=edit_payload(original),
            headers=headers,
        ).status_code
        == 404
    )


def test_superseded_parent_cannot_fork_a_second_child_even_with_fresh_revision(setup):
    client, model = setup
    original = generate(client)["response"]["session_plan"]
    data = edit_payload(original)
    a = client.post(f"/v1/training/plans/{original['id']}/revision", json=data)
    assert a.status_code == 200
    assert (
        client.post(
            f"/v1/training/plans/{a.json()['plan']['id']}/events", json=event("accepted", 0)
        ).status_code
        == 200
    )
    new_data = edit_payload(original, revision=1)
    assert (
        client.post(f"/v1/training/plans/{original['id']}/revision", json=new_data).status_code
        == 409
    )
    assert (
        client.post(f"/v1/training/plans/{original['id']}/revision", json=data).json() == a.json()
    )
    model.parent = original["id"]
    response = client.post("/v1/chat", json={"message": "修改旧方案"})
    import json

    terminal = json.loads(response.text.splitlines()[-1])
    assert terminal["type"] == "error"
    assert (
        client.get(f"/v1/training/plans/{original['id']}").json()["superseded_by"]
        == a.json()["plan"]["id"]
    )
