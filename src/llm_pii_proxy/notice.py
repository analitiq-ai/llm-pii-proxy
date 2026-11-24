"""Placeholder notice appended to `system` (FR-019, contracts/http-interface.md)."""

from typing import Any

from llm_pii_proxy.errors import BadRequest

# Without this, Claude refused value-dependent tasks in spike R0; with it, refusals stopped (R0b).
# Constant, so it never changes the bytes of re-sent history. It gives no advice on taking part of
# a value: those tasks are out of scope, and a tool extracting a part would send it upstream raw.
NOTICE = (
    "Personal data and secrets in this conversation have been replaced by placeholders such as "
    "PII_EMAIL_0001 or PII_PERSON_0003. A local proxy restores the real values in everything you "
    "send back, including tool inputs, before they reach the user or any file. Wherever a real "
    "value is needed, use its placeholder verbatim. Do not refuse, hedge, or ask the user for the "
    "real value because a placeholder is present."
)


def with_notice(body: dict[str, Any]) -> dict[str, Any]:
    """A copy of `body` with the notice appended last to `system`; earlier blocks are unchanged."""
    system = body.get("system")
    if system is None:
        appended: str | list[Any] = NOTICE
    elif isinstance(system, str):
        appended = system + "\n\n" + NOTICE
    elif isinstance(system, list):
        appended = [*system, {"type": "text", "text": NOTICE}]
    else:
        raise BadRequest("`system` must be a string or a list of blocks")
    return {**body, "system": appended}
