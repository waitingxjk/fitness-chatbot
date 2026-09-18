from fastapi.testclient import TestClient
from test_training_review import OfflineModel, upload

from fitness_bot.agent import CompanionAgent
from fitness_bot.app import create_app
from fitness_bot.catalog import Catalog
from fitness_bot.config import Settings
from fitness_bot.review_streaming import review_schema
from fitness_bot.training_decision import render_review, validate_review
from fitness_bot.training_schemas import ReviewDraft, ReviewFinding


def client_app():
    return TestClient(
        create_app(Settings(_env_file=None), CompanionAgent(Catalog.load(), OfflineModel()))
    )


def test_distribution_distinguishes_unmatched_records_from_body_parts():
    from fitness_bot.review_presentation import review_charts

    facts = {
        "recent": {"primary_body_part_sets": {"chest": 3, "unknown": 7}},
        "body_part_labels": {"chest": "胸部"},
    }
    chart = next(c for c in review_charts(facts) if c["id"] == "body-parts")
    assert "已匹配动作目录 3/10 组" in chart["description"]
    assert "待归类 7 组" in chart["description"]
    assert "不能代表完整部位分布" in chart["description"]
    assert {p["label"]: p["value"] for p in chart["points"]} == {"胸部": 3, "待归类动作": 7}


def test_review_reads_as_narrative_with_grounded_inline_numbers():
    with client_app() as client:
        upload(client, "a", "2025-01-01T01:00:00Z")
        upload(client, "b", "2025-01-03T01:00:00Z")
        facts = client.get("/v1/training/review").json()
        draft = ReviewDraft(
            findings=[
                ReviewFinding(
                    title="先把训练节奏稳住",
                    conclusion=(
                        "这批记录有 {{fact:recent.workouts}}，共 {{fact:recent.sets}}。"
                        "**重点是形成可持续安排。**\n\n- 先观察记录之间的安排，再讨论调整。"
                    ),
                    evidence_ids=["recent.frequency"],
                )
            ],
            keep=[],
            next_adjustments=[],
        )
        report = validate_review(draft, facts)
        text = render_review(report)
        assert "## 先把训练节奏稳住" in text
        assert "这批记录有 2 次训练，共 2 个已完成组" in text
        assert "{{" not in text
        for label in (
            "**推断**",
            "比较口径：",
            "可能解释：",
            "缺失信息：",
            "**记录依据**",
            "**保持**",
        ):
            assert label not in text
        assert report["findings"][0]["kind"] == "inference"
        assert report["findings"][0]["evidence"]


def test_runtime_schema_does_not_force_audit_form_fields():
    props = review_schema({"recent": {}, "baseline": {}, "observations": []})["$defs"][
        "ReviewFinding"
    ]["properties"]
    assert "title" in props and "conclusion" in props and "evidence_ids" in props
    assert (
        not {"comparison", "possible_explanation", "suggested_action", "missing_information"}
        & props.keys()
    )


def test_inline_fact_must_exist_and_belong_to_cited_evidence():
    import pytest

    facts = {"recent": {"completed_workouts": 7}, "baseline": {}, "observations": []}
    for body, ids in [
        ("看看 {{fact:missing}}", ["recent.frequency"]),
        ("共有 {{fact:recent.workouts}}", ["recent.distribution"]),
    ]:
        with pytest.raises(ValueError):
            validate_review(
                ReviewDraft(
                    findings=[ReviewFinding(conclusion=body, evidence_ids=ids)],
                    keep=[],
                    next_adjustments=[],
                ),
                facts,
            )


def test_charts_reconcile_to_selected_records_and_keep_unknown_body_parts():
    with client_app() as client:
        upload(client, "a", "2025-01-01T01:00:00Z")
        p = upload(client, "b", "2025-01-03T01:00:00Z")
        p["version"] = 2
        p["workout"]["records"][0]["exerciseId"] = "unmapped-test-exercise"
        client.put("/v1/workouts/b", json=p)
        facts = client.get("/v1/training/review").json()
        report = validate_review(ReviewDraft(keep=[], next_adjustments=[]), facts)
        charts = {c["id"]: c for c in report["charts"]}
        assert [p["source"]["workout_id"] for p in charts["training-timeline"]["points"]] == [
            "a",
            "b",
        ]
        assert (
            sum(p["value"] for p in charts["session-sets"]["points"])
            == facts["recent"]["completed_sets"]
        )
        assert (
            sum(p["value"] for p in charts["body-parts"]["points"])
            == facts["recent"]["completed_sets"]
        )
        assert any(p["label"] == "待归类动作" for p in charts["body-parts"]["points"])
        assert facts["recent"]["body_part_coverage"] == {
            "catalog_matched_sets": 1,
            "unclassified_sets": 1,
            "total_completed_sets": 2,
        }
        assert "snapshot" not in str(charts)


