"""The concrete discovery tasks: login (composed before any account-services
capability) and the capabilities themselves. Real target URLs and example
values, verified against the live site. Each capability's output `extract`
rules are written here, by hand, against the real page markup. See
REPORT.md, "Architecture" for why login is separate.
"""

import os

from dotenv import load_dotenv

from src.config import app_url
from src.schema import Column, Extract, ParamSpec

from .task import DiscoveryInput, DiscoveryTask

# The demo app's database resets periodically, so the account number can't be
# hardcoded - it comes from .env (PARABANK_ACCOUNT_ID).
load_dotenv()

TASKS: dict[str, DiscoveryTask] = {
    "login": DiscoveryTask(
        capability_id="login",
        description="Log in to ParaBank so an authenticated session exists "
        "for other capabilities to run in.",
        target_url=app_url("index.htm"),
        goal_prompt=(
            "Log in to ParaBank using the given username and password, "
            "and confirm you have reached the logged-in account services area."
        ),
        inputs=[
            DiscoveryInput(name="username", param_type="string", secret=True,
                            description="ParaBank login username"),
            DiscoveryInput(name="password", param_type="string", secret=True,
                            description="ParaBank login password"),
        ],
        outputs=[],
        max_steps=10,
    ),
    "request_loan": DiscoveryTask(
        capability_id="request_loan",
        description="Submit a loan request and report approved/denied.",
        target_url=app_url("requestloan.htm"),
        goal_prompt=(
            "Submit a loan request for the given amount and down payment, "
            "using the given account. Report whether it was approved or denied."
        ),
        inputs=[
            DiscoveryInput(name="loan_amount", value="1000", param_type="number",
                            description="Requested loan amount, USD"),
            DiscoveryInput(name="down_payment", value="500", param_type="number",
                            description="Down payment amount, USD"),
            DiscoveryInput(name="from_account_id", value=os.environ.get("PARABANK_ACCOUNT_ID", ""), param_type="string",
                            description="Account to associate with the request"),
        ],
        outputs=[
            ParamSpec(name="status", type="string", description="Approved or Denied"),
            ParamSpec(name="new_account_number", type="string", required=False,
                        description="New loan account number, present only when approved"),
        ],
        max_steps=15,
    ),
    "get_account_overview": DiscoveryTask(
        capability_id="get_account_overview",
        description="Read every account with its balance and available amount, plus the total balance.",
        target_url=app_url("overview.htm"),
        goal_prompt=(
            "Open the accounts overview and confirm it shows the table of accounts "
            "with their balances and a total. This is read-only: do not click "
            "anything that changes data."
        ),
        inputs=[],
        outputs=[
            ParamSpec(
                name="accounts", type="array", description="Every account, one entry each",
                extract=Extract(
                    kind="table",
                    row_selector="#accountTable tbody tr:has(a)",
                    columns=[
                        Column(name="account_number"),
                        Column(name="balance", type="number"),
                        Column(name="available", type="number"),
                    ],
                ),
            ),
            ParamSpec(
                name="total_balance", type="number", description="Sum of all account balances, USD",
                extract=Extract(kind="regex", pattern=r"Total\s+\$([\d,.]+)"),
            ),
        ],
        max_steps=8,
    ),
}
