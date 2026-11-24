"""Offline regressions for malformed model plans; no provider or private history."""

from copy import deepcopy

import pytest
from pydantic import ValidationError
from test_plan_semantics import semantic_draft, semantic_request

from fitness_bot.agent import CompanionAgent
from fitness_bot.catalog import Catalog
from fitness_bot.schemas import SearchPlan
from fitness_bot.training_schemas import SemanticSessionDraft


class RepairModel:
    def __init__(self, first, repaired):
        self.first, self.repaired = first, repaired
        self.repairs = []

    async def plan(self, *_):
        return SearchPlan(kind="session_plan")

    async def session_plan_with_progress(self, *_, on_progress):
        on_progress("候选方案构思")
        if isinstance(self.first, Exception):
            raise self.first
        return SemanticSessionDraft.model_validate(deepcopy(self.first))

    async def repair_session_plan(self, request, catalog, *, feedback):
        self.repairs.append(feedback)
        return SemanticSessionDraft.model_validate(deepcopy(self.repaired))


def bad_stretch():
    value = semantic_draft()
    value["items"][-1].update(exercise_id="1405", purpose_role="stretching")
    # This real catalog stretch records repetitions; the model incorrectly gave seconds.
    return value


def fixed_stretch():
    value = bad_stretch()
    value["items"][-1]["set_prescriptions"] = [
        {"kind": "activity", "repetitions": 6, "rest_seconds": 0}
    ]
    return value


async def run(model):
    agent = CompanionAgent(Catalog.load(), model)
    return [event async for event in agent.stream(semantic_request())]


async def test_schema_failure_repaired_in_same_request():
    bad = semantic_draft()
    del bad["items"][0]["purpose_role"]
    model = RepairModel(bad, semantic_draft())
    events = await run(model)
    assert events[-1]["type"] == "done"
    assert len(model.repairs) == 1
    assert any(e["path"] == ["items", 0, "purpose_role"] for e in model.repairs[0]["issues"])
    assert [s["phase"] for s in events[-1]["response"]["session_plan"]["phase_summaries"]] == [
        "warmup",
        "main",
        "cooldown",
    ]


async def test_catalog_dose_error_repaired_without_converting_seconds_to_repetitions():
    model = RepairModel(bad_stretch(), fixed_stretch())
    events = await run(model)
    result = events[-1]["response"]["session_plan"]
    assert len(model.repairs) == 1
    row = result["items"][-1]["set_prescriptions"][0]
    assert row["repetitions"] == 6 and row["duration_seconds"] is None
    assert model.repairs[0]["rejected_draft"]["items"][-1]["duration_seconds"] == 180
    assert model.repairs[0]["catalog_constraints"][-1]["dose_field"] == "repetitions"


async def test_invalid_repair_fails_once_without_emitting_plan():
    model = RepairModel(bad_stretch(), bad_stretch())
    events = []
    with pytest.raises(ValueError, match="Dose incompatible"):
        async for event in CompanionAgent(Catalog.load(), model).stream(semantic_request()):
            events.append(event)
    assert len(model.repairs) == 1
    assert not any(e["type"] in {"done", "decision_text"} for e in events)


async def test_valid_output_needs_no_repair():
    model = RepairModel(semantic_draft(), None)
    assert (await run(model))[-1]["type"] == "done"
    assert model.repairs == []


async def test_transport_failure_does_not_trigger_semantic_repair():
    model = RepairModel(ConnectionError("provider unavailable"), semantic_draft())
    with pytest.raises(ConnectionError):
        await run(model)
    assert model.repairs == []


async def test_validation_logs_never_include_model_values_or_unknown_keys(caplog):
    bad = semantic_draft()
    bad["items"][0]["private-extra-key"] = "private-value"
    bad["items"][0]["purpose_role"] = "private-invalid-role"
    model = RepairModel(bad, semantic_draft())
    with caplog.at_level("WARNING"):
        await run(model)
    assert "purpose_role" in caplog.text and "literal_error" in caplog.text
    assert "private-" not in caplog.text
    assert "input" not in str(model.repairs[0]["issues"])


