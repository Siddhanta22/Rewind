"""Allowlist enforcement + risk classification.

Called from both the discovery tool functions (src/discovery/browser_tools.py)
and the replay engine (src/replay/engine.py) - the two places actions
actually touch a live page. One enforcement layer, reused by both, checked
right before anything real happens - not trusted to either caller.
"""

import fnmatch
from typing import Any
from urllib.parse import urlparse

from src.schema import Artifact

from .config import SafetyConfig


class SafetyViolation(Exception):
    pass


class ApprovalRequired(SafetyViolation):
    """A call needs a human's approval and there is no one to ask."""


def check_url_allowed(url: str, config: SafetyConfig) -> None:
    parsed = urlparse(url)
    if parsed.hostname not in config.allowed_domains:
        raise SafetyViolation(f"Domain not allowlisted: {parsed.hostname!r} (url={url!r})")
    if config.allowed_routes and not any(
        fnmatch.fnmatch(parsed.path, route) for route in config.allowed_routes
    ):
        raise SafetyViolation(f"Route not allowlisted: {parsed.path!r} (url={url!r})")


def check_action_allowed(action: str, config: SafetyConfig) -> None:
    if action not in config.allowed_actions:
        raise SafetyViolation(f"Action type not allowlisted: {action!r}")


def classify_risk(action: str, config: SafetyConfig) -> str:
    return "risky" if action in config.risky_actions else "safe"


def _over_limit(value: Any, limit: float) -> bool:
    try:
        return not float(value) <= limit  # written this way so NaN counts as over
    except (TypeError, ValueError):
        return True  # can't tell how big it is, so treat it as too big


def requires_human_confirmation(action: str, params: dict[str, Any], config: SafetyConfig) -> bool:
    if not config.risky_requires_confirmation:
        return False
    if classify_risk(action, config) != "risky":
        return False
    # "risky" alone isn't enough to require confirmation - there must also
    # be a concrete risk signal (a parameter over its approval threshold).
    # Without this, EVERY click (e.g. login's) would require confirmation.
    # Found via code review before wiring this in for real.
    return any(
        _over_limit(params[name], limit)
        for name, limit in config.approval_thresholds.items()
        if name in params
    )


def artifact_needs_approval(artifact: Artifact, params: dict[str, Any], config: SafetyConfig) -> bool:
    """Would running this artifact with these parameters need a human's
    approval? One answer shared by the engine, the CLI and the MCP server."""
    return any(requires_human_confirmation(s.action, params, config) for s in artifact.steps)
