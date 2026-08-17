"""The "observe" channel: render the live page's accessibility tree as text.

Fed to Claude each turn instead of raw HTML or a screenshot - see REPORT.md,
"Architecture" for why (works on legacy markup with no test ids, since roles
are inferred from plain HTML semantics, not developer-added attributes).
"""

from playwright.sync_api import Page

# ParaBank doesn't use one consistent content container across pages: the
# home/login page wraps its form in #loginPanel, while the account-services
# pages (register, open account, request loan, ...) wrap theirs in
# #rightPanel. Tried in order; first one present on the current page wins.
# Falls back to the full <body> (with header/nav/footer noise) for any page
# we haven't seen - narrower is a nice-to-have, not something to rely on.
CONTENT_SELECTORS = ["#loginPanel", "#rightPanel"]


def snapshot_text(page: Page) -> str:
    try:
        tree = _aria_tree_text(page)
    except Exception:
        # Fallback for older Playwright versions without aria_snapshot().
        tree = _render_legacy_tree(page.accessibility.snapshot())

    elements = _form_elements_summary(page)
    return f"{tree}\n\n{elements}" if elements else tree


def _aria_tree_text(page: Page) -> str:
    for selector in CONTENT_SELECTORS:
        loc = page.locator(selector)
        if loc.count() > 0:
            return loc.first.aria_snapshot()
    return page.locator("body").aria_snapshot()


def _form_elements_summary(page: Page) -> str:
    """Supplementary grounding the accessibility tree alone doesn't give:
    real id/name/type attributes for form controls. Needed because some
    elements (e.g. ParaBank's unlabeled textboxes - see REPORT.md,
    "Determinism & error handling") have no accessible name to reference,
    and the tree's role labels ("textbox", "paragraph") are NOT real HTML
    tags, so they can't be used to build a CSS/XPath locator on their own.
    """
    try:
        elements = page.eval_on_selector_all(
            "input, select, textarea, button",
            """els => els.map(el => ({
                tag: el.tagName.toLowerCase(),
                type: el.getAttribute('type'),
                id: el.id || null,
                name: el.getAttribute('name'),
            }))""",
        )
    except Exception:
        return ""
    if not elements:
        return ""
    lines = ["Form elements on this page (prefer id_attribute strategy using these):"]
    for el in elements:
        parts = [f"tag={el['tag']}"]
        if el.get("type"):
            parts.append(f"type={el['type']}")
        if el.get("id"):
            parts.append(f'id="{el["id"]}"')
        if el.get("name"):
            parts.append(f'name="{el["name"]}"')
        lines.append("  - " + ", ".join(parts))
    return "\n".join(lines)


def _render_legacy_tree(node: dict | None, depth: int = 0) -> str:
    if node is None:
        return ""
    role = node.get("role", "")
    name = node.get("name", "")
    line = "  " * depth + role
    if name:
        line += f' "{name}"'
    lines = [line]
    for child in node.get("children", []) or []:
        lines.append(_render_legacy_tree(child, depth + 1))
    return "\n".join(lines)
