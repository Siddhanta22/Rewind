"""Output extraction. An output declared with an `extract` rule in the
artifact (see schema.Extract) is read by the generic code below. Outputs
without one fall back to a capability-keyed registry of plain functions,
which is how request_loan still works until it is migrated.
"""

import re
from typing import Callable

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from src.schema import Extract, ParamSpec


def _extract_request_loan(page: Page) -> dict:
    text = page.locator("body").inner_text()
    outputs: dict[str, str] = {}

    status_match = re.search(r"Status:\s*(Approved|Denied)", text)
    if status_match:
        outputs["status"] = status_match.group(1)

    account_match = re.search(r"new account number:\s*(\d+)", text, re.IGNORECASE)
    if account_match:
        outputs["new_account_number"] = account_match.group(1)

    return outputs


def _extract_login(page: Page) -> dict:
    return {}  # login declares no outputs - nothing to extract


EXTRACTORS: dict[str, Callable[[Page], dict]] = {
    "request_loan": _extract_request_loan,
    "login": _extract_login,
}


TABLE_WAIT_MS = 3000  # rows on legacy pages often arrive by AJAX after the page "loads"


def _to_number(text: str) -> float | None:
    cleaned = re.sub(r"[^\d.\-]", "", text)
    try:
        return float(cleaned)
    except ValueError:
        return None


def _coerce(text: str, kind: str) -> object | None:
    return _to_number(text) if kind == "number" else text.strip()


def _extract_regex(page: Page, extract: Extract, kind: str) -> object | None:
    match = re.search(extract.pattern, page.locator("body").inner_text())
    return _coerce(match.group(1), kind) if match else None


def _extract_table(page: Page, extract: Extract) -> list[dict] | None:
    rows = page.locator(extract.row_selector)
    try:
        rows.first.wait_for(timeout=TABLE_WAIT_MS)
    except PlaywrightTimeoutError:
        return None
    cells_per_row = rows.evaluate_all(
        "rows => rows.map(r => [...r.querySelectorAll('td')].map(c => c.innerText))"
    )
    items = []
    for cells in cells_per_row:
        if len(cells) < len(extract.columns):
            continue
        item = {col.name: _coerce(text, col.type) for col, text in zip(extract.columns, cells)}
        if None not in item.values():
            items.append(item)
    return items or None


def _extract_one(page: Page, spec: ParamSpec) -> object | None:
    if spec.extract.kind == "table":
        return _extract_table(page, spec.extract)
    return _extract_regex(page, spec.extract, spec.type)


def extract_outputs(capability_id: str, page: Page, output_specs: list[ParamSpec]) -> dict:
    """Read each declared output. An output with an `extract` rule in the
    artifact is read from that; the rest fall back to the per-capability
    registry above."""
    registry = EXTRACTORS.get(capability_id)
    fallback = registry(page) if registry and any(s.extract is None for s in output_specs) else {}

    result: dict[str, object] = {}
    for spec in output_specs:
        value = _extract_one(page, spec) if spec.extract else fallback.get(spec.name)
        if value is not None:
            result[spec.name] = value
        elif spec.required:
            raise ValueError(f"Required output '{spec.name}' not found on the result page")
    return result
