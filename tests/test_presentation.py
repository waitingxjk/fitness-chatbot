import copy
import re

import pytest

from fitness_bot.presentation import compile_outline, fallback_outline


def test_report_short_ids_resolve_facts_and_retry_collisions(monkeypatch):
    from fitness_bot import presentation

    candidates = iter(["11111111", "11111111", *[f"{n:08x}" for n in range(100)]])
    monkeypatch.setattr(presentation, "token_hex", lambda size: next(candidates))
    compiled = compile_outline(outline(), facts())
    ids = [b["id"] for b in compiled["outline"]]
    ids += list(compiled["artifacts"]) + list(compiled["datasets"]) + list(compiled["facts"])
    assert all(re.fullmatch(r"(block|chart|data|fact)_[0-9a-f]{8}", key) for key in ids)
    assert len({key.split("_")[1] for key in ids}) == len(ids)
    assert all(
        compiled["facts"][key]["evidence_id"] in block["evidence_ids"]
        for block in compiled["outline"]
        if block["type"] == "markdown"
        for key in block["fact_ids"]
    )


def test_short_fact_references_produce_portable_parts_and_reject_uncited_or_legacy_keys():
    from fitness_bot.presentation import validate_generated_block

    source = facts()
    source["recent"]["primary_body_part_sets"] = {"upper legs": 6}
    compiled = compile_outline(outline(), source)
    expected = compiled["outline"][2]
    fact_id = next(
        key
        for key, value in compiled["facts"].items()
        if value["source_key"] == "recent.part.upper legs"
    )
    raw = {"id": expected["id"], "content": f"记录中有 {{{{fact:{fact_id}}}}}。"}
    block, _ = validate_generated_block(raw, expected, source, compiled["facts"])
    assert block["parts"] == [
        {"type": "text", "text": "记录中有 "},
        {"type": "fact_ref", "fact_id": fact_id},
        {"type": "text", "text": "。"},
    ]
    assert compiled["facts"][fact_id]["value"] in block["content"]
    for wrong in [
        "fact_deadbeef",
        "chart_11111111",
        "recent.part.upper legs",
        compiled["outline"][0]["fact_ids"][0],
    ]:
        with pytest.raises(ValueError):
            validate_generated_block(
                {**raw, "content": f"{{{{fact:{wrong}}}}}"}, expected, source, compiled["facts"]
            )


def facts():
    return {
        "time_zone": "UTC",
        "selection": {"mode": "recent_workouts", "baseline_included": False},
        "recent": {
            "completed_workouts": 2,
            "completed_sets": 6,
            "primary_body_part_sets": {"chest": 6},
        },
        "baseline": {},
        "observations": [],
        "body_part_labels": {"chest": "胸部"},
        "cadence": {
            "sessions": [
                {
                    "started_at": "2026-01-01T01:00:00Z",
                    "completed_sets": 3,
                    "source": {"source_id": "a", "workout_id": "one", "version": 1},
                },
                {
                    "started_at": "2026-01-03T01:00:00Z",
                    "completed_sets": 3,
                    "source": {"source_id": "a", "workout_id": "two", "version": 1},
                },
            ],
            "intervals": [],
            "truncated": False,
        },
    }


def outline():
    return {
        "outline": [
            {"type": "text", "purpose": "主要判断", "evidence_ids": ["recent.frequency"]},
            {"type": "text", "purpose": "训练节奏", "evidence_ids": ["cadence.spacing"]},
            {"type": "chart", "chart_key": "training-timeline"},
            {"type": "text", "purpose": "部位分布", "evidence_ids": ["recent.distribution"]},
            {"type": "chart", "chart_key": "body-parts"},
        ]
    }


