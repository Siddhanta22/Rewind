"""Benchmark: discovery (LLM in the loop) vs replay (no LLM) for request_loan.

    python -m src.benchmark --discoveries 3 --replays 20

Discovery numbers cover the Claude loop for one capability in an already
authenticated session. Replay numbers cover a fresh browser context per run:
login artifact, then request_loan artifact. Replay is run with the Anthropic
client instrumented, so "0 LLM calls" is measured, not assumed.
"""

import argparse
import json
import math
import os
import platform
import statistics
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

import anthropic
from anthropic.resources.messages import Messages
from dotenv import load_dotenv
from playwright.sync_api import Browser, Page, sync_playwright

from src.discovery.claude_loop import MODEL, run_tool_loop
from src.discovery.raw_log import RawActionLog
from src.discovery.tasks import TASKS
from src.observability.logger import EvidenceLogger
from src.replay.engine import replay
from src.safety.config import SafetyConfig
from src.schema import Artifact

ROOT = Path(__file__).resolve().parents[1]

# Claude Sonnet 5 list price, USD per 1M tokens (Claude API docs, checked 2026-06-24).
PRICE_IN_PER_M = 2.00
PRICE_OUT_PER_M = 10.00


class _LLMCallCounter:
    def __init__(self) -> None:
        self.count = 0

    def __enter__(self) -> "_LLMCallCounter":
        self._orig = Messages.create
        counter = self

        def counting(messages_self, *args, **kwargs):
            counter.count += 1
            return counter._orig(messages_self, *args, **kwargs)

        Messages.create = counting
        return self

    def __exit__(self, *exc) -> None:
        Messages.create = self._orig


def _login(page: Page) -> None:
    page.goto("https://parabank.parasoft.com/parabank/index.htm")
    page.fill('input[name="username"]', os.environ["PARABANK_USERNAME"])
    page.fill('input[name="password"]', os.environ["PARABANK_PASSWORD"])
    page.click('input[type="submit"]')
    page.wait_for_load_state("networkidle")


def _stats(values: list[float]) -> dict:
    xs = sorted(values)
    n = len(xs)
    return {
        "min": round(xs[0], 2),
        "median": round(statistics.median(xs), 2),
        "p95": round(xs[min(n - 1, math.ceil(0.95 * n) - 1)], 2),
        "max": round(xs[-1], 2),
        "mean": round(statistics.mean(xs), 2),
    }


def _cost(input_tokens: float, output_tokens: float) -> float:
    return input_tokens * PRICE_IN_PER_M / 1e6 + output_tokens * PRICE_OUT_PER_M / 1e6


def bench_discovery(browser: Browser, runs: int, loan_amount: str, down_payment: str,
                    tmp: Path, safety: SafetyConfig) -> list[dict]:
    task = TASKS["request_loan"].model_copy(deep=True)
    for inp in task.inputs:
        if inp.name == "loan_amount":
            inp.value = loan_amount
        elif inp.name == "down_payment":
            inp.value = down_payment
    client = anthropic.Anthropic()

    rows = []
    for i in range(runs):
        context = browser.new_context()
        page = context.new_page()
        _login(page)
        page.goto(task.target_url)
        evidence = EvidenceLogger(f"discovery_{i}", "discovery", safety, evidence_root=tmp)
        evidence.add_secret(os.environ["PARABANK_PASSWORD"])

        start = time.perf_counter()
        result = run_tool_loop(page, task, client, RawActionLog(), evidence, safety)
        seconds = time.perf_counter() - start
        context.close()

        rows.append({
            "status": result.status,
            "seconds": round(seconds, 2),
            "api_calls": result.usage.api_calls,
            "input_tokens": result.usage.input_tokens,
            "output_tokens": result.usage.output_tokens,
            "cost_usd": round(_cost(result.usage.input_tokens, result.usage.output_tokens), 4),
        })
        print(f"discovery {i + 1}/{runs}: {rows[-1]}")
        time.sleep(1)
    return rows


def bench_replay(browser: Browser, runs: int, params: dict, tmp: Path,
                 safety: SafetyConfig) -> tuple[list[dict], int]:
    login = Artifact.model_validate_json((ROOT / "artifacts/login.json").read_text())
    loan = Artifact.model_validate_json((ROOT / "artifacts/request_loan.json").read_text())
    creds = {"username": os.environ["PARABANK_USERNAME"], "password": os.environ["PARABANK_PASSWORD"]}

    rows = []
    with _LLMCallCounter() as counter:
        for i in range(runs):
            context = browser.new_context()
            page = context.new_page()
            evidence = EvidenceLogger(f"replay_{i}", "replay", safety, evidence_root=tmp)

            t0 = time.perf_counter()
            login_result = replay(page, login, creds, safety=safety, evidence=evidence)
            t1 = time.perf_counter()
            loan_result = None
            if login_result.status == "success":
                loan_result = replay(page, loan, params, safety=safety, evidence=evidence)
            t2 = time.perf_counter()
            context.close()

            rows.append({
                "login_status": login_result.status,
                "loan_status": loan_result.status if loan_result else "skipped",
                "loan_outcome": (loan_result.outputs.get("status") if loan_result else None),
                "login_seconds": round(t1 - t0, 2),
                "loan_seconds": round(t2 - t1, 2),
                "total_seconds": round(t2 - t0, 2),
            })
            print(f"replay {i + 1}/{runs}: {rows[-1]}")
            time.sleep(1)
        llm_calls = counter.count
    return rows, llm_calls