async def test_second_schema_failure_still_propagates():
    bad = semantic_draft()
    del bad["items"][0]["purpose_role"]
    model = RepairModel(bad, bad)
    with pytest.raises(ValidationError):
        await run(model)
    assert len(model.repairs) == 1


async def test_model_repair_receives_rules_and_catalog_dose_fields():
    import json

    from fitness_bot.config import Settings
    from fitness_bot.model import OpenAICompanionModel

    class Capture:
        def with_structured_output(self, schema, **_):
            assert schema is SemanticSessionDraft
            return self

        async def ainvoke(self, messages):
            self.payload = json.loads(messages[-1].content)
            return SemanticSessionDraft.model_validate(fixed_stretch())

    model = OpenAICompanionModel(Settings(_env_file=None, openai_api_key="test-key"))
    model.decision_llm = capture = Capture()
    feedback = {"rule": "Dose incompatible with catalog detail type"}
    await model.repair_session_plan(semantic_request(), Catalog.load(), feedback=feedback)
    assert capture.payload["repair_feedback"] == feedback
    stretch = next(e for e in capture.payload["catalog"] if e["id"] == "1405")
    assert stretch["dose_field"] == "repetitions"
    timed = next(e for e in capture.payload["catalog"] if e["id"] == "2141")
    assert timed["dose_field"] == "duration_seconds"


def test_http_repaired_plan_is_committed_only_as_candidate():
    import json

    from fastapi.testclient import TestClient

    from fitness_bot.app import create_app
    from fitness_bot.config import Settings

    first, second = bad_stretch(), fixed_stretch()
    first["evidence_ids"] = second["evidence_ids"] = []

    class HttpModel(RepairModel):
        async def plan(self, *_):
            return SearchPlan(
                kind="session_plan",
                session_inputs={
                    "equipment": ["dumbbell", "cable", "elliptical machine", "body weight"],
                    "session_minutes": 60,
                },
            )

    model = HttpModel(first, second)
    with TestClient(
        create_app(Settings(_env_file=None), CompanionAgent(Catalog.load(), model))
    ) as client:
        events = [
            json.loads(line)
            for line in client.post("/v1/chat", json={"message": "完整训练计划"}).text.splitlines()
        ]
        assert events[-1]["type"] == "done", events[-1]
        plan = events[-1]["response"]["session_plan"]
        saved = client.get(f"/v1/training/plans/{plan['id']}").json()
        assert saved["status"] == "proposed"
        assert saved["plan"] == plan
        assert plan["items"][-1]["set_prescriptions"][0]["repetitions"] == 6
        assert len(model.repairs) == 1


async def test_streamed_schema_repair_retains_draft_and_explains_local_constraint():
    from test_review_streaming import setup_model

    first = semantic_draft()
    first["items"][1]["set_prescriptions"][0]["rest_seconds"] = 45
    model, fake, _ = setup_model([first])
    feedbacks = []

    async def repair(request, catalog, *, feedback):
        feedbacks.append(feedback)
        return SemanticSessionDraft.model_validate(semantic_draft())

    model.repair_session_plan = repair
    # Use the actual streamed adapter and orchestrator, so its failed partial dict is retained.
    from fitness_bot.plan_generation import generate_session_plan

    await generate_session_plan(
        model, semantic_request(), Catalog.load(), on_progress=lambda _: None
    )
    feedback = feedbacks[0]
    assert feedback["rejected_draft"]["items"][1]["set_prescriptions"][0]["rest_seconds"] == 45
    assert feedback["issues"][0]["constraint"] == "rest_seconds >= 60 for warmup/working rows"
    assert feedback["catalog_constraints"][0]["dose_field"] == "duration_seconds"


