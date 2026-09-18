"""Fixed, packaged expert instructions; no discovery, plugins, or executable skills."""

from importlib import resources
from types import MappingProxyType

_SKILL_PATHS = MappingProxyType(
    {
        "exercise-authoring": ("exercise-authoring", "SKILL.md"),
        "training-review": ("training-review", "SKILL.md"),
        "training-recommendation": ("training-recommendation", "SKILL.md"),
    }
)


def load_skill(name: str) -> str:
    """Read one server-selected expert resource, rejecting arbitrary names and paths."""
    if name not in _SKILL_PATHS:
        raise ValueError(f"Unknown expert skill: {name}")
    return resources.files("fitness_bot.skills").joinpath(*_SKILL_PATHS[name]).read_text("utf-8")
