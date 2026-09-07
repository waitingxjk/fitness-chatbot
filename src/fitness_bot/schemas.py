"""Validated HTTP contracts and iOS-compatible exercise records."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from pydantic.alias_generators import to_camel

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
ImageId = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
InputText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=4000)]
ExerciseId = Annotated[str, StringConstraints(min_length=1, max_length=64)]


class Exercise(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, frozen=True)

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


class TrainingContext(BaseModel):
    active_workout: WorkoutSnapshot | None = None
    recent_exercises: list[RecentExercise] = Field(default_factory=list, max_length=100)
    available_equipment: list[Annotated[str, StringConstraints(max_length=64)]] = Field(
        default_factory=list, max_length=30
    )


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

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
    kind: Literal["search", "recommendation", "chat"] = "search"
    query: str = Field(default="", max_length=200)
    body_part: str | None = Field(default=None, max_length=64)
    equipment: str | None = Field(default=None, max_length=64)
    target_muscle: str | None = Field(default=None, max_length=64)


class ExerciseCard(BaseModel):
    exercise: Exercise
    equipment_label: str
    body_part_label: str
    context: str
    can_add_to_active_workout: bool


class ProcessingStep(BaseModel):
    stage: Literal["understanding", "retrieving", "responding"]
    title: str
    summary: str
    data: dict = Field(default_factory=dict)


class ChatResponse(BaseModel):
    conversation_id: UUID
    result_id: UUID = Field(default_factory=uuid4)
    kind: Literal["search", "recommendation", "chat"]
    text: str
    cards: list[ExerciseCard]
    catalog_version: str
    trace: list[ProcessingStep] = Field(default_factory=list)
