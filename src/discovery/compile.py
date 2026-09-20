"""Distills a successful discovery run's raw action log into the clean,
typed, reusable Artifact that replay will execute forever after. Read the
raw log exactly once, here - never touched again after this. See
REPORT.md, "Artifact schema", and project memory for why filtering is
based on ok=True + dedup rather than Claude's self-reported step indices
(found ambiguous on a real run).
"""

from src.config import canonicalize
from src.schema import Artifact, Condition, Locator, OutcomeRule, ParamSpec, Step, TargetInfo

from .raw_log import RawActionLog, RawLogEntry
from .task import DiscoveryTask


def compile_artifact(
    raw_log: RawActionLog, task: DiscoveryTask, finish_payload: dict, run_id: str
) -> Artifact:
    entries = _successful_entries(raw_log)
    steps = [_build_step(i + 1, e, task) for i, e in enumerate(entries)]

    checkpoint = [Condition(**c) for c in finish_payload.get("checkpoint_conditions", [])]
    if not checkpoint:
        checkpoint = [
            Condition(
                kind="text_present",
                text=task.capability_id,
                description="Fallback checkpoint - discovery did not report one",
            )
        ]

    known_outcomes = [
        OutcomeRule(
            outcome_id=r["outcome_id"],
            when=Condition(**r["when"]),
            description=r.get("description", ""),
        )
        for r in finish_payload.get("known_outcome_rules", [])
    ]

    inputs = [
        ParamSpec(name=i.name, type=i.param_type, description=i.description) for i in task.inputs
    ]

    return Artifact(
        capability_id=task.capability_id,
        version=1,
        description=task.description,
        target=TargetInfo(base_url=canonicalize(task.target_url), description=task.description),
        inputs=inputs,
        outputs=task.outputs,
        steps=steps,
        checkpoint=checkpoint,
        known_outcomes=known_outcomes,
        created_from_run=run_id,
    )


def _successful_entries(raw_log: RawActionLog) -> list[RawLogEntry]:
    """Only entries that actually succeeded, keeping just the last attempt
    on the same element if it was acted on more than once (the "typed it
    wrong, corrected it" pattern). Deliberately does not use Claude's
    self-reported successful_step_indices."""
    ok_entries = [e for e in raw_log.entries if e.ok and e.tool != "read_state"]

    def identity(e: RawLogEntry) -> tuple:
        if e.resolved_element_meta:
            meta = e.resolved_element_meta
            return (meta.get("tag"), meta.get("id"), meta.get("name"))
        return (e.args.get("strategy"), e.args.get("value"))

    last_by_identity: dict[tuple, RawLogEntry] = {}
    order: list[tuple] = []
    for e in ok_entries:
        key = identity(e)
        if key not in last_by_identity:
            order.append(key)
        last_by_identity[key] = e  # overwrite - last attempt wins
    return [last_by_identity[key] for key in order]


def _build_step(index: int, entry: RawLogEntry, task: DiscoveryTask) -> Step:
    locator = None
    value = None

    if entry.tool in ("click", "fill", "select_option"):
        primary = Locator(
            strategy=entry.args["strategy"],
            value=entry.args["value"],
            reasoning=entry.reasoning or "(no reasoning captured)",
        )
        fallback = _synthesize_fallback(entry, primary)
        if fallback:
            primary.fallback = fallback
        locator = primary

    if entry.tool == "fill":
        value = _parameterize_value(entry.args["text"], task)
    elif entry.tool == "select_option":
        value = _parameterize_value(entry.args["option"], task)
    elif entry.tool == "navigate":
        url = entry.args.get("url")
        value = canonicalize(url) if url else url

    return Step(
        index=index,
        action=entry.tool,
        locator=locator,
        value=value,
        description=entry.reasoning or f"{entry.tool} action",
    )


def _synthesize_fallback(entry: RawLogEntry, primary: Locator) -> Locator | None:
    """Built deterministically from live DOM metadata captured at
    execution time (BrowserTools._resolved_element_meta) - not guessed
    after the fact."""
    meta = entry.resolved_element_meta
    if not meta:
        return None
    if meta.get("id") and primary.strategy != "id_attribute":
        return Locator(
            strategy="id_attribute",
            value=meta["id"],
            reasoning="Synthesized from the live DOM id attribute captured at discovery time",
        )
    if meta.get("name"):
        css_value = f'{(meta.get("tag") or "*").lower()}[name="{meta["name"]}"]'
        if css_value != primary.value:
            return Locator(
                strategy="css",
                value=css_value,
                reasoning="Synthesized from the live DOM name attribute captured at discovery time",
            )
    return None


def _parameterize_value(value: str, task: DiscoveryTask) -> str:
    for inp in task.inputs:
        if inp.secret:
            continue  # secret inputs already arrive as "{name}" tokens
        if value == inp.value:
            return f"{{{inp.name}}}"
    return value
