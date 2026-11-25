"""One chat completion from the local Privatemode proxy, which holds its own API key."""

from typing import Any

import httpx

from llm_pii_proxy.errors import PrivatemodeInvalid, PrivatemodeUnavailable, ProxyError


async def complete(
    client: httpx.AsyncClient, request: dict[str, Any], purpose: str
) -> str | ProxyError:
    """The answer's text, or why there is none. Causes name `purpose` and the failure kind only:
    an answer quotes the request's content."""
    try:
        response = await client.post("/v1/chat/completions", json=request)
    except httpx.TimeoutException:
        return PrivatemodeUnavailable(f"{purpose} timed out")
    except httpx.TransportError as e:
        return PrivatemodeUnavailable(f"{purpose} unreachable ({type(e).__name__})")
    if not response.is_success:
        return PrivatemodeUnavailable(f"{purpose} answered HTTP {response.status_code}")
    try:
        choice = response.json()["choices"][0]
        content = choice["message"]["content"]
        finish_reason = choice.get("finish_reason")
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        return PrivatemodeInvalid(f"{purpose} returned an invalid answer")
    if not isinstance(content, str):
        return PrivatemodeInvalid(f"{purpose} returned an invalid answer")
    # Anything but a natural stop (e.g. the token limit) is a partial answer: unusable, and a
    # partial conversion would be cached.
    if finish_reason != "stop":
        return PrivatemodeInvalid(f"{purpose} answer was cut off")
    return content
