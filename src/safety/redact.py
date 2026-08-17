"""Redaction of what gets persisted (logs, evidence) - not what Claude sees.

Claude never receives real secret values in the first place (they arrive as
"{name}" placeholder tokens, resolved only inside BrowserTools at the moment
Playwright acts - see src/discovery/browser_tools.py). This module is the
defensive second layer for what ends up written to disk.
"""

from typing import Any

from .config import SafetyConfig


def redact_value(name: str, value: Any, config: SafetyConfig) -> Any:
    if not isinstance(value, str):
        return value
    lname = name.lower()
    if any(pattern in lname for pattern in config.secret_param_patterns):
        return "***REDACTED***"
    return value


def redact_dict(d: dict[str, Any], config: SafetyConfig) -> dict[str, Any]:
    """Recursive - a top-level key like "params" can hide a nested
    "password" key one level down, which a shallow pass would miss. Found
    via live testing: a real password leaked into evidence this way."""
    result: dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, dict):
            result[k] = redact_dict(v, config)
        elif isinstance(v, list):
            result[k] = [redact_dict(item, config) if isinstance(item, dict) else item for item in v]
        else:
            result[k] = redact_value(k, v, config)
    return result


def redact_text(text: str, literal_secrets: list[str]) -> str:
    for secret in literal_secrets:
        if secret:
            text = text.replace(secret, "***REDACTED***")
    return text


def redact_text_recursive(obj: Any, literal_secrets: list[str]) -> Any:
    """Scrubs known secret VALUES out of any string found, anywhere in a
    nested structure - catches leaks redact_dict can't, since a secret can
    appear as plain content inside another field (e.g. a typed password
    showing up inside an accessibility-tree observation string), not just
    under a suspiciously-named key."""
    if isinstance(obj, str):
        return redact_text(obj, literal_secrets)
    if isinstance(obj, dict):
        return {k: redact_text_recursive(v, literal_secrets) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_text_recursive(item, literal_secrets) for item in obj]
    return obj
