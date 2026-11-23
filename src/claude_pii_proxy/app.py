"""HTTP gateway (contracts/http-interface.md)."""

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx
from httpx_sse import EventSource
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route
from starlette.types import Lifespan

from claude_pii_proxy.errors import (
    BadRequest,
    ProxyError,
    RouteNotFound,
    ScrubFailed,
    UnsupportedEncoding,
    UpstreamUnavailable,
)
from claude_pii_proxy.notice import with_notice
from claude_pii_proxy.report import ScrubReport
from claude_pii_proxy.restore import restore_message
from claude_pii_proxy.scrub import Pipeline
from claude_pii_proxy.sessions import Session, SessionStore
from claude_pii_proxy.sse import restore_stream
from claude_pii_proxy.walker import apply_leaves, collect_leaves

log = logging.getLogger("claude_pii_proxy")

SESSION_HEADER = "x-claude-code-session-id"
SCRUB_HEADER = "x-pii-proxy-scrubbed"
_HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)
_NOT_FORWARDED_REQUEST = _HOP_BY_HOP | {"host", "content-length", "accept-encoding"}
_NOT_FORWARDED_RESPONSE = _HOP_BY_HOP | {"content-length", "content-encoding"}
_SIGNATURE_REJECTION = ("signature", "cannot be modified")


def create_app(
    *,
    pipeline: Pipeline,
    sessions: SessionStore,
    upstream: httpx.AsyncClient,
    lifespan: Lifespan[Starlette] | None = None,
) -> Starlette:
    proxy = _Proxy(pipeline, sessions, upstream)
    routes = [
        Route("/v1/messages", proxy.messages, methods=["POST"]),
        Route("/v1/messages/count_tokens", proxy.count_tokens, methods=["POST"]),
        Route("/api/hello", proxy.passthrough, methods=["HEAD"]),
        Route("/v1/models", proxy.passthrough, methods=["GET"]),
    ]
    # Unknown routes may carry content the proxy can't scrub: they fail closed, never forwarded.
    return Starlette(
        routes=routes,
        lifespan=lifespan,
        exception_handlers={
            404: _not_found,
            405: _not_found,
            ProxyError: _proxy_error,
        },
    )


