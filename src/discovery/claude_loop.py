"""The Claude tool-use loop: observe -> decide -> act -> repeat, until
Claude calls finish() or a stop condition fires. See REPORT.md, "Architecture",
and the interface-ai-assignment project memory for why several of these
choices (form-element grounding, first-tool-call-only) came from live testing,
not just design on paper.
"""

import os
import time
from dataclasses import dataclass
from typing import Any

import anthropic
from playwright.sync_api import Page

from src.escalation.handoff import InterventionContext, request_intervention
from src.observability.logger import EvidenceLogger
from src.safety.config import SafetyConfig

from .browser_tools import BrowserTools
from .raw_log import RawActionLog, RawLogEntry
from .task import DiscoveryTask
from .tool_schema import TOOLS

MODEL = "claude-sonnet-5"


@dataclass
class LoopResult:
    status: str  # "success" | "stuck"
    finish_payload: dict[str, Any] | None
    steps_taken: int


def run_tool_loop(
    page: Page,
    task: DiscoveryTask,
    client: anthropic.Anthropic,
    raw_log: RawActionLog,
    evidence: EvidenceLogger,
    safety: SafetyConfig,
    enable_escalation: bool = False,
) -> LoopResult:
    secrets = {inp.name: _resolve_secret(inp.name) for inp in task.inputs if inp.secret}
    tools = BrowserTools(page, secrets, safety)
    # Register real secret values so evidence logging can scrub them from
    # anywhere they might appear as text (e.g. a typed password showing up
    # inside an accessibility-tree observation), not just under a
    # suspiciously-named field.
    for value in secrets.values():
        evidence.add_secret(value)

    system_prompt = _build_system_prompt(task)
    initial_observation = tools.read_state().observation
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": f"Current page:\n{initial_observation}"}
    ]

    start_time = time.monotonic()
    turn = 0
    # Gives a human exactly one chance to help per run, so a run that stays
    # stuck even after intervention doesn't pause forever.
    escalated = False

    while True:
        # Bounds every iteration, whether or not Claude actually acted this
        # turn - a text-only response still counts, so a run that never
        # calls a tool can't loop forever.
        if turn >= task.max_steps:
            evidence.log_event("loop_stopped", reason="max_steps_exceeded", steps=turn)
            return LoopResult(status="stuck", finish_payload=None, steps_taken=turn)
        if time.monotonic() - start_time > task.timeout_s:
            evidence.log_event("loop_stopped", reason="timeout", steps=turn)
            return LoopResult(status="stuck", finish_payload=None, steps_taken=turn)
        turn += 1

        response = client.messages.create(
            model=MODEL, max_tokens=1024, system=system_prompt, tools=TOOLS, messages=messages
        )

        tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
        if not tool_use_blocks:
            messages.append({"role": "assistant", "content": response.content})
            messages.append(
                {"role": "user", "content": "Please take an action using one of the available tools."}
            )
            continue

        # Only the FIRST tool call is executed - confirmed necessary via live
        # testing (Claude batched calls even when told not to). Extra blocks
        # are discarded, not executed.
        call = tool_use_blocks[0]
        discarded = len(tool_use_blocks) - 1
        if discarded:
            evidence.log_event("extra_tool_calls_discarded", count=discarded, kept=call.name)

        if call.name == "finish":
            evidence.log_event("finish", **call.input)
            status = call.input.get("status", "stuck")

            if status == "stuck" and enable_escalation and not escalated:
                context = InterventionContext(
                    run_id=evidence.run_id,
                    capability_id=task.capability_id,
                    mode="discovery",
                    reason=call.input.get("summary", "Claude reported being stuck"),
                    step_index=turn,
                    current_url=page.url,
                    text_snippet=tools.read_state().observation[:500],
                )
                request_intervention(page, context, evidence)
                escalated = True

                # Feed the human's help back in as this turn's tool_result,
                # then let the SAME loop continue - the literal "pause,
                # cede control, resume" the spec asks for, not just
                # pause-and-give-up.
                fresh_observation = tools.read_state().observation
                tool_result_blocks = [
                    {
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "content": "A human operator has intervened to help. Current page "
                        f"state:\n{fresh_observation}\nContinue toward the goal, or call "
                        "finish again if you are still stuck.",
                    }
                ]
                for block in tool_use_blocks:
                    if block.id != call.id:
                        tool_result_blocks.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": "Not executed - only one action is allowed per turn.",
                            }
                        )
                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user", "content": tool_result_blocks})
                continue

            return LoopResult(status=status, finish_payload=call.input, steps_taken=turn)

        result = _execute(tools, call.name, call.input)

        raw_log.append(
            RawLogEntry(
                index=turn,
                tool=call.name,
                args=call.input,
                reasoning=call.input.get("reasoning", ""),
                ok=result.ok,
                observation=result.observation,
                resolved_element_meta=result.resolved_element_meta,
            )
        )
        evidence.log_event("tool_call", tool=call.name, args=call.input, ok=result.ok)

        # Feed Claude's own tool call, and our tool result, back into the
        # conversation - the mechanic that makes this a real back-and-forth,
        # and why the message list grows every turn (see project memory,
        # "conversation history grows every turn" for the trade-off).
        #
        # The API requires a tool_result for EVERY tool_use block in the
        # previous message, not just the one we executed - found via live
        # testing (a BadRequestError otherwise). So discarded extra calls
        # still get a tool_result, explaining they weren't executed.
        tool_result_blocks = []
        for block in tool_use_blocks:
            if block.id == call.id:
                tool_result_blocks.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": result.observation}
                )
            else:
                tool_result_blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": "Not executed - only one action is allowed per turn. "
                        "Decide your next action based on the result of the one that was executed.",
                    }
                )
        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_result_blocks})