def _report(discovery: list[dict], replays: list[dict], llm_calls: int) -> dict:
    report: dict = {}
    if discovery:
        report["discovery"] = {
            "runs": len(discovery),
            "success_rate": sum(r["status"] == "success" for r in discovery) / len(discovery),
            "seconds": _stats([r["seconds"] for r in discovery]),
            "api_calls_mean": round(statistics.mean(r["api_calls"] for r in discovery), 1),
            "input_tokens_mean": round(statistics.mean(r["input_tokens"] for r in discovery)),
            "output_tokens_mean": round(statistics.mean(r["output_tokens"] for r in discovery)),
            "cost_usd_mean": round(statistics.mean(r["cost_usd"] for r in discovery), 4),
        }
    if replays:
        ok = [r for r in replays if r["login_status"] == "success" and r["loan_status"] == "success"]
        report["replay"] = {
            "runs": len(replays),
            "success_rate": len(ok) / len(replays),
            "total_seconds": _stats([r["total_seconds"] for r in replays]),
            "loan_seconds": _stats([r["loan_seconds"] for r in replays if r["loan_status"] != "skipped"]),
            "llm_api_calls_measured": llm_calls,
            "loan_outcomes": dict(Counter(r["loan_outcome"] for r in replays)),
            "cost_usd": 0.0,
        }
    return report


def _print_table(report: dict) -> None:
    d, r = report.get("discovery"), report.get("replay")
    print("\n| | Discovery (LLM loop) | Replay (no LLM) |")
    print("|---|---|---|")
    print(f"| Runs | {d['runs'] if d else '-'} | {r['runs'] if r else '-'} |")
    print(f"| Success rate | {d['success_rate']:.0%} | {r['success_rate']:.0%} |" if d and r else "")
    if d and r:
        print(f"| Median time (s) | {d['seconds']['median']} | {r['loan_seconds']['median']} (loan only), "
              f"{r['total_seconds']['median']} (login + loan) |")
        print(f"| p95 time (s) | - | {r['total_seconds']['p95']} (login + loan) |")
        print(f"| LLM API calls / run | {d['api_calls_mean']} | {r['llm_api_calls_measured']} (measured) |")
        print(f"| Tokens / run (in / out) | {d['input_tokens_mean']} / {d['output_tokens_mean']} | 0 / 0 |")
        print(f"| Est. cost / run | ${d['cost_usd_mean']} | $0 |")
        print(f"\nReplay loan outcomes: {r['loan_outcomes']}")


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Benchmark discovery vs replay")
    parser.add_argument("--discoveries", type=int, default=3)
    parser.add_argument("--replays", type=int, default=20)
    parser.add_argument("--loan-amount", default="100")
    parser.add_argument("--down-payment", default="10")
    parser.add_argument("--out", default=str(ROOT / "benchmarks" / "results.json"))
    args = parser.parse_args()

    safety = SafetyConfig()
    params = {
        "loan_amount": args.loan_amount,
        "down_payment": args.down_payment,
        "from_account_id": os.environ.get("PARABANK_ACCOUNT_ID", ""),
    }

    with tempfile.TemporaryDirectory() as tmp, sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        discovery = (
            bench_discovery(browser, args.discoveries, args.loan_amount, args.down_payment, Path(tmp), safety)
            if args.discoveries else []
        )
        replays, llm_calls = (
            bench_replay(browser, args.replays, params, Path(tmp), safety) if args.replays else ([], 0)
        )
        browser.close()

    report = _report(discovery, replays, llm_calls)
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "environment": {
            "python": platform.python_version(),
            "playwright": version("playwright"),
            "anthropic_sdk": version("anthropic"),
            "model": MODEL,
            "headless": True,
            "target": "parabank.parasoft.com (public demo app)",
        },
        "pricing_usd_per_million_tokens": {"input": PRICE_IN_PER_M, "output": PRICE_OUT_PER_M},
        "params": {"loan_amount": args.loan_amount, "down_payment": args.down_payment},
        "summary": report,
        "discovery_runs": discovery,
        "replay_runs": replays,
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=2) + "\n")
    _print_table(report)
    print(f"\nsaved: {args.out}")


if __name__ == "__main__":
    main()