class _Proxy:
    def __init__(self, pipeline: Pipeline, sessions: SessionStore, upstream: httpx.AsyncClient):
        self._pipeline = pipeline
        self._sessions = sessions
        self._upstream = upstream

    async def messages(self, request: Request) -> Response:
        session_id, session, report, body = await self._scrub(request)
        upstream = await self._send(request, json.dumps(body).encode())
        if not upstream.is_success:
            return await self._forward_error(upstream, session_id, session, report)
        if upstream.headers.get("content-type", "").startswith("text/event-stream"):
            return self._stream(upstream, session_id, session, report)
        try:
            restored, unissued = restore_message(json.loads(await upstream.aread()), session.vault)
        finally:
            await upstream.aclose()
        report.unissued += unissued
        self._log(session_id, session, report)
        return JSONResponse(restored, upstream.status_code, self._headers(upstream, report))

    async def count_tokens(self, request: Request) -> Response:
        session_id, session, report, body = await self._scrub(request)
        upstream = await self._send(request, json.dumps(body).encode())
        if not upstream.is_success:
            return await self._forward_error(upstream, session_id, session, report)
        content = await _read_and_close(upstream)
        self._log(session_id, session, report)
        return Response(content, upstream.status_code, self._headers(upstream, report))

    async def passthrough(self, request: Request) -> Response:
        upstream = await self._send(request, await request.body())
        content = await _read_and_close(upstream)
        return Response(content, upstream.status_code, _response_headers(upstream))

    async def _scrub(self, request: Request) -> tuple[str, Session, ScrubReport, dict[str, Any]]:
        session_id = request.headers.get(SESSION_HEADER)
        if not session_id:
            raise BadRequest(f"missing {SESSION_HEADER} header")
        if request.headers.get("content-encoding"):
            raise UnsupportedEncoding("compressed request bodies are not supported")
        body = _parse_body(await request.body())
        session = self._sessions.get(session_id, has_assistant_turns=_has_assistant_turns(body))
        report = ScrubReport()
        leaves = collect_leaves(body)
        report.skipped_binary = leaves.skipped_binary
        results = await session.scrub_leaves(leaves.texts, self._pipeline, report)
        failures = [r for r in results if isinstance(r, Exception)]
        if failures:
            raise _as_proxy_error(failures[0])
        scrubbed = apply_leaves(body, [r for r in results if isinstance(r, str)])
        return session_id, session, report, with_notice(scrubbed)

    async def _send(self, request: Request, content: bytes) -> httpx.Response:
        outbound = self._upstream.build_request(
            request.method,
            request.url.path,
            params=request.url.query,
            headers=_request_headers(request),
            content=content,
        )
        try:
            return await self._upstream.send(outbound, stream=True)
        except httpx.TransportError as e:
            raise UpstreamUnavailable(f"upstream unreachable ({type(e).__name__})") from e

    async def _forward_error(
        self, upstream: httpx.Response, session_id: str, session: Session, report: ScrubReport
    ) -> Response:
        content = await _read_and_close(upstream)
        if (
            upstream.status_code == 400
            and session.joined_mid_conversation
            and any(phrase in content.decode(errors="replace") for phrase in _SIGNATURE_REJECTION)
        ):
            log.warning(
                "session=%s upstream rejected re-sent thinking: the proxy joined this conversation "
                "mid-way (e.g. after a restart), so its placeholders differ from the earlier ones. "
                "Start a new Claude Code conversation.",
                session_id,
            )
        self._log(session_id, session, report)
        return Response(content, upstream.status_code, self._headers(upstream, report))

    def _stream(
        self, upstream: httpx.Response, session_id: str, session: Session, report: ScrubReport
    ) -> Response:
        events = EventSource(upstream).aiter_sse()

        async def body() -> AsyncIterator[bytes]:
            try:
                async for chunk in restore_stream(events, session.vault, report):
                    yield chunk
            finally:
                await upstream.aclose()

        # The report's unissued count is final only once the stream has ended.
        done = BackgroundTask(self._log, session_id, session, report)
        return StreamingResponse(
            body(), upstream.status_code, self._headers(upstream, report), background=done
        )

    def _headers(self, upstream: httpx.Response, report: ScrubReport) -> dict[str, str]:
        return _response_headers(upstream) | {SCRUB_HEADER: report.header_value()}

    @staticmethod
    def _log(session_id: str, session: Session, report: ScrubReport) -> None:
        session.totals.update(report)
        log.info(report.log_line(session_id, session.totals))


def _parse_body(raw: bytes) -> dict[str, Any]:
    try:
        body = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise BadRequest("request body is not valid JSON") from e
    if not isinstance(body, dict):
        raise BadRequest("request body must be a JSON object")
    if "messages" in body and not isinstance(body["messages"], list):
        raise BadRequest("`messages` must be a list")
    if "system" in body and not isinstance(body["system"], str | list):
        raise BadRequest("`system` must be a string or a list of blocks")
    return body


def _has_assistant_turns(body: dict[str, Any]) -> bool:
    return any(
        isinstance(m, dict) and m.get("role") == "assistant" for m in body.get("messages", [])
    )


def _as_proxy_error(failure: Exception) -> ProxyError:
    if isinstance(failure, ProxyError):
        return failure
    # The cause names only the error type: its message may quote request content.
    log.error("local scrubbing failed: %s", type(failure).__name__)
    return ScrubFailed(f"local scrubbing failed ({type(failure).__name__})")


def _request_headers(request: Request) -> list[tuple[str, str]]:
    headers = [(k, v) for k, v in request.headers.items() if k not in _NOT_FORWARDED_REQUEST]
    return [*headers, ("accept-encoding", "identity")]


def _response_headers(upstream: httpx.Response) -> dict[str, str]:
    return {k: v for k, v in upstream.headers.items() if k not in _NOT_FORWARDED_RESPONSE}


async def _read_and_close(upstream: httpx.Response) -> bytes:
    try:
        return await upstream.aread()
    finally:
        await upstream.aclose()


async def _not_found(request: Request, exc: Exception) -> Response:
    return await _proxy_error(request, RouteNotFound(f"route not supported: {request.method}"))


async def _proxy_error(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, ProxyError)
    headers = exc.headers()
    if request.url.path in ("/v1/messages", "/v1/messages/count_tokens"):
        headers[SCRUB_HEADER] = "none"  # nothing was forwarded
    return JSONResponse(exc.body(), exc.status, headers)