def _build_system_prompt(task: DiscoveryTask) -> str:
    input_lines = []
    for inp in task.inputs:
        if inp.secret:
            input_lines.append(
                f'- {inp.name}: use the literal token "{{{inp.name}}}" - never invent or guess the real value'
            )
        else:
            input_lines.append(f"- {inp.name} = {inp.value}")
    inputs_block = "\n".join(input_lines) if input_lines else "(none)"

    return f"""You are operating a real web application via browser tools to accomplish a goal.

Goal: {task.goal_prompt}

Available input values for this task:
{inputs_block}

Rules:
- Take exactly ONE action per turn. You will see the result before deciding the next action.
- Prefer the "id_attribute" locator strategy using the "name" or "id" shown in the
  "Form elements on this page" section, when present - it's a real, valid selector.
  Only use "accessible_name" when the element genuinely has one in the accessibility tree.
  Do NOT invent CSS/XPath selectors from accessibility-tree role labels
  (e.g. "textbox", "paragraph") - those are roles, not real HTML tags, and will fail.
- Every action must include a "reasoning" field explaining why that locator should
  reliably identify the element.
- When the goal is met, call finish with status="success", a summary, the indices of
  the actions that were actually part of the successful path, and the checkpoint
  condition(s) you observed on the final page that confirm success.
- If you cannot safely proceed (unclear what to do, blocked, unexpected state you
  don't recognize), call finish with status="stuck" and explain why in the summary.
"""


def _execute(tools: BrowserTools, name: str, args: dict[str, Any]):
    if name == "navigate":
        return tools.navigate(args["url"])
    if name == "click":
        return tools.click(args["strategy"], args["value"], args.get("reasoning", ""))
    if name == "fill":
        return tools.fill(args["strategy"], args["value"], args["text"], args.get("reasoning", ""))
    if name == "select_option":
        return tools.select_option(args["strategy"], args["value"], args["option"], args.get("reasoning", ""))
    if name == "read_state":
        return tools.read_state()
    raise ValueError(f"Unknown tool: {name}")


def _resolve_secret(name: str) -> str:
    env_var = f"PARABANK_{name.upper()}"
    value = os.environ.get(env_var)
    if not value:
        raise RuntimeError(f"Missing required secret env var: {env_var}")
    return value
