"""The tool schema sent to Claude on every API call - the menu of actions
it's allowed to request. Claude never calls anything itself; it only ever
responds with a tool-use block matching one of these shapes, which our own
code then executes. See REPORT.md, "Architecture".

Every action tool requires "reasoning" - this is what guarantees
Locator.reasoning (required in schema.py) is always populated at the
moment Claude acts, not invented afterward.
"""

LOCATOR_STRATEGY = {
    "type": "string",
    "enum": ["accessible_name", "id_attribute", "css", "structural"],
    "description": (
        "How the locator 'value' should be interpreted - the exact format "
        "matters, found necessary via live testing:\n"
        "- id_attribute: value is the BARE id, no '#' prefix (e.g. 'submitBtn').\n"
        "- accessible_name: value must be a COMPLETE Playwright role selector, "
        'e.g. \'role=button[name="Log In"]\' - not just the bare name text.\n'
        "- css: value must be a COMPLETE, valid CSS selector, "
        'e.g. \'input[name="username"]\'. Prefer this when an element only has '
        "a name attribute (no id, no accessible name) - see the 'Form elements "
        "on this page' section of the observation.\n"
        "- structural: value must be a valid XPath expression."
    ),
}

_CONDITION_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["text_present", "element_visible", "url_matches"]},
        "text": {"type": "string"},
        "pattern": {"type": "string"},
        "description": {"type": "string"},
    },
    "required": ["kind", "description"],
}

TOOLS = [
    {
        "name": "navigate",
        "description": "Navigate the browser to a URL.",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    },
    {
        "name": "click",
        "description": "Click an element identified by a locator.",
        "input_schema": {
            "type": "object",
            "properties": {
                "strategy": LOCATOR_STRATEGY,
                "value": {"type": "string", "description": "The locator value (e.g. a CSS selector or role query)."},
                "reasoning": {"type": "string", "description": "Why this locator should reliably identify the element."},
            },
            "required": ["strategy", "value", "reasoning"],
        },
    },
    {
        "name": "fill",
        "description": "Type text into an input identified by a locator.",
        "input_schema": {
            "type": "object",
            "properties": {
                "strategy": LOCATOR_STRATEGY,
                "value": {"type": "string"},
                "text": {
                    "type": "string",
                    "description": 'The text to type. For any secret input, use a "{input_name}" '
                    "token instead of its real value - the real value is never shown to you.",
                },
                "reasoning": {"type": "string"},
            },
            "required": ["strategy", "value", "text", "reasoning"],
        },
    },
    {
        "name": "select_option",
        "description": "Select an option in a dropdown identified by a locator.",
        "input_schema": {
            "type": "object",
            "properties": {
                "strategy": LOCATOR_STRATEGY,
                "value": {"type": "string"},
                "option": {"type": "string"},
                "reasoning": {"type": "string"},
            },
            "required": ["strategy", "value", "option", "reasoning"],
        },
    },
    {
        "name": "read_state",
        "description": "Re-observe the current page without taking any action.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "finish",
        "description": "Signal that the goal has been met, or that you are stuck and cannot safely proceed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["success", "stuck"]},
                "summary": {"type": "string"},
                "successful_step_indices": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Indices (1-based, in the order you took them) of the actions "
                    "that were actually part of the successful path - omit any that were "
                    "mistakes or corrected.",
                },
                "checkpoint_conditions": {
                    "type": "array",
                    "items": _CONDITION_SCHEMA,
                    "description": "The condition(s) that confirm you reached the right RESULT "
                    "PAGE - not that one specific business outcome occurred. E.g. for a loan "
                    "request, use something true whether the loan was approved OR denied "
                    "(like a 'Loan Request Processed' heading), never outcome-specific text "
                    "like 'has been approved'. The specific outcome (approved vs denied, "
                    "found vs not found, etc.) is data the caller reads afterward, not part "
                    "of confirming you reached the right page.\n"
                    "IMPORTANT: prefer kind='text_present' unless you are also providing a "
                    "real, complete locator (same format as click/fill locators). A "
                    "kind='element_visible' condition with no locator can never be checked "
                    "and will always fail - if you don't have a specific locator for it, "
                    "describe the same thing as text_present instead.",
                },
                "known_outcome_rules": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "outcome_id": {"type": "string"},
                            "when": _CONDITION_SCHEMA,
                            "description": {"type": "string"},
                        },
                        "required": ["outcome_id", "when", "description"],
                    },
                    "description": "Optional: any other recognized non-success terminal states "
                    "encountered (e.g. session expired). Omit if not applicable.",
                },
            },
            "required": ["status", "summary"],
        },
    },
]
