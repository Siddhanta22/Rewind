"""MCP server: publishes each recorded capability as a typed tool that any
MCP-capable AI agent can call, e.g. request_loan(loan_amount, down_payment).

    python -m src.mcp_server        (stdio transport)

The agent supplies only the capability's inputs. Login credentials stay in this
server's environment and are never exposed to the agent. Risky calls (see the
safety config) are refused instead of run, since no human is at a terminal to
confirm them. Raw page text never leaves this machine: what the agent gets back
names the failing step, while full details go to local run logs under runs/.
"""

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from mcp import types
from mcp.server import Server, ServerRequestContext
from mcp.server.stdio import stdio_server
from playwright.sync_api import sync_playwright

from src.observability.logger import EvidenceLogger
from src.replay.engine import replay
from src.replay.result import ReplayResult
from src.replay.validation import validate_params
from src.safety.config import SafetyConfig
from src.safety.enforcement import artifact_needs_approval
from src.schema import Artifact

ROOT = Path(__file__).resolve().parents[1]
SESSION_CAPABILITY = "login"
SAFETY = SafetyConfig()
JSON_TYPES = {"string": "string", "number": "number", "boolean": "boolean"}
ERROR_STATUSES = {"invalid_request", "hard_failure", "server_error"}

INSTRUCTIONS = (
    "Each tool replays a recorded browser workflow on a banking app. Results come back "
    "as JSON with a status: success (read outputs), known_outcome, hard_failure, "
    "invalid_request, or needs_human_approval (ask a person to confirm, then retry "
    "through a person-approved path). A denied loan is a normal success result, not a failure."
)


def load_artifacts() -> dict[str, Artifact]:
    paths = sorted((ROOT / "artifacts").glob("*.json"))
    artifacts = [Artifact.model_validate_json(p.read_text()) for p in paths]
    return {a.capability_id: a for a in artifacts}


def build_tools(artifacts: dict[str, Artifact]) -> list[types.Tool]:
    tools = []
    for capability_id, artifact in artifacts.items():
        if capability_id == SESSION_CAPABILITY:
            continue
        properties = {
            p.name: {"type": JSON_TYPES[p.type], "description": p.description}
            for p in artifact.inputs
        }
        returns = ", ".join(f"{o.name} ({o.description})" for o in artifact.outputs)
        tools.append(
            types.Tool(
                name=capability_id,
                description=f"{artifact.description} Returns: {returns}.",
                input_schema={
                    "type": "object",
                    "properties": properties,
                    "required": [p.name for p in artifact.inputs if p.required],
                    "additionalProperties": False,
                },
            )
        )
    return tools


def _payload(result: ReplayResult, stage: str, run_id: str) -> dict[str, Any]:
    payload: dict[str, Any] = {"status": result.status, "run_id": run_id}
    if result.status == "success":
        payload["outputs"] = result.outputs
    elif result.status == "known_outcome":
        payload["outcome_id"] = result.outcome_id
    else:
        detail = result.failure_detail
        payload["failed_at"] = {
            "capability": stage,
            "step_index": detail.step_index if detail else None,
            "expected": detail.expected if detail else None,
        }
    return payload


def run_capability(
    artifacts: dict[str, Artifact], artifact: Artifact, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Blocking: log in, then replay the capability, in a fresh headless browser."""
    username = os.environ.get("PARABANK_USERNAME")
    password = os.environ.get("PARABANK_PASSWORD")
    if not (username and password):
        return {"status": "server_error", "detail": "Server has no login credentials configured."}
    login = artifacts.get(SESSION_CAPABILITY)
    if login is None:
        return {"status": "server_error", "detail": "No login artifact is installed."}

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    run_id = f"mcp_{artifact.capability_id}_{stamp}"
    evidence = EvidenceLogger(run_id, "replay", SAFETY, evidence_root=ROOT / "runs")
    calls = [
        (login, {"username": username, "password": password}),
        (artifact, {name: str(value) for name, value in arguments.items()}),
    ]

    result, stage = None, SESSION_CAPABILITY
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_context().new_page()
            for step_artifact, params in calls:
                stage = step_artifact.capability_id
                result = replay(page, step_artifact, params, safety=SAFETY, evidence=evidence)
                if result.status != "success":
                    break
        finally:
            browser.close()
    return _payload(result, stage, run_id)


async def _handle_call(
    artifacts: dict[str, Artifact], lock: asyncio.Lock, name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    artifact = artifacts.get(name)
    if artifact is None or name == SESSION_CAPABILITY:
        return {"status": "invalid_request", "detail": f"Unknown capability '{name}'."}
    if problem := validate_params(artifact, arguments):
        return {"status": "invalid_request", "detail": problem}
    if artifact_needs_approval(artifact, arguments, SAFETY):
        return {
            "status": "needs_human_approval",
            "detail": "This request is above the auto-approval limit and was not run.",
        }
    async with lock:  # one browser session at a time; Playwright's sync API needs its own thread
        return await asyncio.to_thread(run_capability, artifacts, artifact, arguments)


def build_server(artifacts: dict[str, Artifact] | None = None) -> Server:
    artifacts = load_artifacts() if artifacts is None else artifacts
    lock = asyncio.Lock()

    async def on_list_tools(
        ctx: ServerRequestContext, params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        return types.ListToolsResult(tools=build_tools(artifacts))

    async def on_call_tool(
        ctx: ServerRequestContext, params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        payload = await _handle_call(artifacts, lock, params.name, params.arguments or {})
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(payload))],
            is_error=payload["status"] in ERROR_STATUSES,
        )

    return Server(
        "rewind",
        instructions=INSTRUCTIONS,
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )


async def main() -> None:
    server = build_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    load_dotenv(ROOT / ".env")
    asyncio.run(main())
