"""The four actions Claude's tool calls map to: navigate, click, fill,
select_option, plus read_state. Every method enforces safety first, then
executes via Playwright, then returns the new accessibility-tree snapshot
so the caller (the Claude loop) has its next observation. See REPORT.md,
"Architecture".
"""

from dataclasses import dataclass
from typing import Any

from playwright.sync_api import Page

from src.locator_utils import build_selector
from src.safety.config import SafetyConfig
from src.safety.enforcement import check_action_allowed, check_url_allowed

from .accessibility import snapshot_text


@dataclass
class ToolResult:
    ok: bool
    observation: str
    resolved_element_meta: dict[str, Any] | None = None


def _wait_for_settle(page: Page, timeout_ms: int = 5000) -> None:
    """Click/fill/select_option don't auto-wait for a resulting page
    navigation the way page.goto() does - without this, an observation
    taken right after a form-submitting click can capture a stale,
    mid-navigation page. Found via live testing: this caused Claude to
    wrongly conclude a successful login had failed and retry it twice."""
    try:
        page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except Exception:
        pass  # not every action triggers navigation - timing out here is fine


def _resolved_element_meta(page: Page, selector: str) -> dict[str, Any] | None:
    """Captured from the live DOM at execution time - not guessed by Claude.
    Used later by compile.py to build a deterministic locator fallback."""
    try:
        return page.locator(selector).first.evaluate(
            "el => ({tag: el.tagName, id: el.id || null, name: el.getAttribute('name')})"
        )
    except Exception:
        return None


class BrowserTools:
    def __init__(self, page: Page, secrets: dict[str, str], safety: SafetyConfig):
        self.page = page
        self.secrets = secrets
        self.safety = safety

    def _resolve_value(self, raw: str) -> str:
        # Secret tokens arrive as "{username}"/"{password}"; the real value
        # is substituted only here, at the last possible moment - it is
        # never sent back to Claude or logged. See src/safety/redact.py.
        if raw.startswith("{") and raw.endswith("}"):
            key = raw[1:-1]
            if key in self.secrets:
                return self.secrets[key]
        return raw

    def navigate(self, url: str) -> ToolResult:
        # A blocked or failed navigation must degrade gracefully like every
        # other tool method, not crash the run - a guardrail that crashes
        # the program instead of saying "no, try something else" defeats
        # its own purpose. Found via live testing (an uncaught
        # SafetyViolation crashed a real discovery run).
        try:
            check_url_allowed(url, self.safety)
            check_action_allowed("navigate", self.safety)
            self.page.goto(url)
        except Exception as e:
            return ToolResult(ok=False, observation=f"navigate blocked or failed: {e}")
        return ToolResult(ok=True, observation=snapshot_text(self.page))

    def click(self, strategy: str, value: str, reasoning: str = "") -> ToolResult:
        check_action_allowed("click", self.safety)
        selector = build_selector(strategy, value)
        try:
            self.page.locator(selector).first.click(timeout=3000)
        except Exception as e:
            return ToolResult(ok=False, observation=f"click failed: {e}")
        _wait_for_settle(self.page)
        return ToolResult(
            ok=True,
            observation=snapshot_text(self.page),
            resolved_element_meta=_resolved_element_meta(self.page, selector),
        )

    def fill(self, strategy: str, value: str, text: str, reasoning: str = "") -> ToolResult:
        check_action_allowed("fill", self.safety)
        selector = build_selector(strategy, value)
        resolved_text = self._resolve_value(text)
        try:
            self.page.locator(selector).first.fill(resolved_text, timeout=3000)
        except Exception as e:
            return ToolResult(ok=False, observation=f"fill failed: {e}")
        _wait_for_settle(self.page)
        return ToolResult(
            ok=True,
            observation=snapshot_text(self.page),
            resolved_element_meta=_resolved_element_meta(self.page, selector),
        )

    def select_option(self, strategy: str, value: str, option: str, reasoning: str = "") -> ToolResult:
        check_action_allowed("select_option", self.safety)
        selector = build_selector(strategy, value)
        try:
            self.page.locator(selector).first.select_option(option, timeout=3000)
        except Exception as e:
            return ToolResult(ok=False, observation=f"select_option failed: {e}")
        _wait_for_settle(self.page)
        return ToolResult(
            ok=True,
            observation=snapshot_text(self.page),
            resolved_element_meta=_resolved_element_meta(self.page, selector),
        )

    def read_state(self) -> ToolResult:
        return ToolResult(ok=True, observation=snapshot_text(self.page))
