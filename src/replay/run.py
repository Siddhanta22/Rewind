"""CLI entrypoint for replay. Just wiring - loads the saved artifact(s),
runs them deterministically via run_chain, prints the structured result.
No LLM/API key needed here at all - see REPORT.md, "Architecture", for why
that's the whole point.

Usage:
    python -m src.replay.run --capability login
    python -m src.replay.run --capability request_loan \\
        --param loan_amount=1000 --param down_payment=500 --param from_account_id=13455
"""

import argparse
import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

from src.observability.logger import EvidenceLogger
from src.safety.config import SafetyConfig
from src.schema import Artifact

from .engine import run_chain
from .validation import validate_params


def _parse_params(raw: list[str]) -> dict[str, str]:
    params = {}
    for item in raw or []:
        key, _, value = item.partition("=")
        params[key] = value
    return params


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Replay a saved capability artifact")
    parser.add_argument("--capability", required=True)
    parser.add_argument("--param", action="append", help="key=value, repeatable")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--no-escalation", action="store_true")
    args = parser.parse_args()
    params = _parse_params(args.param)

    login_artifact = Artifact.model_validate_json(open("artifacts/login.json").read())
    target_artifact = None
    if args.capability != "login":
        target_artifact = Artifact.model_validate_json(open(f"artifacts/{args.capability}.json").read())
        # Before the run log or the browser exist: a bad call should cost nothing
        if problem := validate_params(target_artifact, params):
            parser.error(f"{problem} Declared inputs: {[p.name for p in target_artifact.inputs]}")

    safety = SafetyConfig()
    run_id = f"replay_{args.capability}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
    evidence = EvidenceLogger(run_id=run_id, run_type="replay", safety=safety)

    login_params = {"username": os.environ["PARABANK_USERNAME"], "password": os.environ["PARABANK_PASSWORD"]}
    if target_artifact is None:
        calls = [(login_artifact, login_params)]
    else:
        calls = [(login_artifact, login_params), (target_artifact, params)]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless)
        page = browser.new_page()
        results = run_chain(
            page, calls, safety=safety, evidence=evidence, enable_escalation=not args.no_escalation
        )
        browser.close()

    for i, r in enumerate(results):
        print(f"call {i} ({calls[i][0].capability_id}): status={r.status} outputs={r.outputs} "
              f"outcome_id={r.outcome_id}")
        if r.failure_detail:
            print(f"  failure_detail: {r.failure_detail}")

    evidence.finalize({"results": [r.model_dump() for r in results]})


if __name__ == "__main__":
    main()
