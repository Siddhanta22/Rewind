"""A call's parameters are checked against the artifact's declared inputs
before any browser opens, on both the MCP and the terminal path. No network
needed."""

import sys
from pathlib import Path

import pytest

from src.replay import run
from src.replay.validation import validate_params
from src.schema import Artifact, ParamSpec, TargetInfo

ROOT = Path(__file__).resolve().parents[1]

ARTIFACT = Artifact(
    capability_id="demo",
    target=TargetInfo(base_url="http://localhost/"),
    inputs=[
        ParamSpec(name="amount", type="number"),
        ParamSpec(name="note", type="string", required=False),
        ParamSpec(name="urgent", type="boolean", required=False),
    ],
)


def test_a_valid_call_passes():
    assert validate_params(ARTIFACT, {"amount": 200, "note": "hi"}) is None


def test_numbers_may_arrive_as_text_from_the_cli():
    assert validate_params(ARTIFACT, {"amount": "200.50"}) is None


def test_optional_inputs_can_be_left_out():
    assert validate_params(ARTIFACT, {"amount": 1}) is None


def test_a_missing_required_parameter_is_named():
    assert "amount" in validate_params(ARTIFACT, {"note": "hi"})


def test_an_unknown_parameter_is_rejected():
    assert "bogus" in validate_params(ARTIFACT, {"amount": 1, "bogus": 2})


@pytest.mark.parametrize("bad", ["lots", "", "nan", "inf", True, None])
def test_a_number_input_rejects_things_that_are_not_finite_numbers(bad):
    assert "must be a number" in validate_params(ARTIFACT, {"amount": bad})


@pytest.mark.parametrize("good", [True, False, "true", "False"])
def test_boolean_input_accepts_real_booleans_and_their_text_form(good):
    assert validate_params(ARTIFACT, {"amount": 1, "urgent": good}) is None


def test_boolean_input_rejects_other_text():
    assert "true or false" in validate_params(ARTIFACT, {"amount": 1, "urgent": "yes"})


def test_the_cli_rejects_a_missing_parameter_before_any_browser_or_log(monkeypatch, capsys):
    def must_not_run(*args, **kwargs):
        raise AssertionError("a bad call must not get as far as the browser or the run log")

    monkeypatch.chdir(ROOT)
    monkeypatch.setattr(run, "sync_playwright", must_not_run)
    monkeypatch.setattr(run, "EvidenceLogger", must_not_run)
    monkeypatch.setattr(
        sys, "argv", ["run", "--capability", "request_loan", "--param", "down_payment=10"]
    )

    with pytest.raises(SystemExit) as exit_info:
        run.main()

    assert exit_info.value.code == 2
    err = capsys.readouterr().err
    assert "loan_amount" in err and "Declared inputs" in err
