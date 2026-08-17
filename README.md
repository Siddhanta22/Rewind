# Computer-Use Automation System

An LLM (Claude) drives a real banking demo app once to discover how to do a task, that
successful run gets compiled into a typed, versioned, replayable **artifact**, and a
separate deterministic **replay engine** executes that artifact on every future call with
zero LLM involvement. Built against [ParaBank](https://parabank.parasoft.com), a public
banking demo app built for automation testing.

See `REPORT.md` for the full design write-up (architecture, artifact schema, determinism,
safety, escalation, and what was cut).

## Setup

**1. Python environment**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

**2. Config (`.env`)**

Copy `.env.example` to `.env` and fill in:

```
ANTHROPIC_API_KEY=          # required for discovery only - replay never uses it
PARABANK_USERNAME=
PARABANK_PASSWORD=
```

You'll need your own ParaBank test account: go to
`https://parabank.parasoft.com/parabank/register.htm` and register one (any fake
name/address/SSN - it's a public demo app, never use real PII). Note your username,
password, and the account number shown on the "Accounts Overview" page after
registering.

**Note:** if you register a fresh account, its account number will differ from the one
baked into `src/discovery/tasks.py`'s `request_loan` task (`from_account_id="13455"`) -
update that value if you re-run discovery yourself. Replay doesn't have this issue, since
you supply `--param from_account_id=...` directly on the command line.

## Demo path

Run discovery once per capability (needs `ANTHROPIC_API_KEY`, drives a real LLM against the
live site, produces `artifacts/<capability>.json` + logs under `/evidence/`):

```bash
python -m src.discovery.run --capability login
python -m src.discovery.run --capability request_loan
```

Then replay the resulting artifacts (no LLM, no API key needed - this is the production
path an AI agent would actually call):

```bash
python -m src.replay.run --capability request_loan \
    --param loan_amount=1000 --param down_payment=500 --param from_account_id=<your account>
```

This logs in (using the `login` artifact) and submits the loan request (using the
`request_loan` artifact) against the same live browser session, and prints a structured
result:

```
call 0 (login): status=success outputs={} outcome_id=None
call 1 (request_loan): status=success outputs={'status': 'Denied'} outcome_id=None
```

Both commands default to a visible (non-headless) browser window, since that's what makes
human escalation possible if the run gets stuck or hits a risky action - pass `--headless`
to run without a window, and `--no-escalation` to disable pausing for a human entirely.

## Running without live services

`python -m src.replay.run` never calls the Anthropic API - it only needs the live ParaBank
site and Playwright. This is the concrete answer to "run without live services if
applicable": once artifacts exist, replay works with no LLM dependency at all.

## Running tests

```bash
python -m pytest tests/ -q
```

## Project structure

```
src/
  schema.py           artifact schema (Artifact, Step, Locator, Condition, ...)
  locator_utils.py     shared selector-building logic (discovery + replay)
  discovery/           the Claude tool-use loop, browser tools, compile-to-artifact
  replay/               deterministic execution: locators, checkpoint, extraction, engine
  safety/               allowlist, risk classification, redaction
  escalation/            human handoff primitive
  observability/         evidence logging
artifacts/            saved capability artifacts (JSON)
evidence/              real discovery + replay run logs, screenshots, example artifacts
```
