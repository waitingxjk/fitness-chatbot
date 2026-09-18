"""Private exercise definitions and the bounded expert authoring result."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Code = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
Step = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
DetailType = Literal["weightedReps", "bodyweightReps", "duration", "machineLevelReps"]
TrainingType = Literal["strength", "cardio", "mobility", "stretching", "other"]
DefinitionField = Literal[
    "name",
    "aliases",
    "detail_type",
    "body_part_code",
    "equipment_code",
    "target_muscle_code",
    "secondary_muscle_codes",
    "training_type",
    "instructions",
    "default_weight_unit",
]


class CustomExerciseSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name
    detail_type: DetailType
    aliases: list[Name] = Field(default_factory=list, max_length=10)
    body_part_code: Code = "unknown"
    equipment_code: Code = "unknown"
    target_muscle_code: Code = "unknown"
    secondary_muscle_codes: list[Code] = Field(default_factory=list, max_length=10)
    training_type: TrainingType = "other"
    instructions: list[Step] = Field(default_factory=list, max_length=10)
    default_weight_unit: Literal["kg", "lb"] | None = None
    inferred_fields: list[DefinitionField] = Field(default_factory=list, max_length=11)

    @model_validator(mode="after")
    def consistent_recording(self):
        if self.detail_type != "weightedReps" and self.default_weight_unit is not None:
            raise ValueError("只有重量＋次数动作可以设置默认重量单位。")
        if self.detail_type == "bodyweightReps" and self.equipment_code not in {
            "unknown",
            "body weight",
            "assisted",
            "stability ball",
            "bosu ball",
            "roller",
        }:
            raise ValueError("自重记录方式与器械不一致，请确认记录方式。")
        return self


class CreateCustomExercise(BaseModel):
    model_config = ConfigDict(extra="forbid")
    exercise: CustomExerciseSpec


class UpdateCustomExercise(CreateCustomExercise):
    version: int = Field(ge=1)


class ExerciseNameCorrection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_name: Name
    resolved_name: Name


class CustomExerciseDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["ready", "needs_input", "existing"]
    creation_requested: bool = False
    name_correction: ExerciseNameCorrection | None = None
    exercise: CustomExerciseSpec | None = None
    existing_exercise_id: str | None = Field(default=None, max_length=64)
    question: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def complete_result(self):
        if self.status == "ready" and self.exercise is None:
            raise ValueError("Ready authoring result requires an exercise")
        if self.status == "existing" and not self.existing_exercise_id:
            raise ValueError("Existing result requires a catalog ID")
        if self.status == "needs_input" and not (self.question or "").strip():
            raise ValueError("Clarification requires a question")
        return self


class SaveCustomExerciseDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(default=1, ge=1)


class ExerciseNameResolution(BaseModel):
    """Resolve exercise identity before filling metadata or looking up catalog IDs."""

    model_config = ConfigDict(extra="forbid")
    status: Literal["resolved", "needs_input"]
    input_name: Name | None = None
    name: Name | None = None
    is_correction: bool = False
    discarded_names: list[Name] = Field(default_factory=list, max_length=10)
    question: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def complete_resolution(self):
        if self.is_correction and not self.input_name:
            raise ValueError("Name correction requires the original name")
        if self.status == "resolved" and not self.name:
            raise ValueError("Resolved exercise requires a name")
        if self.status == "needs_input" and not self.question:
            raise ValueError("Unresolved name requires a clarification")
        return self


class ExistingExerciseMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    same_exercise: bool = Field(
        description="Whether candidate is the same movement, not just a similar muscle or purpose"
    )
