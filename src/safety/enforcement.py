"""Allowlist enforcement + risk classification.

Called from both the discovery tool functions (src/discovery/browser_tools.py)
and the replay engine (src/replay/engine.py) - the two places actions
actually touch a live page. One enforcement layer, reused by both, checked
right before anything real happens - not trusted to either caller.
"""

import fnmatch
from typing import Any
from urllib.parse import urlparse

from .config import SafetyConfig


class SafetyViolation(Exception):
    pass


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


def requires_human_confirmation(action: str, params: dict[str, Any], config: SafetyConfig) -> bool:
    if not config.risky_requires_confirmation:
        return False
    if classify_risk(action, config) != "risky":
        return False
    # "risky" alone isn't enough to require confirmation - there must also
    # be a concrete risk signal (currently: a loan_amount over threshold).
    # Without this, EVERY click (e.g. login's) would require confirmation,
    # since it has no loan_amount to fall under the auto-approve threshold.
    # Found via code review before wiring this in for real.
    loan_amount = params.get("loan_amount")
    if loan_amount is None:
        return False
    if config.max_auto_approve_loan_amount is not None:
        try:
            if float(loan_amount) <= config.max_auto_approve_loan_amount:
                return False
        except (TypeError, ValueError):
            pass
    return True
