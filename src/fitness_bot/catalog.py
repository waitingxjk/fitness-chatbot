"""Local catalog: complete versioned JSON, exact filters and lexical name search."""

import hashlib
import json
from pathlib import Path

from pydantic import TypeAdapter

from fitness_bot.schemas import Exercise, SearchPlan

DATA_DIR = Path(__file__).parent / "data"


class Catalog:
    def __init__(self, exercises: list[Exercise], terms: list[dict]):
        self.exercises = exercises
        self.terms = terms
        self._by_id = {e.id: e for e in exercises}
        if not exercises or len(self._by_id) != len(exercises):
            raise ValueError("Catalog must be nonempty and have unique exercise IDs")
        versions = {e.catalog_version for e in exercises}
        if len(versions) != 1:
            raise ValueError("Catalog contains mixed versions")
        self.version = versions.pop()
        fields = {
            "bodyPart": "body_part_code",
            "equipment": "equipment_code",
            "muscle": "target_muscle_code",
        }
        filter_codes = {
            kind: {getattr(e, field) for e in exercises if e.is_active}
            for kind, field in fields.items()
        }
        # The full iOS terminology also describes secondary muscles; those are not
        # valid values for our exact primary-muscle filter.
        self.search_terms = [
            term for term in terms if term["code"] in filter_codes.get(term["type"], set())
        ]

    @classmethod
    def load(cls, directory: Path = DATA_DIR) -> "Catalog":
        manifest = json.loads((directory / "manifest.json").read_text())
        for filename, digest in manifest["sha256"].items():
            if hashlib.sha256((directory / filename).read_bytes()).hexdigest() != digest:
                raise ValueError(f"Catalog checksum mismatch: {filename}")
        exercises = TypeAdapter(list[Exercise]).validate_json(
            (directory / "exercises_zh_hans.json").read_bytes()
        )
        if len(exercises) != manifest["exercise_count"]:
            raise ValueError("Catalog count mismatch")
        return cls(exercises, json.loads((directory / "catalog_terms_zh_hans.json").read_text()))

    def with_personal(self, exercises: list[Exercise]) -> "Catalog":
        """Build an isolated view; never mutate the shared standard snapshot."""
        catalog = Catalog([e for e in self.exercises if e.source_kind == "standard"], self.terms)
        for exercise in exercises:
            if exercise.source_kind != "custom" or exercise.id in catalog._by_id:
                raise ValueError("Invalid personal catalog entry")
            catalog.exercises.append(exercise)
            catalog._by_id[exercise.id] = exercise
        return catalog

    def get(self, exercise_id: str) -> Exercise | None:
        return self._by_id.get(exercise_id)

    def label(self, kind: str, code: str) -> str:
        if code == "unknown":
            return "待补充"
        return next(
            (t["displayNameZhHans"] for t in self.terms if t["type"] == kind and t["code"] == code),
            code,
        )

    def code(self, kind: str, value: str) -> str:
        normalized = value.strip().casefold()
        return next(
            (
                t["code"]
                for t in self.terms
                if t["type"] == kind
                and normalized in {t["code"].casefold(), t["displayNameZhHans"].casefold()}
            ),
            normalized,
        )

    def search(self, plan: SearchPlan, limit: int = 20) -> list[Exercise]:
        query = plan.query.strip().casefold()
        filters = [
            ("bodyPart", "body_part_code", plan.body_part),
            ("equipment", "equipment_code", plan.equipment),
            ("muscle", "target_muscle_code", plan.target_muscle),
        ]
        if not query and not any(value for _, _, value in filters):
            return []
        matches = []
        for exercise in self.exercises:
            if not exercise.is_active:
                continue
            if any(
                value and getattr(exercise, attr) != self.code(kind, value)
                for kind, attr, value in filters
            ):
                continue
            names = [exercise.display_name_zh_hans, exercise.source_name, *exercise.aliases_zh_hans]
            names = [name.casefold() for name in names]
            if query and not any(query in name for name in names):
                continue
            matches.append((int(query in names), exercise))
        matches.sort(key=lambda pair: (-pair[0], pair[1].id))
        return [exercise for _, exercise in matches[:limit]]
