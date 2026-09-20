"""Schema-driven output extraction, against local pages shaped like the
ParaBank accounts overview (header row, data rows, a short Total row).
No network needed."""

import pytest
from playwright.sync_api import sync_playwright

from src.replay.extractors import extract_outputs
from src.schema import Column, Extract, ParamSpec

OVERVIEW = """
<body><h1>Accounts Overview</h1>
<table id="accountTable">
  <thead><tr><th>Account</th><th>Balance*</th><th>Available Amount</th></tr></thead>
  <tbody>
    <tr><td><a href="activity.htm?id=14898">14898</a></td><td>$1,395.50</td><td>$1,395.50</td></tr>
    <tr><td><a href="activity.htm?id=15453">15453</a></td><td>$60.00</td><td>$55.25</td></tr>
    <tr><td align="right"><b>Total</b></td><td><b>$1,455.50</b></td><td>&nbsp;</td></tr>
  </tbody>
</table></body>
"""

ACCOUNTS = ParamSpec(
    name="accounts",
    type="array",
    extract=Extract(
        kind="table",
        row_selector="#accountTable tbody tr:has(a)",
        columns=[
            Column(name="account_number"),
            Column(name="balance", type="number"),
            Column(name="available", type="number"),
        ],
    ),
)
TOTAL = ParamSpec(
    name="total_balance",
    type="number",
    extract=Extract(kind="regex", pattern=r"Total\s+\$([\d,.]+)"),
)


@pytest.fixture
def page():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        yield browser.new_page()
        browser.close()


def test_table_rows_become_typed_records(page):
    page.set_content(OVERVIEW)
    out = extract_outputs("get_account_overview", page, [ACCOUNTS])
    assert out["accounts"] == [
        {"account_number": "14898", "balance": 1395.5, "available": 1395.5},
        {"account_number": "15453", "balance": 60.0, "available": 55.25},
    ]  # the Total row has no link, so it is not an account row


def test_regex_value_is_coerced_to_a_number(page):
    page.set_content(OVERVIEW)
    out = extract_outputs("get_account_overview", page, [TOTAL])
    assert out == {"total_balance": 1455.5}


def test_table_that_renders_late_is_waited_for(page):
    page.set_content(
        "<body><script>setTimeout(() => {"
        "document.body.innerHTML = '<table id=\"accountTable\"><tbody>"
        "<tr><td><a href=\"#\">1</a></td><td>$2.00</td><td>$2.00</td></tr></tbody></table>'"
        "}, 300)</script></body>"
    )
    out = extract_outputs("get_account_overview", page, [ACCOUNTS])
    assert out["accounts"][0]["balance"] == 2.0


def test_missing_required_output_raises(page):
    page.set_content("<body>Nothing here</body>")
    with pytest.raises(ValueError, match="accounts"):
        extract_outputs("get_account_overview", page, [ACCOUNTS])


def test_missing_optional_output_is_omitted(page):
    page.set_content(OVERVIEW)
    optional = TOTAL.model_copy(
        update={"required": False, "extract": Extract(kind="regex", pattern=r"Grand total (\d+)")}
    )
    assert extract_outputs("get_account_overview", page, [optional]) == {}


def test_outputs_without_a_rule_still_use_the_registry(page):
    page.set_content("<body>Status: Approved</body>")
    spec = ParamSpec(name="status", type="string")
    assert extract_outputs("request_loan", page, [spec]) == {"status": "Approved"}
