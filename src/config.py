"""Where the target app lives.

Artifacts always store the canonical public ParaBank URL, so they stay
portable. Which instance actually gets driven is a deployment setting:
leave PARABANK_BASE_URL unset for the public demo, or set it (for example
http://localhost:8080/parabank) to point discovery, replay and the
benchmark at a local copy. `rebase` maps canonical to configured at run
time; `canonicalize` maps back when an artifact is compiled.
"""

import os
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

PUBLIC_BASE_URL = "https://parabank.parasoft.com/parabank"

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def base_url() -> str:
    return os.environ.get("PARABANK_BASE_URL", PUBLIC_BASE_URL).rstrip("/")


def base_hostname() -> str | None:
    return urlparse(base_url()).hostname


def app_url(path: str) -> str:
    """A page on the configured instance, e.g. app_url("overview.htm")."""
    return f"{base_url()}/{path.lstrip('/')}"


def rebase(url: str) -> str:
    """Point a canonical (public-site) URL at the configured instance."""
    if url.startswith(PUBLIC_BASE_URL):
        return base_url() + url[len(PUBLIC_BASE_URL):]
    return url


def canonicalize(url: str) -> str:
    """Inverse of rebase, so compiled artifacts don't record a local URL."""
    if url.startswith(base_url()):
        return PUBLIC_BASE_URL + url[len(base_url()):]
    return url
