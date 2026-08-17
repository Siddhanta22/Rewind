"""Resolves a Step's Locator against a live page during replay - tries the
primary strategy, falls back through the chain if needed. Uses the same
build_selector as discovery's BrowserTools, so a locator discovery
validated resolves identically here. See REPORT.md, "Determinism & error
handling".
"""

from playwright.sync_api import Locator as PWLocator
from playwright.sync_api import Page

from src.locator_utils import build_selector
from src.schema import Locator


class LocatorResolutionError(Exception):
    def __init__(self, locator: Locator, attempts: list[str]):
        self.locator = locator
        self.attempts = attempts
        super().__init__(f"Could not resolve locator - tried: {attempts}")


def resolve_locator(page: Page, locator: Locator, timeout_ms: int = 3000) -> PWLocator:
    attempts: list[str] = []
    current: Locator | None = locator
    while current is not None:
        selector = build_selector(current.strategy, current.value)
        attempts.append(f"{current.strategy}:{current.value}")
        pw_locator = page.locator(selector).first
        try:
            pw_locator.wait_for(state="visible", timeout=timeout_ms)
            return pw_locator
        except Exception:
            current = current.fallback
    raise LocatorResolutionError(locator, attempts)
