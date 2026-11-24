"""Training decision inputs, separate from the iOS workout snapshot contract."""

from datetime import date
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from fitness_bot.plan_semantics import ACTIVITY_ROLES, PlanPhase, PurposeRole, validate_role
from fitness_bot.prescriptions import SetDose, summarize_sets, validate_prescription_reason


class ReviewOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: Literal["single", "multiple"] = "multiple"
    selection_mode: Literal["recent_workouts", "date_window"] = "recent_workouts"
    workout_count: int = Field(default=7, ge=1, le=100)

    @model_validator(mode="before")
    @classmethod
    def legacy_explicit_dates(cls, value):
        # Preserve explicit date requests; an empty/default options object is count-based.
        if (
            isinstance(value, dict)
            and "selection_mode" not in value
            and any(
                value.get(key) is not None for key in ("recent_days", "baseline_days", "end_date")
            )
        ):
            return {**value, "selection_mode": "date_window"}
        return value

    source_id: str | None = Field(default=None, min_length=1, max_length=191, pattern=r"^[^\s/]+$")
    workout_id: str | None = Field(default=None, min_length=1, max_length=191, pattern=r"^[^\s/]+$")

    @model_validator(mode="after")
    def selection(self):
        if bool(self.source_id) != bool(self.workout_id):
            raise ValueError("source_id and workout_id must be provided together")
        if self.workout_id and self.scope != "single":
            raise ValueError("Workout selection requires single review scope")
        return self

    # Exclusive local calendar date. None includes today's synchronized records.
    end_date: date | None = None
    recent_days: int = Field(default=7, ge=1, le=90)
    baseline_days: int = Field(default=21, ge=1, le=180)
    time_zone: str = Field(default="UTC", max_length=64)

    @field_validator("time_zone")
    @classmethod
    def valid_zone(cls, value):
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError):
            raise ValueError("Unknown IANA time zone") from None
        return value

    @field_validator("end_date")
    @classmethod
    def safe_date(cls, value):
        if value and not 1901 <= value.year <= 9998:
            raise ValueError("Review date outside supported range")
        return value


class TrainingInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Readiness(TrainingInput):
    observed_at: AwareDatetime
    pain: Literal["none", "present", "unknown"] = "unknown"
    fatigue: Literal["normal", "high", "unknown"] = "unknown"
    sleep: Literal["normal", "poor", "unknown"] = "unknown"
    notes: str = Field(default="", max_length=1000)


class ConfirmedCycle(TrainingInput):
    phase: Literal["maintain", "progress", "deload", "recovery"]
    starts_on: date
    ends_on: date
    reason: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def date_order(self):
        if self.ends_on < self.starts_on or (self.ends_on - self.starts_on).days > 365:
            raise ValueError("Cycle requires an ordered date range of at most one year")
        return self


class TrainingProfile(TrainingInput):
    goal: Literal["strength", "hypertrophy", "general_fitness"] | None = None
    goal_notes: str = Field(default="", max_length=1000)
    priority_exercise_ids: list[str] = Field(default_factory=list, max_length=5)
    experience: Literal["beginner", "experienced"] | None = None
    weekly_sessions: int | None = Field(default=None, ge=1, le=7)
    session_minutes: int | None = Field(default=None, ge=10, le=180)
    equipment: list[str] = Field(default_factory=list, max_length=30)
    time_zone: str = Field(default="UTC", max_length=64)
    readiness: Readiness | None = None
    confirmed_cycle: ConfirmedCycle | None = None

    @field_validator("time_zone")
    @classmethod
    def valid_zone(cls, value):
        return ReviewOptions.valid_zone(value)


class ProfileUpdate(TrainingInput):
    expected_version: int = Field(ge=0, le=2_147_483_646)
    profile: TrainingProfile


