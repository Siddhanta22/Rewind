"""The raw, unfiltered record of a discovery run - every tool call Claude
made and Playwright executed, in order, including any wrong turns.

Read exactly once, by compile.py, to produce the Artifact on success. Saved
to /evidence/ regardless of outcome, as a human-debuggable audit trail.
Never read by replay - see REPORT.md, "Architecture" for why the artifact
is deliberately decoupled from this.
"""

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class RawLogEntry(BaseModel):
    index: int
    tool: str
    args: dict[str, Any]
    reasoning: str = ""
    ok: bool
    observation: str
    resolved_element_meta: dict[str, Any] | None = None
    ts: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class RawActionLog(BaseModel):
    entries: list[RawLogEntry] = Field(default_factory=list)

    def append(self, entry: RawLogEntry) -> None:
        self.entries.append(entry)

    def to_json(self) -> dict:
        return self.model_dump()
