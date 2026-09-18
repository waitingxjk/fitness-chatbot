"""iOS-owned workout snapshots. Validation never changes training semantics."""

import math
import re
from datetime import UTC
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    JsonValue,
    model_validator,
)

Identifier = Annotated[str, Field(min_length=1, max_length=191, pattern=r"^[^\s/]+$")]
Version = Annotated[int, Field(strict=True, ge=1, le=9223372036854775807)]
Number = Annotated[float, Field(strict=True, allow_inf_nan=False)]
Integer = Annotated[int, Field(strict=True)]


def iso_string(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
        r"(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})",
        value,
    ):
        raise ValueError("Timestamp must be an ISO8601 string with timezone")
    return value


def normalize_utc(value):
    try:
        return value.astimezone(UTC)
    except OverflowError:
        raise ValueError("Timestamp is outside the supported UTC range") from None


Timestamp = Annotated[AwareDatetime, BeforeValidator(iso_string), AfterValidator(normalize_utc)]


def finite_json(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("JSON numbers must be finite")
    if isinstance(value, dict):
        for child in value.values():
            finite_json(child)
    elif isinstance(value, list):
        for child in value:
            finite_json(child)
    return value


class SnapshotModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkoutSet(SnapshotModel):
    id: Identifier
    exerciseRecordId: Identifier
    sequence: Integer
    loadType: Literal["none", "external", "bodyweight", "added", "assistance", "machineLevel"]
    loadValue: Number | None
    loadUnit: Literal["kg", "lb", "level"] | None
    repetitions: Integer | None
    durationSeconds: Integer | None
    difficulty: Literal["easy", "moderate", "hard"] | None
    rpe: Number | None
    isCompleted: Annotated[bool, Field(strict=True)]


class WorkoutRecord(SnapshotModel):
    id: Identifier
    workoutId: Identifier
    exerciseId: Identifier
    exerciseNameSnapshot: str
    sequence: Integer
    occurredAt: Timestamp
    detailType: Literal["weightedReps", "bodyweightReps", "duration", "machineLevelReps"]
    detailPayload: dict[str, JsonValue] | None
    feelingPayload: dict[str, JsonValue] | None
    isDraft: Annotated[bool, Field(strict=True)]
    version: Version
    sets: list[WorkoutSet]


class WorkoutSnapshot(SnapshotModel):
    id: Identifier
    title: str
    status: Literal["active", "paused", "completed"]
    startedAt: Timestamp
    pausedAt: Timestamp | None
    pausedDurationSeconds: Integer
    endedAt: Timestamp | None
    createdAt: Timestamp
    updatedAt: Timestamp
    records: list[WorkoutRecord]

    @model_validator(mode="after")
    def valid_links(self):
        record_ids, set_ids = set(), set()
        for record in self.records:
            if record.workoutId != self.id or record.id in record_ids:
                raise ValueError("Record must reference its workout and have a unique ID")
            record_ids.add(record.id)
            for item in record.sets:
                if item.exerciseRecordId != record.id or item.id in set_ids:
                    raise ValueError("Set must reference its record and have a unique ID")
                set_ids.add(item.id)
        return self


class WorkoutUpload(SnapshotModel):
    sourceId: Identifier
    version: Version
    deleted: Annotated[bool, Field(strict=True)]
    workout: WorkoutSnapshot | None

    @model_validator(mode="before")
    @classmethod
    def finite_input(cls, value):
        return finite_json(value)

    @model_validator(mode="after")
    def valid_deletion(self):
        if self.deleted != (self.workout is None):
            raise ValueError("workout must be null if and only if deleted is true")
        return self


class WorkoutReceipt(SnapshotModel):
    userId: str
    sourceId: str
    workoutId: str
    version: int
    uploadedAt: str


class StoredWorkout(WorkoutReceipt):
    deleted: bool
    workout: WorkoutSnapshot | None
