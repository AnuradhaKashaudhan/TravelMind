"""Loader for packaged, explicitly versioned string prompt templates."""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib.resources import files


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    """A named and versioned format-string prompt."""

    name: str
    version: int
    template: str

    @classmethod
    def load(cls, name: str, version: int = 1) -> PromptTemplate:
        """Load a packaged ``name.vN.txt`` prompt."""
        if not re.fullmatch(r"[a-z0-9_]+", name) or version < 1:
            raise ValueError("Prompt name or version is invalid")
        filename = f"{name}.v{version}.txt"
        resource = files("app.llm.prompts").joinpath(filename)
        if not resource.is_file():
            raise FileNotFoundError(f"Prompt template not found: {filename}")
        return cls(name=name, version=version, template=resource.read_text("utf-8"))

    def render(self, **values: str) -> str:
        """Render the template, raising a useful error for missing variables."""
        try:
            return self.template.format_map(values)
        except KeyError as error:
            raise ValueError(f"Missing prompt variable: {error.args[0]}") from error
