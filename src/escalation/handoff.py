"""The human-takeover primitive: pause automation, let a human operate the
SAME live session directly, block until they signal resume, then hand
control back. Reused by discovery (stuck), replay (hard failure), and
risky-action confirmation - one real mechanism, three trigger points. See
REPORT.md, "Escalation & handoff".

Requires the browser to be launched non-headless - the human needs an
actual window to interact with.
"""

from typing import Literal

from playwright.sync_api import Page
from pydantic import BaseModel

from src.observability.logger import EvidenceLogger
from src.page_utils import safe_text_snippet


class InterventionContext(BaseModel):
    run_id: str
    capability_id: str
    mode: Literal["discovery", "replay"]
    reason: str
    step_index: int | None = None
    current_url: str
    text_snippet: str


class InterventionOutcome(BaseModel):
    resumed_at_url: str
    new_text_snippet: str
    page_changed: bool


def request_intervention(
    page: Page, context: InterventionContext, evidence: EvidenceLogger
) -> InterventionOutcome:
    evidence.save_screenshot(page, f"intervention_requested_{context.mode}")
    evidence.log_event("intervention_requested", **context.model_dump())

    print("\n" + "=" * 60)
    print("HUMAN INTERVENTION REQUESTED")
    print("=" * 60)
    print(f"run_id:      {context.run_id}")
    print(f"capability:  {context.capability_id}")
    print(f"mode:        {context.mode}")
    print(f"step_index:  {context.step_index}")
    print(f"reason:      {context.reason}")
    print(f"current url: {context.current_url}")
    print("-" * 60)
    print("The browser window is open and live - take whatever action is")
    print("needed directly in it, then return here.")
    print("=" * 60)
    input("Press Enter once you've resolved this and want automation to resume... ")

    new_url = page.url
    new_text = safe_text_snippet(page)
    evidence.log_event("intervention_resumed", resumed_at_url=new_url)

    return InterventionOutcome(
        resumed_at_url=new_url,
        new_text_snippet=new_text,
        page_changed=(new_url != context.current_url or new_text != context.text_snippet),
    )
