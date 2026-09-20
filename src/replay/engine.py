"""The actual replay orchestrator: walks an artifact's steps with real
Playwright execution, no LLM, then classifies the result into exactly one
of success / known_outcome / hard_failure. See REPORT.md, "Determinism &
error handling".
"""

import time

from playwright.sync_api import Page

from src.config import rebase
from src.escalation.handoff import InterventionContext, request_intervention
from src.observability.logger import EvidenceLogger
from src.page_utils import safe_text_snippet
from src.safety.config import SafetyConfig
from src.safety.enforcement import (
    check_action_allowed,
    check_url_allowed,
    requires_human_confirmation,
)
from src.schema import Artifact, Step

from .checkpoint import evaluate_condition
from .extractors import extract_outputs
from .locators import resolve_locator
from .result import FailureDetail, ReplayResult
from .substitution import substitute

END_STATE_TIMEOUT_MS = 5000
POLL_INTERVAL_MS = 100
POLL_CONDITION_TIMEOUT_MS = 200


def replay(
    page: Page,
    artifact: Artifact,
    params: dict,
    *,
    safety: SafetyConfig,
    evidence: EvidenceLogger,
    enable_escalation: bool = False,
) -> ReplayResult:
    target_url = rebase(artifact.target.base_url)
    check_url_allowed(target_url, safety)
    # Register secret-looking param VALUES (by field name, e.g. "password")
    # so evidence scrubs them everywhere - deliberately not all params, so
    # legitimate business data (loan_amount, etc.) stays visible and useful
    # in evidence. ParamSpec has no per-field secret flag today (a
    # documented schema gap), so this infers from the same name patterns
    # redact_dict already uses.
    for key, value in params.items():
        if any(pattern in key.lower() for pattern in safety.secret_param_patterns):
            evidence.add_secret(str(value))
    evidence.log_event("replay_start", capability_id=artifact.capability_id, params=params)

    # Deterministic bootstrap, mirroring discovery: the artifact's steps
    # assume the page is already at the target - nothing else establishes
    # that, so replay must navigate there first, same as discovery does
    # before Claude's loop starts. Found missing via live testing.
    page.goto(target_url)

    # Gives a human exactly one chance to recover, from either trigger
    # point below (a failed step, or a failed checkpoint) - never both,
    # so a replay can't pause-and-wait forever.
    escalated = False

    for step in artifact.steps:
        if enable_escalation and step.action == "click" and requires_human_confirmation("click", params, safety):
            _escalate(
                page,
                evidence,
                artifact.capability_id,
                reason=f"Step {step.index}: risky action requires confirmation before proceeding "
                f"(params: {params})",
                step_index=step.index,
            )
        try:
            _execute_step(page, step, params, safety)
        except Exception as e:
            evidence.log_event("step_failed", step_index=step.index, error=str(e))
            evidence.save_screenshot(page, f"hard_failure_step_{step.index}")
            if enable_escalation and not escalated:
                _escalate(
                    page,
                    evidence,
                    artifact.capability_id,
                    reason=f"Step {step.index} ({step.action}) failed: {e}",
                    step_index=step.index,
                )
                escalated = True
                break  # human may have completed the rest manually - skip
                # remaining automated steps, go straight to the final check
            return ReplayResult(
                status="hard_failure",
                failure_detail=FailureDetail(
                    step_index=step.index,
                    expected=step.description,
                    observed_url=page.url,
                    observed_text_snippet=safe_text_snippet(page, max_chars=300),
                ),
            )
        evidence.log_event("step_ok", step_index=step.index, action=step.action)

    if not escalated:
        _wait_for_end_state(page, artifact)

    # Known outcomes checked before the checkpoint - a loose checkpoint
    # match could otherwise coincidentally overlap with a known-outcome page.
    for rule in artifact.known_outcomes:
        if evaluate_condition(page, rule.when):
            evidence.log_event("known_outcome", outcome_id=rule.outcome_id)
            return ReplayResult(status="known_outcome", outcome_id=rule.outcome_id)

    checkpoint_ok = all(evaluate_condition(page, c) for c in artifact.checkpoint)
    if not checkpoint_ok:
        if enable_escalation and not escalated:
            _escalate(
                page,
                evidence,
                artifact.capability_id,
                reason="Checkpoint not satisfied after all steps completed",
                step_index=None,
            )
            escalated = True
            checkpoint_ok = all(evaluate_condition(page, c) for c in artifact.checkpoint)

        if not checkpoint_ok:
            evidence.log_event("checkpoint_failed", escalated=escalated)
            evidence.save_screenshot(page, "checkpoint_failed")
            return ReplayResult(
                status="hard_failure",
                failure_detail=FailureDetail(
                    step_index=None,
                    expected="; ".join(c.description for c in artifact.checkpoint),
                    observed_url=page.url,
                    observed_text_snippet=safe_text_snippet(page, max_chars=300),
                ),
            )

    try:
        outputs = extract_outputs(artifact.capability_id, page, artifact.outputs)
    except ValueError as e:
        evidence.log_event("output_extraction_failed", error=str(e))
        return ReplayResult(
            status="hard_failure",
            failure_detail=FailureDetail(
                step_index=None,
                expected=str(e),
                observed_url=page.url,
                observed_text_snippet=safe_text_snippet(page, max_chars=300),
            ),
        )

    evidence.log_event("replay_success", outputs=outputs, escalated=escalated)
    return ReplayResult(status="success", outputs=outputs)


