"""Shared between replay and escalation - a safe, truncated text snapshot
of the current page for error/context reporting. Was duplicated
byte-for-byte in both places; consolidated for the same reason
build_selector was (src/locator_utils.py): two copies of the same logic
can quietly drift apart.
"""

from playwright.sync_api import Page


def safe_text_snippet(page: Page, max_chars: int = 500) -> str:
    try:
        return page.locator("body").inner_text()[:max_chars]
    except Exception:
        return ""
