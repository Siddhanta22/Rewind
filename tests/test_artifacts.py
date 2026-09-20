"""Every artifact in artifacts/ must keep its promises. An input that no step
uses is a lie in the tool's contract: an agent can pass it and it silently
does nothing (request_loan once advertised from_account_id while every loan
was funded from whatever account the form listed first). No network needed."""

from pathlib import Path

import pytest

from src.schema import Artifact

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = {
    p.stem: Artifact.model_validate_json(p.read_text()) for p in sorted((ROOT / "artifacts").glob("*.json"))
}


@pytest.mark.parametrize("name", ARTIFACTS)
def test_every_declared_input_is_used_by_a_step(name):
    artifact = ARTIFACTS[name]
    used = {s.value for s in artifact.steps if s.value}
    unused = [p.name for p in artifact.inputs if "{" + p.name + "}" not in used]
    assert not unused, f"{name} declares inputs no step uses: {unused}"


@pytest.mark.parametrize("name", ARTIFACTS)
def test_every_placeholder_in_a_step_is_a_declared_input(name):
    artifact = ARTIFACTS[name]
    declared = {"{" + p.name + "}" for p in artifact.inputs}
    stray = [s.value for s in artifact.steps if s.value and s.value.startswith("{") and s.value not in declared]
    assert not stray, f"{name} has placeholders that are not declared inputs: {stray}"


def test_the_loan_step_that_picks_the_account_is_a_dropdown_selection():
    steps = ARTIFACTS["request_loan"].steps
    picks = [s for s in steps if s.value == "{from_account_id}"]
    assert [s.action for s in picks] == ["select_option"]
    click = next(s for s in steps if s.action == "click")
    assert picks[0].index < click.index  # the account is chosen before Apply Now
