"""Evaluates a single Condition against the current live page - used for
both the checkpoint (all must hold, AND) and known_outcomes (checked
individually, first match wins). See engine.py.
"""

import re

from playwright.sync_api import Page

from src.schema import Condition

from .locators import LocatorResolutionError, resolve_locator


def evaluate_condition(page: Page, condition: Condition, timeout_ms: int = 3000) -> bool:
    if condition.kind == "text_present":
        if not condition.text:
            return False
        try:
            body_text = page.locator("body").inner_text()
        except Exception:
            return False
        return condition.text in body_text

    if condition.kind == "element_visible":
        if condition.locator is None:
            return False
        try:
            resolve_locator(page, condition.locator, timeout_ms=timeout_ms)
            return True
        except LocatorResolutionError:
            return False

    if condition.kind == "url_matches":
        if not condition.pattern:
            return False
        return bool(re.search(condition.pattern, page.url))

    return False