def test_compile_merges_adjacent_text_and_builds_resolvable_immutable_resources():
    source = facts()
    compiled = compile_outline(outline(), source)
    assert [b["type"] for b in compiled["outline"]] == [
        "markdown",
        "chart_ref",
        "markdown",
        "chart_ref",
    ]
    assert compiled["outline"][0]["purposes"] == ["主要判断", "训练节奏"]
    assert len(set(b["id"] for b in compiled["outline"])) == 4
    assert len(compiled["artifacts"]) == len(compiled["datasets"]) == 2
    for b in compiled["outline"]:
        if b["type"] == "chart_ref":
            artifact = compiled["artifacts"][b["artifact_id"]]
            assert artifact["format"] == "chart-spec/v1"
            assert compiled["datasets"][artifact["dataset_id"]]["rows"]
    before = copy.deepcopy(compiled)
    source["cadence"]["sessions"][0]["source"]["workout_id"] = "mutated"
    assert compiled == before
    assert compile_outline(outline(), facts())["outline"][0]["id"] != compiled["outline"][0]["id"]


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d["outline"][0].update(evidence_ids=["invented"]),
        lambda d: d["outline"][2].update(chart_key="invented"),
        lambda d: d["outline"].append({"type": "chart", "chart_key": "body-parts"}),
        lambda d: d["outline"].insert(0, {"type": "chart", "chart_key": "session-sets"}),
    ],
)
def test_invalid_layout_cannot_allocate_unbound_references(change):
    raw = outline()
    change(raw)
    with pytest.raises(ValueError):
        compile_outline(raw, facts())


def test_empty_facts_fallback_is_text_only_without_inventing_measurements():
    source = {"recent": {}, "baseline": {}, "observations": []}
    result = compile_outline(fallback_outline(source), source)
    assert [b["type"] for b in result["outline"]] == ["markdown"]
    assert result["artifacts"] == result["datasets"] == {}


@pytest.mark.parametrize(
    "content", ["![趋势](https://example.com/chart.svg)", '<img src="https://example.com/a">']
)
def test_report_prose_cannot_smuggle_media_outside_registry(content):
    from fitness_bot.presentation import validate_generated_block

    expected = compile_outline(outline(), facts())["outline"][0]
    with pytest.raises(ValueError, match="artifact references"):
        validate_generated_block({"id": expected["id"], "content": content}, expected, facts())


def test_fallback_keeps_available_charts_and_their_evidence():
    source = facts()
    compiled = compile_outline(fallback_outline(source), source)
    assert len(compiled["artifacts"]) == 3
    assert [b["type"] for b in compiled["outline"]] == ["markdown", "chart_ref"] * 3
    cited = {
        key for b in compiled["outline"] if b["type"] == "markdown" for key in b["evidence_ids"]
    }
    assert {"recent.frequency", "recent.distribution", "cadence.spacing"} <= cited


@pytest.mark.parametrize("count", ["2", "两", "二"])
def test_verified_selected_count_is_bound_to_the_cited_scope(count):
    from fitness_bot.presentation import validate_generated_block

    source = facts()
    expected = {"id": "block_a", "evidence_ids": ["cadence.spacing"]}
    content = f"这 {count} 次训练的间隔较短。"
    block, finding = validate_generated_block(
        {"id": "block_a", "content": content}, expected, source
    )
    assert block["content"] == "这2 次训练的间隔较短。"
    assert finding["evidence_ids"] == ["cadence.spacing"]


@pytest.mark.parametrize(
    "content,ids",
    [
        ("这 999 次训练很密集。", ["cadence.spacing"]),
        ("每周 2 次训练。", ["cadence.spacing"]),
        ("这 2 次训练很密集。", ["recent.distribution"]),
        ("负重提高了 2 kg。", ["recent.frequency"]),
    ],
)
def test_scope_binding_never_approves_other_quantities_or_uncited_scope(content, ids):
    from fitness_bot.presentation import validate_generated_block

    with pytest.raises(ValueError):
        validate_generated_block(
            {"id": "a", "content": content}, {"id": "a", "evidence_ids": ids}, facts()
        )
