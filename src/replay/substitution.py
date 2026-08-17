"""Deliberately dumb parameter substitution - a plain string match-and-swap
of "{name}" tokens, no expression language. More power here means less
predictable replay, and determinism is the entire point. See REPORT.md,
"Determinism & error handling".
"""

from typing import Any


def substitute(value: str | None, params: dict[str, Any]) -> str | None:
    if value is None:
        return None
    if value.startswith("{") and value.endswith("}"):
        name = value[1:-1]
        if name in params:
            return str(params[name])
    return value