def test_empty_history_does_not_invent_charts():
    with client_app() as client:
        facts = client.get("/v1/training/review").json()
        assert validate_review(ReviewDraft(keep=[], next_adjustments=[]), facts)["charts"] == []


def test_chart_scope_follows_latest_seven_not_recent_days():
    with client_app() as client:
        for i in range(9):
            upload(client, f"record-{i}", f"2025-01-{i + 1:02d}T01:00:00Z")
        facts = client.get("/v1/training/review").json()
        charts = validate_review(ReviewDraft(keep=[], next_adjustments=[]), facts)["charts"]
        sessions = next(c for c in charts if c["id"] == "session-sets")["points"]
        assert len(sessions) == 7
        assert [p["source"]["workout_id"] for p in sessions] == [f"record-{i}" for i in range(2, 9)]
        assert sum(p["value"] for p in sessions) == facts["recent"]["completed_sets"]


def test_inline_slots_cannot_leak_in_titles_or_tail_text():
    import pytest

    facts = {"recent": {"completed_workouts": 7}, "observations": []}
    for draft in [
        ReviewDraft(
            findings=[
                ReviewFinding(
                    title="{{fact:recent.workouts}}",
                    conclusion="记录概览",
                    evidence_ids=["recent.frequency"],
                )
            ],
            keep=[],
            next_adjustments=[],
        ),
        ReviewDraft(keep=["{{fact:recent.workouts}}"], next_adjustments=[]),
    ]:
        with pytest.raises(ValueError, match="only supported"):
            validate_review(draft, facts)


def test_factual_limit_statements_are_not_misclassified_as_claims():
    facts = {"recent": {}, "observations": [], "synchronization_complete": None}
    for text in [
        "已完成组不等于有效组。",
        "完成组数不能直接当作有效组。",
        "记录有空档，但不能据此认定恢复训练。",
        "当前无法确认已经开始停训。",
        "这些记录不足以证明已恢复训练。",
    ]:
        report = validate_review(
            ReviewDraft(
                findings=[ReviewFinding(conclusion=text, evidence_ids=["recent.frequency"])],
                keep=[],
                next_adjustments=[],
            ),
            facts,
        )
        assert report["findings"][0]["conclusion"] == text


def test_negated_limit_does_not_allow_a_positive_claim_elsewhere():
    import pytest

    facts = {"recent": {}, "observations": [], "synchronization_complete": None}
    for text in [
        "不等于有效组，但这些都是有效组。",
        "不能据此认定恢复训练，但你已经恢复训练。",
        "不能忽略你已经恢复训练。",
        "当前无法确认已经开始停训。但你已经开始停训。",
        "记录包含 999 次训练。",
    ]:
        with pytest.raises(ValueError):
            validate_review(
                ReviewDraft(
                    findings=[ReviewFinding(conclusion=text, evidence_ids=["recent.frequency"])],
                    keep=[],
                    next_adjustments=[],
                ),
                facts,
            )


def test_fact_keys_with_catalog_body_part_spaces_resolve_exactly():
    facts = {
        "recent": {"primary_body_part_sets": {"upper legs": 12}},
        "body_part_labels": {"upper legs": "腿部"},
        "observations": [],
    }
    report = validate_review(
        ReviewDraft(
            findings=[
                ReviewFinding(
                    conclusion="主要投入为 {{fact:recent.part.upper legs}}。",
                    evidence_ids=["recent.distribution"],
                )
            ],
            keep=[],
            next_adjustments=[],
        ),
        facts,
    )
    assert report["findings"][0]["conclusion"] == "主要投入为 腿部 12 个已完成组。"


def test_spaced_fact_key_still_requires_exact_key_and_cited_evidence():
    import pytest

    facts = {"recent": {"primary_body_part_sets": {"upper legs": 12}}, "observations": []}
    for key, ids in [
        ("recent.part.upper arms", ["recent.distribution"]),
        ("recent.part.upper legs", ["recent.frequency"]),
    ]:
        with pytest.raises(ValueError):
            validate_review(
                ReviewDraft(
                    findings=[
                        ReviewFinding(
                            conclusion="{{fact:" + key + "}}",
                            evidence_ids=ids,
                        )
                    ],
                    keep=[],
                    next_adjustments=[],
                ),
                facts,
            )