async def test_cancelling_repair_stops_request_without_another_attempt():
    import asyncio

    started = asyncio.Event()
    closed = asyncio.Event()

    class BlockingRepair(RepairModel):
        async def repair_session_plan(self, request, catalog, *, feedback):
            self.repairs.append(feedback)
            try:
                started.set()
                await asyncio.Event().wait()
            finally:
                closed.set()

    model = BlockingRepair(bad_stretch(), None)
    task = asyncio.create_task(run(model))
    await asyncio.wait_for(started.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set() and len(model.repairs) == 1


def test_arbitrary_validator_message_is_not_a_loggable_constraint():
    from fitness_bot.plan_errors import SessionPlanOutputError, validation_issues

    error = ValidationError.from_exception_data(
        "SyntheticValidator",
        [
            {
                "type": "value_error",
                "loc": ("items", 0),
                "input": "private-value",
                "ctx": {"error": ValueError("private-message")},
            }
        ],
    )
    issues = validation_issues(SessionPlanOutputError(error, {"private-key": "private-value"}))
    assert issues == [{"path": ["items", 0], "type": "value_error"}]


async def test_unknown_catalog_id_feedback_identifies_the_exact_field():
    bad = semantic_draft()
    bad["items"][1]["exercise_id"] = "289"  # Must preserve real catalog ID 0289.
    model = RepairModel(bad, semantic_draft())
    assert (await run(model))[-1]["type"] == "done"
    issue = model.repairs[0]["issues"][0]
    assert issue["path"] == ["items", 1, "exercise_id"]
    assert "leading zeros" in issue["constraint"]


async def test_model_correction_focuses_on_selected_catalog_and_preserves_leading_zeros():
    import json

    from fitness_bot.config import Settings
    from fitness_bot.model import OpenAICompanionModel

    class Capture:
        def with_structured_output(self, schema, **_):
            return self

        async def ainvoke(self, messages):
            self.payload = json.loads(messages[-1].content)
            return SemanticSessionDraft.model_validate(semantic_draft())

    bad = semantic_draft()
    bad["items"][1]["exercise_id"] = "289"
    model = OpenAICompanionModel(Settings(_env_file=None, openai_api_key="test-key"))
    model.decision_llm = capture = Capture()
    await model.repair_session_plan(
        semantic_request(), Catalog.load(), feedback={"rejected_draft": bad, "issues": []}
    )
    ids = {e["id"] for e in capture.payload["catalog"]}
    assert "0289" in ids and "289" not in ids
    assert "2141" in ids
    assert len(ids) < 20


@pytest.mark.parametrize("bad_id", [289, None, ""])
async def test_malformed_id_does_not_hide_possible_repair_candidates(bad_id):
    import json

    from fitness_bot.config import Settings
    from fitness_bot.model import OpenAICompanionModel

    class Capture:
        def with_structured_output(self, schema, **_):
            return self

        async def ainvoke(self, messages):
            self.payload = json.loads(messages[-1].content)
            return SemanticSessionDraft.model_validate(semantic_draft())

    bad = semantic_draft()
    bad["items"][1]["exercise_id"] = bad_id
    model = OpenAICompanionModel(Settings(_env_file=None, openai_api_key="test-key"))
    model.decision_llm = capture = Capture()
    await model.repair_session_plan(
        semantic_request(), Catalog.load(), feedback={"rejected_draft": bad, "issues": []}
    )
    ids = {e["id"] for e in capture.payload["catalog"]}
    assert "0289" in ids
    assert len(ids) > 20


@pytest.mark.parametrize(
    ("name", "effort", "expected"),
    [
        ("gpt-5.4-mini-1", None, "low"),
        ("gpt-5.4-mini-1", "medium", "medium"),
        ("gpt-4o-mini", None, None),
    ],
)
async def test_plan_reasoning_default_preserves_explicit_configuration(name, effort, expected):
    from fitness_bot.config import Settings
    from fitness_bot.model import OpenAICompanionModel

    model = OpenAICompanionModel(
        Settings(
            _env_file=None,
            openai_api_key="test-key",
            openai_model=name,
            openai_reasoning_effort=effort,
        )
    )

    async def capture(request, schema, instruction, extra=None, **kwargs):
        selected = kwargs.get("llm") or model.decision_llm
        assert selected.reasoning_effort == expected
        return SemanticSessionDraft.model_validate(semantic_draft())

    model._decision = capture
    await model.session_plan(semantic_request(), Catalog.load())
