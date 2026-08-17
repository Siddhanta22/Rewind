"""Output extraction - a capability-keyed registry of plain functions,
since ParamSpec (outputs) declares the shape of data but not where/how to
find it on the page. A real gap in the artifact schema, worked around here
rather than changing the frozen schema - see REPORT.md, "Cuts".
"""

import re
from typing import Callable

from playwright.sync_api import Page

from src.schema import ParamSpec


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


def extract_outputs(capability_id: str, page: Page, output_specs: list[ParamSpec]) -> dict:
    extractor = EXTRACTORS.get(capability_id)
    raw = extractor(page) if extractor else {}

    result: dict[str, object] = {}
    for spec in output_specs:
        if spec.name in raw:
            result[spec.name] = raw[spec.name]
        elif spec.required:
            raise ValueError(f"Required output '{spec.name}' not found on the result page")
    return result
