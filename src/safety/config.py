"""Allowlist + risk-classification config.

Enforced at the execution layer (discovery's tool functions, and the replay
engine) - not inside Claude's reasoning - so it holds regardless of whether
an LLM or a fixed artifact requested the action. See REPORT.md, "Safety".
"""

import json
from pathlib import Path

from pydantic import BaseModel, Field


class SafetyConfig(BaseModel):
    allowed_domains: list[str] = Field(default_factory=lambda: ["parabank.parasoft.com"])
    allowed_routes: list[str] = Field(default_factory=lambda: ["/parabank/*"])
    allowed_actions: list[str] = Field(
        default_factory=lambda: ["navigate", "fill", "click", "select_option", "read_state"]
    )
    risky_actions: list[str] = Field(default_factory=lambda: ["click"])
    risky_requires_confirmation: bool = True
    # Below this, a risky (click) step auto-proceeds without pausing for a
    # human; above it, request_intervention is called first.
    max_auto_approve_loan_amount: float | None = 5000
    secret_param_patterns: list[str] = Field(
        default_factory=lambda: ["password", "passwd", "secret", "token", "api_key", "ssn"]
    )


def load_safety_config(path: Path | None = None) -> SafetyConfig:
    if path is None or not path.exists():
        return SafetyConfig()
    return SafetyConfig.model_validate(json.loads(path.read_text()))
