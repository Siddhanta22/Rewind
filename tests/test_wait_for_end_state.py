"""The end-of-run wait must return as soon as the expected page state
renders (not after a fixed sleep) and must give up at the timeout when it
never does. Runs against a local page, no network needed."""

import time

import pytest
from playwright.sync_api import sync_playwright

from src.replay.engine import _wait_for_end_state
from src.schema import Artifact, Condition, OutcomeRule, TargetInfo

ARTIFACT = Artifact(
    capability_id="test",
    target=TargetInfo(base_url="http://localhost/"),
    checkpoint=[Condition(kind="text_present", text="Loan Request Processed")],
    known_outcomes=[
        OutcomeRule(
            outcome_id="session_expired",
            when=Condition(kind="text_present", text="Customer Login"),
        )
    ],
)

RENDERS_LATER = (
    "<body>Working</body><script>"
    "setTimeout(() => { document.body.textContent = 'Loan Request Processed' }, 300)"
    "</script>"
)


@pytest.fixture
def page():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        yield browser.new_page()
        browser.close()


def test_returns_as_soon_as_the_checkpoint_renders(page):
    page.set_content(RENDERS_LATER)
    start = time.monotonic()
    assert _wait_for_end_state(page, ARTIFACT, timeout_ms=5000) is True
    assert time.monotonic() - start < 2.0


def test_recognizes_a_known_outcome(page):
    page.set_content("<body>Customer Login</body>")
    assert _wait_for_end_state(page, ARTIFACT, timeout_ms=5000) is True


def test_gives_up_at_the_timeout_when_nothing_renders(page):
    page.set_content("<body>Something unexpected</body>")
    start = time.monotonic()
    assert _wait_for_end_state(page, ARTIFACT, timeout_ms=600) is False
    assert time.monotonic() - start >= 0.55
