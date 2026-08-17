"""Shared between discovery and replay - turns a Locator strategy+value
into a real Playwright selector string. Deliberately a single
implementation used by both sides: a locator discovery validated must
resolve identically during replay, or determinism breaks down.
"""


def build_selector(strategy: str, value: str) -> str:
    if strategy == "id_attribute":
        # value is the bare id (e.g. "submitBtn"), never pre-formatted -
        # built into a real CSS id selector here so callers don't have to
        # get CSS syntax right for the one case that's unambiguous.
        return value if value.startswith("#") else f"#{value}"
    if strategy == "structural":
        return f"xpath={value}"
    # css and accessible_name are expected to already be a complete, valid
    # Playwright selector string (e.g. 'input[name="username"]' or
    # 'role=button[name="Log In"]').
    return value
