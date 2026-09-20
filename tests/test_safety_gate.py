"""The approval gate: which calls need a human, and that a call needing one is
refused (never silently run) when there is no one to ask. No network needed."""

import sys
from pathlib import Path

import pytest

from src.observability.logger import EvidenceLogger
from src.replay import run
from src.replay.engine import replay
from src.safety.config import SafetyConfig
from src.safety.enforcement import (
    ApprovalRequired,
    SafetyViolation,
    artifact_needs_approval,
    requires_human_confirmation,
)
from src.schema import Artifact

ROOT = Path(__file__).resolve().parents[1]
CONFIG = SafetyConfig()
LOAN = Artifact.model_validate_json((ROOT / "artifacts/request_loan.json").read_text())
OVERVIEW = Artifact.model_validate_json((ROOT / "artifacts/get_account_overview.json").read_text())


@pytest.mark.parametrize("amount, expected", [(100, False), (5000, False), (5000.01, True), ("50000", True)])
def test_a_click_needs_approval_only_above_the_loan_limit(amount, expected):
    assert requires_human_confirmation("click", {"loan_amount": amount}, CONFIG) is expected


@pytest.mark.parametrize("unreadable", ["lots", "", None, "nan", "inf"])
def test_a_value_that_cannot_be_read_as_a_small_number_needs_approval(unreadable):
    assert requires_human_confirmation("click", {"loan_amount": unreadable}, CONFIG) is True


def test_actions_that_are_not_risky_never_need_approval():
    assert requires_human_confirmation("fill", {"loan_amount": 10**9}, CONFIG) is False


def test_parameters_without_a_threshold_are_ignored():
    assert requires_human_confirmation("click", {"note": "x" * 10, "down_payment": 10**9}, CONFIG) is False


def test_a_new_parameter_is_covered_by_adding_a_threshold():
    config = SafetyConfig(approval_thresholds={"amount": 100})
    assert requires_human_confirmation("click", {"amount": 101}, config) is True
    assert requires_human_confirmation("click", {"amount": 100}, config) is False
    assert requires_human_confirmation("click", {"loan_amount": 10**9}, config) is False


def test_an_artifact_needs_approval_only_if_it_has_a_risky_step():
    big = {"loan_amount": 50000, "down_payment": 20, "from_account_id": "1"}
    assert artifact_needs_approval(LOAN, big, CONFIG) is True
    assert artifact_needs_approval(LOAN, {**big, "loan_amount": 100}, CONFIG) is False
    assert artifact_needs_approval(OVERVIEW, big, CONFIG) is False  # read-only: no steps


def test_the_engine_refuses_before_touching_the_browser_when_it_cannot_ask(tmp_path):
    evidence = EvidenceLogger("gate", "replay", CONFIG, evidence_root=tmp_path)
    big = {"loan_amount": 50000, "down_payment": 20, "from_account_id": "1"}

    # page=None: any attempt to use the browser would raise AttributeError instead
    with pytest.raises(ApprovalRequired, match="no one to ask"):
        replay(None, LOAN, big, safety=CONFIG, evidence=evidence, enable_escalation=False)


def test_approval_required_is_a_safety_violation():
    assert issubclass(ApprovalRequired, SafetyViolation)


def test_the_cli_refuses_a_gated_call_when_escalation_is_off(monkeypatch, capsys):
    def must_not_run(*args, **kwargs):
        raise AssertionError("a refused call must not get as far as the browser or the run log")

    monkeypatch.chdir(ROOT)
    monkeypatch.setattr(run, "sync_playwright", must_not_run)
    monkeypatch.setattr(run, "EvidenceLogger", must_not_run)
    monkeypatch.setattr(
        sys,
        "argv",
        ["run", "--capability", "request_loan", "--param", "loan_amount=50000",
         "--param", "down_payment=20", "--param", "from_account_id=1", "--no-escalation"],
    )

    with pytest.raises(SystemExit) as exit_info:
        run.main()

    assert exit_info.value.code == 3
    assert "needs human approval" in capsys.readouterr().err
