"""Structured AI output schemas (PRD section 9.2)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

FailureClass = Literal[
    "locator changed",
    "element moved",
    "element removed",
    "new popup or overlay",
    "flow changed",
    "timing or wait issue",
    "suspected real bug",
]


class Diagnosis(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    failure_class: FailureClass
    confidence: float = Field(ge=0.0, le=1.0)
    explanation: str
    target_description: str
    needs_visual: bool


class Fix(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    diff: str
    rationale: str = Field(max_length=1000)