class SessionInputs(TrainingInput):
    """Explicit conversational conditions for this turn, never profile writes."""

    goal: Literal["strength", "hypertrophy", "general_fitness"] | None = None
    experience: Literal["beginner", "experienced"] | None = None
    session_minutes: int | None = Field(default=None, ge=10, le=180)
    equipment: list[str] | None = Field(default=None, max_length=30)
    readiness: Readiness | None = None
    target_date: date | None = None


class ReviewFinding(TrainingInput):
    title: str = Field(default="", max_length=80)
    conclusion: str = Field(min_length=1, max_length=1600)
    evidence_ids: list[str] = Field(min_length=1, max_length=8)
    comparison: str = Field(default="", max_length=600)
    possible_explanation: str = Field(default="", max_length=600)
    suggested_action: str = Field(default="", max_length=600)
    missing_information: list[str] = Field(default_factory=list, max_length=5)


class ReviewDraft(TrainingInput):
    findings: list[ReviewFinding] = Field(default_factory=list, max_length=4)
    keep: list[str] = Field(max_length=4)
    next_adjustments: list[str] = Field(max_length=4)
    question: str | None = Field(default=None, max_length=500)


class ExerciseSuggestionDraft(TrainingInput):
    exercise_id: str = Field(min_length=1, max_length=64)
    purpose: str = Field(min_length=1, max_length=500)
    set_prescriptions: list[SetDose] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def validate_suggestion(self):
        validate_prescription_reason(self.purpose)
        count = sum(row.kind == "working" for row in self.set_prescriptions)
        if not 1 <= count <= 6:
            raise ValueError("Recommendation requires one to six working sets")
        return self


class RecommendationDraft(TrainingInput):
    items: list[ExerciseSuggestionDraft] = Field(default_factory=list, max_length=3)


class PreviewExercise(TrainingInput):
    exercise_id: str = Field(min_length=1, max_length=64)
    purpose: str = Field(min_length=1, max_length=300)

    @field_validator("purpose")
    @classmethod
    def no_prescription(cls, value):
        validate_prescription_reason(value)
        return value


class SessionPreviewDraft(TrainingInput):
    theme: str = Field(min_length=1, max_length=120)
    rationale: str = Field(min_length=1, max_length=600)
    items: list[PreviewExercise] = Field(default_factory=list, max_length=10)

    @field_validator("theme", "rationale")
    @classmethod
    def no_prescription(cls, value):
        validate_prescription_reason(value)
        return value


class SessionItem(TrainingInput):
    phase: PlanPhase | None = None
    purpose_role: PurposeRole | None = None
    exercise_id: str = Field(min_length=1, max_length=64)
    role: Literal["priority", "assistance", "supplement"]
    purpose: str = Field(min_length=1, max_length=500)
    sets: int = Field(default=1, ge=0, le=6)
    set_prescriptions: list[SetDose] | None = Field(default=None, min_length=1, max_length=12)
    repetitions: int | None = Field(default=None, ge=1, le=30)
    duration_seconds: int | None = Field(default=None, ge=10, le=600)
    rest_seconds: int = Field(ge=0, le=300)
    effort: str = Field(min_length=1, max_length=500)
    target_rir_min: int = Field(default=2, ge=2, le=6)
    target_rir_max: int = Field(default=3, ge=2, le=6)
    load_strategy: Literal["trial", "history"] = "trial"
    load_observation_id: str | None = Field(default=None, max_length=64)
    seconds_per_rep: int = Field(default=4, ge=3, le=10)
    switch_seconds: int = Field(default=90, ge=0, le=300)
    alternative_exercise_ids: list[str] = Field(default_factory=list, max_length=2)
    alternative_condition: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def one_dose(self):
        if self.phase is not None or self.purpose_role is not None:
            validate_role(self.phase, self.purpose_role)
        activity = self.purpose_role in ACTIVITY_ROLES
        if not activity and (self.rest_seconds < 60 or self.switch_seconds < 60):
            raise ValueError("Strength work requires rest and transition time")
        if self.set_prescriptions is not None:
            rows = [row.model_dump() for row in self.set_prescriptions]
            summary = summarize_sets(rows, allow_activity=activity)
            if summary["sets"] > 6:
                raise ValueError("Model proposals allow at most six working sets")
            for key in (
                "sets",
                "repetitions",
                "duration_seconds",
                "rest_seconds",
                "target_rir_min",
                "target_rir_max",
            ):
                setattr(self, key, summary[key])
        if not activity and self.sets == 0:
            raise ValueError("Strength work requires working sets")
        if (self.repetitions is None) == (self.duration_seconds is None):
            raise ValueError("Exactly one of repetitions or duration_seconds required")
        if self.target_rir_max < self.target_rir_min:
            raise ValueError("RIR range must be ordered")
        return self


