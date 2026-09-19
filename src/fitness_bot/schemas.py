"""Validated HTTP contracts and iOS-compatible exercise records."""

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, StringConstraints, model_validator
from pydantic.alias_generators import to_camel

from fitness_bot.prescriptions import SetPrescription
from fitness_bot.training_schemas import ReviewOptions, SessionInputs
from fitness_bot.workout_schemas import WorkoutUpload

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
ImageId = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
InputText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=4000)]
ExerciseId = Annotated[str, StringConstraints(min_length=1, max_length=64)]


class Exercise(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, frozen=True)

    source_kind: Literal["standard", "custom"] = "standard"
    training_type: Literal["strength", "cardio", "mobility", "stretching", "other"] | None = None
    avatar: dict | None = None
    id: ExerciseId
    source_name: str
    display_name_zh_hans: str
    aliases_zh_hans: list[str]
    body_part_code: str
    equipment_code: str
    target_muscle_code: str
    secondary_muscle_codes: list[str]
    instruction_steps_zh_hans: list[str]
    media_id: str
    detail_type: Literal["weightedReps", "bodyweightReps", "duration", "machineLevelReps"]
    default_weight_unit: Literal["kg", "lb"] | None
    is_active: bool
    catalog_version: str
    created_at: datetime
    updated_at: datetime


class Message(BaseModel):
    role: Literal["user", "assistant"]
    text: InputText
    images: list[ImageId] = Field(default_factory=list, max_length=3)


class WorkoutSnapshot(BaseModel):
    id: ExerciseId
    status: Literal["active", "paused", "completed", "discarded"] = "active"
    exercise_ids: list[ExerciseId] = Field(default_factory=list, max_length=100)


class RecentExercise(BaseModel):
    exercise_id: ExerciseId
    performed_at: datetime
    completed_sets: int = Field(default=0, ge=0, le=1000)
    repetitions: int | None = Field(default=None, ge=0, le=100000)
    weight_kg: float | None = Field(default=None, ge=0, le=10000, allow_inf_nan=False)
    duration_seconds: int | None = Field(default=None, ge=0, le=86400)


class StoredWorkoutHistory(BaseModel):
    total_workouts: int = Field(default=0, ge=0)
    workouts: list[dict] = Field(default_factory=list, max_length=30)
    truncated: bool = False


class CurrentWorkoutUpload(WorkoutUpload):
    @model_validator(mode="after")
    def active_snapshot(self):
        if self.deleted or self.workout is None or self.workout.status not in {"active", "paused"}:
            raise ValueError("Current workout requires a non-deleted active or paused snapshot")
        return self


class TrainingContext(BaseModel):
    planning_target: Literal["current_workout", "next_workout"] | None = None
    current_workout: CurrentWorkoutUpload | None = None
    remaining_minutes: int | None = Field(default=None, strict=True, ge=10, le=180)
    # Server-owned continuation metadata. Never restored from client echoes.
    planning_target_date: date | None = None
    custom_exercise_drafts: list[dict] = Field(default_factory=list, max_length=3)
    plan_to_modify: UUID | None = None
    review_options: ReviewOptions = Field(default_factory=ReviewOptions)
    training_review: dict | None = None
    training_profile: dict | None = None
    session_plans: list[dict] = Field(default_factory=list, max_length=5)
    active_workout: WorkoutSnapshot | None = None
    recent_exercises: list[RecentExercise] = Field(default_factory=list, max_length=100)
    available_equipment: list[Annotated[str, StringConstraints(max_length=64)]] = Field(
        default_factory=list, max_length=30
    )
    # Server-owned: HTTP callers may echo this from a saved turn, but it is always
    # replaced from the authenticated user's database before model invocation.
    workout_history: StoredWorkoutHistory | None = None


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    _user_id: str | None = PrivateAttr(default=None)

    message: InputText = ""
    images: list[ImageId] = Field(default_factory=list, max_length=3)
    request_id: UUID = Field(default_factory=uuid4)
    conversation_id: UUID = Field(default_factory=uuid4)
    history: list[Message] = Field(default_factory=list, max_length=30)
    previous_result_ids: list[ExerciseId] = Field(default_factory=list, max_length=3)
    context: TrainingContext = Field(default_factory=TrainingContext)

    @model_validator(mode="after")
    def require_content(self):
        if not self.message and not self.images:
            raise ValueError("请提供文字或至少一张图片。")
        return self


class SearchPlan(BaseModel):
    kind: Literal[
        "search", "recommendation", "chat", "review", "session_plan", "custom_exercise"
    ] = "search"
    recommendation_count: int = Field(default=3, ge=1, le=3)
    query: str = Field(default="", max_length=200)
    body_part: str | None = Field(default=None, max_length=64)
    equipment: str | None = Field(default=None, max_length=64)
    target_muscle: str | None = Field(default=None, max_length=64)
    review_options: ReviewOptions | None = None
    session_inputs: SessionInputs | None = None
    parent_plan_id: UUID | None = None


class ExerciseCard(BaseModel):
    exercise: Exercise
    equipment_label: str
    body_part_label: str
    context: str
    can_add_to_active_workout: bool
    set_prescriptions: list[SetPrescription] = Field(default_factory=list, max_length=12)


class ProcessingStep(BaseModel):
    stage: Literal["understanding", "retrieving", "responding"]
    title: str
    summary: str
    data: dict = Field(default_factory=dict)


class ChatResponse(BaseModel):
    conversation_id: UUID
    result_id: UUID = Field(default_factory=uuid4)
    kind: Literal["search", "recommendation", "chat", "review", "session_plan", "custom_exercise"]
    text: str
    cards: list[ExerciseCard]
    catalog_version: str
    trace: list[ProcessingStep] = Field(default_factory=list)
    schema_version: Literal[1] | None = None
    outline: list[dict] = Field(default_factory=list)
    blocks: list[dict] = Field(default_factory=list)
    artifacts: dict[str, dict] = Field(default_factory=dict)
    datasets: dict[str, dict] = Field(default_factory=dict)
    facts: dict[str, dict] = Field(default_factory=dict)
    custom_exercise: dict | None = None
    review: dict | None = None
    session_plan: dict | None = None
    plan_preview: dict | None = None
    suggestion_kind: Literal["single_exercise", "alternatives", "session_plan"] | None = None