def _escalate(
    page: Page, evidence: EvidenceLogger, capability_id: str, *, reason: str, step_index: int | None
) -> None:
    context = InterventionContext(
        run_id=evidence.run_id,
        capability_id=capability_id,
        mode="replay",
        reason=reason,
        step_index=step_index,
        current_url=page.url,
        text_snippet=safe_text_snippet(page, max_chars=300),
    )
    request_intervention(page, context, evidence)


def run_chain(
    page: Page,
    calls: list[tuple[Artifact, dict]],
    *,
    safety: SafetyConfig,
    evidence: EvidenceLogger,
    enable_escalation: bool = False,
) -> list[ReplayResult]:
    """The composition mechanism for login -> request_loan: a loop over
    (artifact, params) pairs sharing one page, short-circuiting if any
    non-final call doesn't succeed."""
    results = []
    for artifact, params in calls:
        result = replay(page, artifact, params, safety=safety, evidence=evidence, enable_escalation=enable_escalation)
        results.append(result)
        if result.status != "success":
            break
    return results


def _execute_step(page: Page, step: Step, params: dict, safety: SafetyConfig) -> None:
    if step.action == "navigate":
        url = rebase(substitute(step.value, params))
        check_url_allowed(url, safety)
        page.goto(url)
        return

    if step.locator is None:
        raise ValueError(f"Step {step.index} ({step.action}) has no locator")
    pw_locator = resolve_locator(page, step.locator)

    if step.action == "click":
        check_action_allowed("click", safety)
        pw_locator.click(timeout=3000)
    elif step.action == "fill":
        check_action_allowed("fill", safety)
        pw_locator.fill(substitute(step.value, params), timeout=3000)
    elif step.action == "select_option":
        check_action_allowed("select_option", safety)
        pw_locator.select_option(substitute(step.value, params), timeout=3000)
    else:
        raise ValueError(f"Unknown step action: {step.action}")


def _wait_for_end_state(
    page: Page, artifact: Artifact, timeout_ms: int = END_STATE_TIMEOUT_MS
) -> bool:
    """Poll until the page shows something replay can classify: a known
    outcome, or every checkpoint condition. True if it got there in time.

    This replaces a fixed sleep. networkidle can return before an AJAX-driven
    update (e.g. ParaBank's "Apply Now") finishes rendering, and discovery
    never hit that because Claude's next API call added enough delay. Between
    steps no wait is needed: each step's locator waits for its own element.
    If the state never appears, the normal checks in replay() report the
    failure."""
    deadline = time.monotonic() + timeout_ms / 1000
    while True:
        outcome_seen = any(
            evaluate_condition(page, rule.when, timeout_ms=POLL_CONDITION_TIMEOUT_MS)
            for rule in artifact.known_outcomes
        )
        checkpoint_seen = all(
            evaluate_condition(page, c, timeout_ms=POLL_CONDITION_TIMEOUT_MS)
            for c in artifact.checkpoint
        )
        if outcome_seen or checkpoint_seen:
            return True
        if time.monotonic() >= deadline:
            return False
        page.wait_for_timeout(POLL_INTERVAL_MS)