class SessionDraft(TrainingInput):
    semantics_version: Literal[0, 1] = 0
    progression_exercise_id: str | None = Field(default=None, max_length=64)
    theme: str = Field(min_length=1, max_length=200)
    rationale: str = Field(min_length=1, max_length=1200)
    evidence_ids: list[str] = Field(default_factory=list, max_length=12)
    adjustment: Literal[
        "maintain", "increase_reps", "increase_load", "increase_sets", "reduce", "rest"
    ]
    adjustment_reason: str = Field(min_length=1, max_length=1000)
    preparation: str = Field(min_length=1, max_length=1000)
    preparation_minutes: int = Field(ge=5, le=20)
    finish_minutes: int = Field(ge=2, le=10)
    buffer_minutes: int = Field(ge=3, le=20)
    items: list[SessionItem] = Field(default_factory=list, max_length=20)
    feedback_questions: list[str] = Field(min_length=1, max_length=4)


class SemanticSessionItem(SessionItem):
    phase: PlanPhase
    purpose_role: PurposeRole
    set_prescriptions: list[SetDose] = Field(min_length=1, max_length=12)


class SemanticSessionDraft(SessionDraft):
    semantics_version: Literal[1] = 1
    preparation_minutes: int = Field(default=0, ge=0, le=20)
    finish_minutes: int = Field(default=0, ge=0, le=10)
    items: list[SemanticSessionItem] = Field(default_factory=list, max_length=20)


class WorkoutLink(TrainingInput):
    source_id: str = Field(min_length=1, max_length=191, pattern=r"^[^\s/]+$")
    workout_id: str = Field(min_length=1, max_length=191, pattern=r"^[^\s/]+$")


class EffortReport(TrainingInput):
    scale: Literal["RPE", "RIR"]
    value: float = Field(ge=0, le=10, allow_inf_nan=False)
    timing: Literal["last_working_set", "session"]
    observed_at: AwareDatetime
    familiar_with_scale: bool = False


class ObservationSource(WorkoutLink):
    version: int = Field(ge=1)
    record_id: str = Field(min_length=1, max_length=191)


class ObservationAttestation(TrainingInput):
    expected_version: int = Field(ge=0, le=2_147_483_646)
    source: ObservationSource
    confirmed: bool
    load_convention: Literal[
        "total", "per_hand", "added", "assistance", "bodyweight", "machine_stack", "machine_level"
    ]
    conditions: str = Field(min_length=1, max_length=300)
    effort: EffortReport | None = None


class PlanEventInput(TrainingInput):
    event_id: UUID
    expected_revision: int = Field(ge=0, le=2_147_483_646)
    kind: Literal["accepted", "skipped", "executed", "feedback"]
    reason: str = Field(default="", max_length=1000)
    workout: WorkoutLink | None = None
    effort: EffortReport | None = None

    @model_validator(mode="after")
    def required_feedback(self):
        if self.kind == "skipped" and not self.reason:
            raise ValueError("Skipping requires a reason")
        if (self.kind == "executed") != (self.workout is not None):
            raise ValueError("Only execution events must reference a workout")
        if self.kind == "feedback" and not self.reason and self.effort is None:
            raise ValueError("Feedback needs a reason or effort report")
        return self
