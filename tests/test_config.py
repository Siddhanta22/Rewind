"""PARABANK_BASE_URL decides which ParaBank instance gets driven, while
artifacts keep the canonical public URL. No network needed."""

import pytest

from src import config
from src.safety.config import SafetyConfig

LOCAL = "http://localhost:8080/parabank"


@pytest.fixture
def local(monkeypatch):
    monkeypatch.setenv("PARABANK_BASE_URL", LOCAL)


def test_defaults_to_the_public_site(monkeypatch):
    monkeypatch.delenv("PARABANK_BASE_URL", raising=False)
    assert config.base_url() == config.PUBLIC_BASE_URL
    assert config.app_url("overview.htm") == f"{config.PUBLIC_BASE_URL}/overview.htm"
    assert config.rebase(f"{config.PUBLIC_BASE_URL}/x.htm") == f"{config.PUBLIC_BASE_URL}/x.htm"


def test_a_blank_setting_means_the_public_site(monkeypatch):
    monkeypatch.setenv("PARABANK_BASE_URL", "")  # what a blank line in .env produces
    assert config.base_url() == config.PUBLIC_BASE_URL


def test_rebase_points_a_canonical_url_at_the_local_instance(local):
    assert config.rebase(f"{config.PUBLIC_BASE_URL}/overview.htm") == f"{LOCAL}/overview.htm"


def test_canonicalize_undoes_rebase(local):
    canonical = f"{config.PUBLIC_BASE_URL}/requestloan.htm"
    assert config.canonicalize(config.rebase(canonical)) == canonical


def test_urls_outside_the_app_are_left_alone(local):
    assert config.rebase("https://example.com/a") == "https://example.com/a"
    assert config.canonicalize("https://example.com/a") == "https://example.com/a"


def test_trailing_slash_in_the_setting_is_ignored(monkeypatch):
    monkeypatch.setenv("PARABANK_BASE_URL", LOCAL + "/")
    assert config.app_url("index.htm") == f"{LOCAL}/index.htm"


def test_allowlist_follows_the_configured_host(local):
    assert SafetyConfig().allowed_domains == ["localhost"]
