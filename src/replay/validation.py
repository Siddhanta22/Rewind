"""Checks a call's parameters against the inputs an artifact declares.

Shared by the MCP server and the replay CLI, so both reject the same bad
calls before any browser opens. Without it, a missing parameter isn't an
error: substitute() leaves the literal "{name}" in place and replay types
it into the form.
"""

import math
from typing import Any

from src.schema import Artifact


def _is_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            return False
    return isinstance(value, (int, float)) and math.isfinite(value)


def _is_boolean(value: Any) -> bool:
    # From the CLI every value is text, so accept "true"/"false" as well
    return isinstance(value, bool) or (isinstance(value, str) and value.lower() in ("true", "false"))


def validate_params(artifact: Artifact, params: dict[str, Any]) -> str | None:
    """Return a message describing the first problem, or None if the call is fine."""
    declared = {p.name: p for p in artifact.inputs}
    if unknown := sorted(set(params) - set(declared)):
        return f"Unknown parameter(s): {unknown}. Expected: {sorted(declared)}."
    if missing := [p.name for p in artifact.inputs if p.required and p.name not in params]:
        return f"Missing required parameter(s): {missing}."
    for name, value in params.items():
        kind = declared[name].type
        if kind == "number" and not _is_number(value):
            return f"Parameter '{name}' must be a number."
        if kind == "boolean" and not _is_boolean(value):
            return f"Parameter '{name}' must be true or false."
    return None
