"""CLI entrypoint for discovery. Just wiring - parses args, establishes a
session if needed, runs the real Claude tool-use loop, compiles and saves
the resulting artifact. All the actual decisions live in claude_loop.py /
compile.py, not here.

Usage:
    python -m src.discovery.run --capability login
    python -m src.discovery.run --capability request_loan
"""

import argparse
import os
from datetime import datetime, timezone

import anthropic
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

from src.config import app_url
from src.observability.logger import EvidenceLogger
from src.safety.config import SafetyConfig

from .claude_loop import run_tool_loop
from .compile import compile_artifact
from .raw_log import RawActionLog
from .tasks import TASKS


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Run discovery for a capability")
    parser.add_argument("--capability", required=True, choices=list(TASKS.keys()))
    parser.add_argument("--headless", action="store_true", help="Run without a visible browser window")
    parser.add_argument("--no-escalation", action="store_true", help="Disable human escalation on stuck")
    args = parser.parse_args()

    task = TASKS[args.capability]
    safety = SafetyConfig()
    raw_log = RawActionLog()
    run_id = f"discovery_{args.capability}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
    evidence = EvidenceLogger(run_id=run_id, run_type="discovery", safety=safety)
    client = anthropic.Anthropic()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless)
        page = browser.new_page()

        if args.capability != "login":
            # Login is a separate, composed capability (see REPORT.md,
            # "Architecture") - establish the session deterministically
            # first, so discovery for this capability starts already
            # authenticated, same as it will during real replay.
            evidence.add_secret(os.environ["PARABANK_PASSWORD"])
            page.goto(app_url("index.htm"))
            page.fill('input[name="username"]', os.environ["PARABANK_USERNAME"])
            page.fill('input[name="password"]', os.environ["PARABANK_PASSWORD"])
            page.click('input[type="submit"]')
            page.wait_for_load_state("networkidle")

        page.goto(task.target_url)  # deterministic bootstrap, not an LLM decision

        result = run_tool_loop(
            page, task, client, raw_log, evidence, safety, enable_escalation=not args.no_escalation
        )

        print(f"status: {result.status}")
        print(f"steps taken: {result.steps_taken}")
        if result.finish_payload:
            print(f"summary: {result.finish_payload.get('summary')}")

        evidence.save_raw_run(raw_log.to_json())
        evidence.finalize({"status": result.status, "finish_payload": result.finish_payload})

        if result.status == "success":
            artifact = compile_artifact(raw_log, task, result.finish_payload, run_id=run_id)
            out_path = f"artifacts/{args.capability}.json"
            with open(out_path, "w") as f:
                f.write(artifact.model_dump_json(indent=2))
            print(f"artifact saved: {out_path}")

        browser.close()


if __name__ == "__main__":
    main()
