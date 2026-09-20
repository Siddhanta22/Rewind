"""Artifact schema: the typed, versioned, agent-invocable capability contract.

Produced once by discovery (see src/discovery/compile.py), consumed on every
call by replay (see src/replay/engine.py). Decoupled from the raw discovery
transcript on purpose - see REPORT.md, "Artifact schema".
"""

from typing import Literal

from pydantic import BaseModel, Field


class TargetInfo(BaseModel):
    base_url: str
    description: str = ""


class Locator(BaseModel):
    """How to find one element on the page, plus why that choice is robust.

    strategy is tried first; fallback (if present) is tried if the primary
    strategy finds nothing. Chosen at discovery time by whichever strategy
    Claude's tool call actually resolved against - see REPORT.md,
    "Determinism & error handling" for why accessible-name isn't always
    available on legacy markup and id/structural fallbacks exist.
    """

    strategy: Literal["accessible_name", "id_attribute", "css", "structural"]
    value: str
    fallback: "Locator | None" = None
    reasoning: str


class Step(BaseModel):
    index: int
    action: Literal["navigate", "fill", "click", "select_option"]
    locator: Locator | None = None  # None only for "navigate"
    value: str | None = None  # literal, or a "{param_name}" placeholder
    description: str


class Column(BaseModel):
    name: str
    type: Literal["string", "number"] = "string"


class Extract(BaseModel):
    """Where on the result page an output value lives, so replay can read it
    without any capability-specific code.

    regex: `pattern` is matched against the page text; its first capture
      group is the value.
    table: `row_selector` matches one element per row (use it to leave out
      header and total rows, e.g. "tbody tr:has(a)"); `columns` names the
      row's cells in order. A row with fewer cells than columns, or a number
      cell that can't be read as a number, is skipped. Yields a list of
      {column name: value}.
    """

    kind: Literal["regex", "table"]
    pattern: str | None = None
    row_selector: str | None = None
    columns: list[Column] = Field(default_factory=list)


class ParamSpec(BaseModel):
    name: str
    type: Literal["string", "number", "boolean", "array"]
    required: bool = True
    description: str = ""
    extract: Extract | None = None  # outputs only; None falls back to the extractor registry


class Condition(BaseModel):
    """A checkable fact about the current page state - no LLM required."""

    kind: Literal["text_present", "element_visible", "url_matches"]
    text: str | None = None
    locator: Locator | None = None
    pattern: str | None = None
    description: str = ""


class OutcomeRule(BaseModel):
    """A recognized non-success terminal state (e.g. session expired).

    Not for business results like "loan denied" - those satisfy the normal
    checkpoint and are just typed output data. This is only for a run that
    lands somewhere else entirely, that discovery specifically identified
    as legitimate rather than broken.
    """

    outcome_id: str
    when: Condition
    description: str = ""


class Artifact(BaseModel):
    capability_id: str
    version: int = 1
    description: str = ""
    target: TargetInfo
    inputs: list[ParamSpec] = Field(default_factory=list)
    outputs: list[ParamSpec] = Field(default_factory=list)
    steps: list[Step] = Field(default_factory=list)
    checkpoint: list[Condition] = Field(default_factory=list)
    known_outcomes: list[OutcomeRule] = Field(default_factory=list)
    created_from_run: str = ""
