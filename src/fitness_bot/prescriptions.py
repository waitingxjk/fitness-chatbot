"""Shared prescription contract; planned targets never imply completed activity."""

import re
from copy import deepcopy
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SetDose(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["warmup", "working", "activity"] = "working"
    repetitions: int | None = Field(default=None, ge=1, le=30)
    duration_seconds: int | None = Field(default=None, ge=10, le=600)
    rest_seconds: int = Field(default=90, ge=0, le=300)
    target_rir_min: int = Field(default=2, ge=2, le=6)
    target_rir_max: int = Field(default=3, ge=2, le=6)

    @model_validator(mode="after")
    def valid_dose(self):
        if (self.repetitions is None) == (self.duration_seconds is None):
            raise ValueError("Exactly one of repetitions or duration_seconds required")
        if type(self) is SetDose and self.kind != "activity" and self.rest_seconds < 60:
            raise ValueError("Model strength sets require at least sixty seconds rest")
        if self.target_rir_max < self.target_rir_min:
            raise ValueError("RIR range must be ordered")
        return self


class PrescriptionLoad(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy: Literal[
        "trial", "history", "history_reference", "trial_increment", "user", "bodyweight", "none"
    ] = "trial"
    value: float | None = Field(default=None, gt=0, le=10000, allow_inf_nan=False)
    unit: Literal["kg", "lb", "level"] | None = None
    convention: (
        Literal[
            "total",
            "per_hand",
            "added",
            "assistance",
            "bodyweight",
            "machine_stack",
            "machine_level",
            "unknown",
        ]
        | None
    ) = None
    explanation: str = Field(default="", max_length=1000)
    observation_id: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def valid_load(self):
        if self.value is not None and self.unit is None:
            raise ValueError("A load value requires a unit")
        if self.strategy in {"trial", "none", "bodyweight"} and self.value is not None:
            raise ValueError("Unknown or bodyweight load cannot contain a numeric value")
        if (
            self.strategy in {"history", "history_reference", "trial_increment", "user"}
            and self.value is None
        ):
            raise ValueError("A specified load requires a value")
        if self.strategy == "history_reference" and not self.observation_id:
            raise ValueError("A historical reference requires an observation")
        return self


class SetPrescription(SetDose):
    # User-entered targets may be wider than the model's suggested dose bounds.
    repetitions: int | None = Field(default=None, ge=1, le=1000)
    duration_seconds: int | None = Field(default=None, ge=1, le=3600)
    rest_seconds: int = Field(default=90, ge=0, le=900)
    load: PrescriptionLoad = Field(default_factory=PrescriptionLoad)


def trial_load(exercise):
    if exercise.detail_type in {"bodyweightReps", "duration"}:
        return {
            "strategy": "bodyweight" if exercise.detail_type == "bodyweightReps" else "none",
            "value": None,
            "unit": None,
            "convention": "bodyweight" if exercise.detail_type == "bodyweightReps" else None,
            "explanation": "自重动作，无外加负重。"
            if exercise.detail_type == "bodyweightReps"
            else "按时长完成。",
        }
    return {
        "strategy": "trial",
        "value": None,
        "unit": "level"
        if exercise.detail_type == "machineLevelReps"
        else (exercise.default_weight_unit or "kg"),
        "convention": "machine_level" if exercise.detail_type == "machineLevelReps" else None,
        "explanation": "重量待试做确认，以动作控制和目标余力为准。",
    }


def validate_dose_type(rows, exercise):
    for row in rows:
        if (exercise.detail_type == "duration") != (row.get("duration_seconds") is not None):
            raise ValueError("Dose incompatible with catalog detail type")


def model_sets(item, exercise, load):
    doses = item.set_prescriptions or [
        SetDose(
            repetitions=item.repetitions,
            duration_seconds=item.duration_seconds,
            rest_seconds=item.rest_seconds,
            target_rir_min=item.target_rir_min,
            target_rir_max=item.target_rir_max,
        )
        for _ in range(item.sets)
    ]
    rows = []
    for dose in doses:
        chosen_load = load
        if dose.kind in {"warmup", "activity"} or exercise.detail_type in {
            "duration",
            "bodyweightReps",
        }:
            chosen_load = trial_load(exercise)
        rows.append({**dose.model_dump(), "load": deepcopy(chosen_load)})
    validate_dose_type(rows, exercise)
    return rows


def legacy_sets(item):
    """Expand old immutable snapshots without rewriting them."""
    if item.get("set_prescriptions") is not None:
        return deepcopy(item["set_prescriptions"])
    return [
        {
            "kind": "working",
            "repetitions": item.get("repetitions"),
            "duration_seconds": item.get("duration_seconds"),
            "rest_seconds": item.get("rest_seconds", 90),
            "target_rir_min": item.get("target_rir_min", 2),
            "target_rir_max": item.get("target_rir_max", 3),
            "load": deepcopy(
                item.get("load") or {"strategy": "trial", "value": None, "unit": None}
            ),
        }
        for _ in range(item.get("sets", 0))
    ]


def summarize_sets(rows, *, seconds_per_rep=4, switch_seconds=90, allow_activity=False):
    work = [row for row in rows if row["kind"] == "working"]
    if not work and not (allow_activity and rows and all(r["kind"] == "activity" for r in rows)):
        raise ValueError("At least one working set is required")
    dose_rows = work or rows
    return {
        "sets": len(work),
        "total_sets": len(rows),
        "repetitions": max(
            (r["repetitions"] for r in dose_rows if r.get("repetitions") is not None), default=None
        ),
        "duration_seconds": max(
            (r["duration_seconds"] for r in dose_rows if r.get("duration_seconds") is not None),
            default=None,
        ),
        "rest_seconds": max(row["rest_seconds"] for row in dose_rows),
        "target_rir_min": min(row["target_rir_min"] for row in dose_rows),
        "target_rir_max": max(row["target_rir_max"] for row in dose_rows),
        "estimated_seconds": sum(
            (r.get("duration_seconds") or r["repetitions"] * seconds_per_rep) for r in rows
        )
        + sum(r["rest_seconds"] for r in rows[:-1])
        + switch_seconds,
    }


def validate_user_sets(values, exercise, *, original_rows=(), allow_activity=False):
    if not 1 <= len(values) <= 12:
        raise ValueError("Each exercise needs 1–12 sets")
    rows = [SetPrescription.model_validate(row).model_dump() for row in values]
    validate_dose_type(rows, exercise)
    if not any(row["kind"] == "working" for row in rows) and not (
        allow_activity and all(row["kind"] == "activity" for row in rows)
    ):
        raise ValueError("At least one working set is required")
    original_references = [
        PrescriptionLoad.model_validate(row["load"]).model_dump()
        for row in original_rows
        if row.get("load", {}).get("strategy") == "history_reference"
    ]
    units = set()
    conventions = set()
    for row in rows:
        load = row["load"]
        detail_type = exercise.detail_type
        if detail_type in {"duration", "bodyweightReps"}:
            if load["value"] is not None or load["unit"] is not None:
                raise ValueError("Catalog dose type does not support load or unit")
            row["load"] = trial_load(exercise)
            continue
        allowed = {"level"} if detail_type == "machineLevelReps" else {"kg", "lb"}
        if load["unit"] not in allowed and not (load["value"] is None and load["unit"] is None):
            raise ValueError("Load unit incompatible with catalog detail type")
        preserved_reference = load["strategy"] == "history_reference"
        if preserved_reference and load not in original_references:
            raise ValueError("Historical reference must match the original plan load")
        if load["value"] is not None:
            if detail_type == "machineLevelReps" and not load["value"].is_integer():
                raise ValueError("Machine level must be a whole number")
            if load["convention"] in {None, "unknown"} and not preserved_reference:
                raise ValueError("Specify load convention before confirming a numeric load")
            if detail_type == "machineLevelReps" and load["convention"] != "machine_level":
                raise ValueError("Machine level requires machine_level convention")
            if detail_type == "weightedReps" and load["convention"] in {
                "machine_level",
                "bodyweight",
            }:
                raise ValueError("Load convention incompatible with weighted exercise")
            units.add(load["unit"])
            conventions.add(load["convention"])
            # Edits are user choices, never new evidence-backed machine recommendations.
            if not preserved_reference:
                load.update(
                    strategy="user",
                    observation_id=None,
                    explanation="用户调整的计划目标；完成后另行记录实际表现。",
                )
        else:
            row["load"] = trial_load(exercise)
    if len(units) > 1 or len(conventions) > 1:
        raise ValueError("All sets of one exercise must use the same load unit and convention")
    return rows


def validate_prescription_reason(text):
    if re.search(
        r"(?i)\d|kg|lb|公斤|千克|磅|%|％|百分之|"
        r"(?<![后第])[一二三四五六七八九十百]+\s*(组|分钟|秒)|[二三四五六七八九十百]+\s*次",
        text,
    ):
        raise ValueError("Dose/load narrative must use structured fields")
