"""The MCP server's contract with an agent: which tools it advertises, how it
rejects bad calls, and what it refuses to run or return. The browser run itself
is stubbed, so nothing here touches the network."""

import asyncio
import json

import pytest
from mcp import Client

from src import mcp_server
from src.replay.result import FailureDetail, ReplayResult


def call(tool: str, arguments: dict):
    async def go():
        async with Client(mcp_server.build_server()) as client:
            return await client.call_tool(tool, arguments)

    result = asyncio.run(go())
    return result, json.loads(result.content[0].text)


@pytest.fixture
def browser_runs(monkeypatch):
    """Replace the real browser run; records what it was asked to do."""
    runs = []

    def fake(artifacts, artifact, arguments):
        runs.append((artifact.capability_id, arguments))
        return {"status": "success", "run_id": "test", "outputs": {"status": "Approved"}}

    monkeypatch.setattr(mcp_server, "run_capability", fake)
    return runs


def test_advertises_capabilities_but_not_login():
    async def go():
        async with Client(mcp_server.build_server()) as client:
            return await client.list_tools()

    tools = {t.name: t for t in asyncio.run(go()).tools}
    assert set(tools) == {"request_loan", "get_account_overview"}
    schema = tools["request_loan"].input_schema
    assert schema["properties"]["loan_amount"]["type"] == "number"
    assert "loan_amount" in schema["required"]
    overview = tools["get_account_overview"].input_schema
    assert overview["properties"] == {} and overview["required"] == []


def test_valid_call_runs_the_capability(browser_runs):
    result, payload = call(
        "request_loan", {"loan_amount": 100, "down_payment": 10, "from_account_id": "1"}
    )
    assert payload["status"] == "success"
    assert payload["outputs"] == {"status": "Approved"}
    assert not result.is_error
    assert browser_runs == [
        ("request_loan", {"loan_amount": 100, "down_payment": 10, "from_account_id": "1"})
    ]


def test_login_cannot_be_called_directly(browser_runs):
    result, payload = call("login", {"username": "a", "password": "b"})
    assert payload["status"] == "invalid_request"
    assert result.is_error
    assert browser_runs == []


@pytest.mark.parametrize(
    "arguments",
    [
        {"down_payment": 10, "from_account_id": "1"},  # missing loan_amount
        {"loan_amount": "lots", "down_payment": 10, "from_account_id": "1"},  # not a number
        {"loan_amount": 100, "down_payment": 10, "from_account_id": "1", "note": "x"},  # unknown
    ],
)
def test_bad_arguments_are_rejected_before_any_browser_run(browser_runs, arguments):
    result, payload = call("request_loan", arguments)
    assert payload["status"] == "invalid_request"
    assert result.is_error
    assert browser_runs == []


def test_large_loan_is_refused_not_run(browser_runs):
    result, payload = call(
        "request_loan", {"loan_amount": 50000, "down_payment": 500, "from_account_id": "1"}
    )
    assert payload["status"] == "needs_human_approval"
    assert browser_runs == []


def test_failure_payload_names_the_step_but_leaks_no_page_text():
    failure = ReplayResult(
        status="hard_failure",
        failure_detail=FailureDetail(
            step_index=2,
            expected="text_present: Loan Request Processed",
            observed_url="https://example.test/private",
            observed_text_snippet="Account 12345 balance $9,999",
        ),
    )
    payload = mcp_server._payload(failure, "request_loan", "run1")
    assert payload["failed_at"] == {
        "capability": "request_loan",
        "step_index": 2,
        "expected": "text_present: Loan Request Processed",
    }
    assert "12345" not in json.dumps(payload)
