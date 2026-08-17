"""Discovery task blueprint - same role as schema.py, but for what a
discovery run needs to start, not what it produces. See tasks.py for the
actual filled-in login/request_loan instances.
"""

from typing import Literal

from pydantic import BaseModel, Field

from src.schema import ParamSpec


class DiscoveryInput(BaseModel):
    name: str
    value: str = ""  # ignored if secret=True; real value comes from env instead
    param_type: Literal["string", "number", "boolean"]
    secret: bool = False
    description: str = ""


class DiscoveryTask(BaseModel):
    capability_id: str
    description: str
    target_url: str
    goal_prompt: str  # the one field Claude actually reads as plain English
    inputs: list[DiscoveryInput] = Field(default_factory=list)
    outputs: list[ParamSpec] = Field(default_factory=list)
    max_steps: int = 25
    timeout_s: int = 180
