"""Allowlist + risk-classification config.

Enforced at the execution layer (discovery's tool functions, and the replay
engine) - not inside Claude's reasoning - so it holds regardless of whether
an LLM or a fixed artifact requested the action. See REPORT.md, "Safety".
"""

import json
from pathlib import Path

from pydantic import BaseModel, Field

from src.config import base_hostname


class SafetyConfig(BaseModel):
    allowed_domains: list[str] = Field(default_factory=lambda: [base_hostname()])
    allowed_routes: list[str] = Field(default_factory=lambda: ["/parabank/*"])
    allowed_actions: list[str] = Field(
        default_factory=lambda: ["navigate", "fill", "click", "select_option", "read_state"]
    )
    risky_actions: list[str] = Field(default_factory=lambda: ["click"])
    risky_requires_confirmation: bool = True
    # A risky (click) step needs a human's approval when any of these
    # parameters is over its limit. Keyed by parameter name, so a new
    # capability's amount is one more entry (e.g. "amount": 1000). A value
    # that can't be read as a number counts as over the limit.
    approval_thresholds: dict[str, float] = Field(default_factory=lambda: {"loan_amount": 5000})
    secret_param_patterns: list[str] = Field(
        default_factory=lambda: ["password", "passwd", "secret", "token", "api_key", "ssn"]
    )


def load_safety_config(path: Path | None = None) -> SafetyConfig:
    if path is None or not path.exists():
        return SafetyConfig()
    return SafetyConfig.model_validate(json.loads(path.read_text()))
