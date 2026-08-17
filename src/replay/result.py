"""The shape of what replay hands back to the calling agent - the concrete
three-way split (success / known_outcome / hard_failure) we designed
early on, made real. No logic here - engine.py fills these in.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field


class FailureDetail(BaseModel):
    step_index: int | None = None
    expected: str
    observed_url: str
    observed_text_snippet: str


class ReplayResult(BaseModel):
    status: Literal["success", "known_outcome", "hard_failure"]
    outputs: dict[str, Any] = Field(default_factory=dict)
    outcome_id: str | None = None
    failure_detail: FailureDetail | None = None
