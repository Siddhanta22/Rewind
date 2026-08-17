"""Sanity check: the Request Loan artifact we designed actually validates
against the Pydantic schema, and round-trips through JSON."""

from src.schema import (
    Artifact,
    Condition,
    Locator,
    OutcomeRule,
    ParamSpec,
    Step,
    TargetInfo,
)


def build_request_loan_artifact() -> Artifact:
    return Artifact(
        capability_id="request_loan",
        version=1,
        description="Submit a loan request and report approved/denied.",
        target=TargetInfo(
            base_url="https://parabank.parasoft.com/parabank/requestloan.htm",
            description="ParaBank - Request Loan page",
        ),
        inputs=[
            ParamSpec(name="loan_amount", type="number", description="Requested loan amount, USD"),
            ParamSpec(name="down_payment", type="number", description="Down payment amount, USD"),
            ParamSpec(name="from_account_id", type="string", description="Account to associate with the request"),
        ],
        outputs=[
            ParamSpec(name="status", type="string", description="Approved or Denied"),
            ParamSpec(
                name="new_account_number",
                type="string",
                required=False,
                description="New loan account number, present only when approved",
            ),
        ],
        steps=[
            Step(
                index=1,
                action="fill",
                locator=Locator(
                    strategy="id_attribute",
                    value="#amount",
                    reasoning="Accessible name unavailable (label not programmatically "
                    "associated); id is a stable, developer-assigned attribute",
                ),
                value="{loan_amount}",
                description="Enter requested loan amount",
            ),
            Step(
                index=2,
                action="fill",
                locator=Locator(
                    strategy="id_attribute",
                    value="#downPayment",
                    reasoning="Same label-association gap as #amount",
                ),
                value="{down_payment}",
                description="Enter down payment",
            ),
            Step(
                index=3,
                action="click",
                locator=Locator(
                    strategy="accessible_name",
                    value='role=button name="Apply Now"',
                    reasoning="Button text is rendered directly as the accessible name",
                ),
                value=None,
                description="Submit the loan request",
            ),
        ],
        checkpoint=[
            Condition(
                kind="text_present",
                text="Loan Request Processed",
                description="Confirms we reached the result page, not still on the form or an error page",
            ),
        ],
        known_outcomes=[
            OutcomeRule(
                outcome_id="session_expired",
                when=Condition(
                    kind="text_present",
                    text="Customer Login",
                    description="Bounced back to the login form instead of the result page",
                ),
                description="Session expired mid-flow; caller should re-authenticate and retry",
            ),
        ],
        created_from_run="run_0001",
    )


def test_artifact_round_trips_through_json():
    artifact = build_request_loan_artifact()
    json_str = artifact.model_dump_json(indent=2)
    reloaded = Artifact.model_validate_json(json_str)
    assert reloaded == artifact
    assert reloaded.steps[0].value == "{loan_amount}"
    assert reloaded.known_outcomes[0].outcome_id == "session_expired"


if __name__ == "__main__":
    artifact = build_request_loan_artifact()
    print(artifact.model_dump_json(indent=2))
