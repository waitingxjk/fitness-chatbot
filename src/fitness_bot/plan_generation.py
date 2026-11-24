"""One bounded model correction before a candidate can leave orchestration."""

import logging

from pydantic import ValidationError

from fitness_bot.plan_errors import SessionPlanOutputError, validation_issues
from fitness_bot.training_decision import validate_session_plan

logger = logging.getLogger(__name__)


async def generate_session_plan(
    model, request, catalog, *, on_progress, target_date=None, original_plan=None
):
    draft = None
    try:
        if hasattr(model, "session_plan_with_progress"):
            draft = await model.session_plan_with_progress(
                request, catalog, on_progress=on_progress
            )
        else:
            draft = await model.session_plan(request, catalog)
    except (ValidationError, SessionPlanOutputError) as error:
        failure = error
    else:
        try:
            return validate_session_plan(
                draft, request, catalog, target_date=target_date, original_plan=original_plan
            )
        except ValueError as error:
            failure = error

    # Non-model failures (transport, cancellation, storage, programming errors) never retry here.
    issues = validation_issues(failure)
    logger.warning(
        "Plan validation failed stage=%s exception_type=%s validation_issues=%s repair=%s",
        "schema" if draft is None else "domain",
        type(failure).__name__,
        issues,
        hasattr(model, "repair_session_plan"),
    )
    if not hasattr(model, "repair_session_plan"):
        raise failure

    rejected = (
        draft.model_dump(mode="json")
        if draft is not None
        else (failure.draft if isinstance(failure, SessionPlanOutputError) else None)
    )
    constraints = []
    raw_items = rejected.get("items", []) if isinstance(rejected, dict) else []
    for item in raw_items if isinstance(raw_items, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("exercise_id"), str):
            continue
        exercise = catalog.get(item["exercise_id"])
        if exercise is not None:
            constraints.append(
                {
                    "exercise_id": exercise.id,
                    "phase": item.get("phase"),
                    "detail_type": exercise.detail_type,
                    "dose_field": "duration_seconds"
                    if exercise.detail_type == "duration"
                    else "repetitions",
                }
            )
    feedback = {
        "issues": issues,
        # Domain messages are local validation rules, never emitted to logs or the client.
        "rule": "Correct the schema errors" if draft is None else str(failure),
        "rejected_draft": rejected,
        "catalog_constraints": constraints,
    }
    repaired = await model.repair_session_plan(request, catalog, feedback=feedback)
    # Same validation, parent constraints and budget as the first attempt. No recursion/fallback.
    return validate_session_plan(
        repaired, request, catalog, target_date=target_date, original_plan=original_plan
    )
