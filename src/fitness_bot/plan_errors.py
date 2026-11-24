"""Keep failed model drafts in memory, expose only bounded non-private diagnostics."""

from pydantic import ValidationError

from fitness_bot.training_schemas import SemanticSessionDraft


class SessionPlanOutputError(ValueError):
    def __init__(self, validation_error, draft):
        super().__init__("Model session plan failed schema validation")
        self.validation_error = validation_error
        self.draft = draft


class PlanConstraintError(ValueError):
    def __init__(self, message, path, constraint):
        super().__init__(message)
        self.issue = {"path": path, "type": "plan_constraint", "constraint": constraint}


LOCAL_CONSTRAINTS = {
    "Value error, Model strength sets require at least sixty seconds rest": (
        "rest_seconds >= 60 for warmup/working rows"
    ),
    "Value error, Exactly one of repetitions or duration_seconds required": (
        "Exactly one of repetitions or duration_seconds must be non-null"
    ),
    "Value error, RIR range must be ordered": "target_rir_max >= target_rir_min",
    "Value error, Strength work requires rest and transition time": (
        "primary/accessory items require rest_seconds >= 60 and switch_seconds >= 60"
    ),
    "Value error, Invalid phase and purpose_role combination": (
        "purpose_role must be allowed within its phase"
    ),
    "Value error, At least one working set is required": (
        "primary/accessory require working rows; all other purpose roles require activity rows"
    ),
    "Value error, Model proposals allow at most six working sets": "working row count <= 6",
}


def _schema_fields(value):
    fields = set()
    if isinstance(value, dict):
        fields.update(value.get("properties", {}))
        for child in value.values():
            fields.update(_schema_fields(child))
    elif isinstance(value, list):
        for child in value:
            fields.update(_schema_fields(child))
    return fields


PLAN_FIELDS = _schema_fields(SemanticSessionDraft.model_json_schema())


def validation_issues(error):
    """Schema paths/codes only: never log rejected inputs, messages or extra keys."""
    if isinstance(error, PlanConstraintError):
        return [error.issue]
    if isinstance(error, SessionPlanOutputError):
        error = error.validation_error
    if not isinstance(error, ValidationError):
        return []
    return [
        {
            "path": [
                part if isinstance(part, int) or part in PLAN_FIELDS else "<unknown>"
                for part in issue["loc"]
            ],
            "type": issue["type"],
            **(
                {"constraint": LOCAL_CONSTRAINTS[issue["msg"]]}
                if issue["msg"] in LOCAL_CONSTRAINTS
                else {}
            ),
        }
        for issue in error.errors(include_input=False, include_context=False, include_url=False)[
            :20
        ]
    ]
