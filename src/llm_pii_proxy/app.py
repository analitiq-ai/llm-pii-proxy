"""HTTP gateway (contracts/http-interface.md)."""

import json
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import AsyncExitStack, aclosing
from typing import Any

import httpx
from httpx_sse import EventSource, ServerSentEvent
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route
from starlette.types import Lifespan, Receive, Scope, Send

from llm_pii_proxy.errors import (
    BadRequest,
    ProxyError,
    RouteNotFound,
    ScrubFailed,
    UnsupportedEncoding,
    UpstreamUnavailable,
)
from llm_pii_proxy.notice import with_notice
from llm_pii_proxy.report import ScrubReport
from llm_pii_proxy.restore import restore_message
from llm_pii_proxy.scrub import Pipeline
from llm_pii_proxy.sessions import Session, SessionStore
from llm_pii_proxy.sse import encode, restore_stream
from llm_pii_proxy.tracing.record import Leg, RequestTrace, Tap
from llm_pii_proxy.walker import apply_leaves, collect_leaves

log = logging.getLogger("llm_pii_proxy")

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
    tap: Tap,
    trace_legs: frozenset[Leg],
) -> Starlette:
    proxy = _Proxy(pipeline, sessions, upstream, tap, trace_legs)
    routes = [
        Route("/v1/messages", proxy.traced(proxy.messages), methods=["POST"]),
        Route("/v1/messages/count_tokens", proxy.traced(proxy.count_tokens), methods=["POST"]),
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


_TracedHandler = Callable[[Request, RequestTrace], Awaitable[Response]]


class _Proxy:
    def __init__(
        self,
        pipeline: Pipeline,
        sessions: SessionStore,
        upstream: httpx.AsyncClient,
        tap: Tap,
        trace_legs: frozenset[Leg],
    ):
        self._pipeline = pipeline
        self._sessions = sessions
        self._upstream = upstream
        self._tap = tap
        self._trace_legs = trace_legs

    def traced(self, handler: _TracedHandler) -> Callable[[Request], Awaitable[Response]]:
        """The one place a request's trace is finished: on the response, on a raised exception, or
        when a streamed response ends, however it ends. A request cancelled before the handler
        returns writes nothing."""

        async def endpoint(request: Request) -> Response:
            trace = RequestTrace(
                self._tap,
                self._trace_legs,
                session_id=request.headers.get(SESSION_HEADER) or None,
                route=request.url.path,
            )
            try:
                if trace.wants(Leg.CLIENT_IN):
                    trace.capture(Leg.CLIENT_IN, await request.body())
                response = await handler(request, trace)
            except ProxyError as e:
                trace.finish(e.status)
                raise
            except Exception:
                trace.finish(500)
                raise
            if isinstance(response, _StreamedResponse):
                # A streamed leg is known only once the response ends.
                response.on_end.callback(trace.finish, response.status_code)
            else:
                trace.capture(Leg.CLIENT_OUT, response.body)
                trace.finish(response.status_code)
            return response

        return endpoint

    async def messages(self, request: Request, trace: RequestTrace) -> Response:
        session_id, session, report, body = await self._scrub(request)
        upstream = await self._forward(request, trace, body)
        if not upstream.is_success:
            return await self._forward_error(upstream, trace, session_id, session, report)
        if upstream.headers.get("content-type", "").startswith("text/event-stream"):
            return self._stream(upstream, trace, session_id, session, report)
        content = await _read_and_close(upstream)
        trace.capture(Leg.UPSTREAM_IN, content)
        restored, unissued = restore_message(json.loads(content), session.vault)
        report.unissued += unissued
        self._log(session_id, session, report)
        return JSONResponse(restored, upstream.status_code, self._headers(upstream, report))

    async def count_tokens(self, request: Request, trace: RequestTrace) -> Response:
        session_id, session, report, body = await self._scrub(request)
        upstream = await self._forward(request, trace, body)
        if not upstream.is_success:
            return await self._forward_error(upstream, trace, session_id, session, report)
        content = await _read_and_close(upstream)
        trace.capture(Leg.UPSTREAM_IN, content)
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

    async def _forward(
        self, request: Request, trace: RequestTrace, body: dict[str, Any]
    ) -> httpx.Response:
        content = json.dumps(body).encode()
        trace.capture(Leg.UPSTREAM_OUT, content)
        upstream = await self._send(request, content)
        trace.upstream_responded(upstream.status_code)
        return upstream

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
        self,
        upstream: httpx.Response,
        trace: RequestTrace,
        session_id: str,
        session: Session,
        report: ScrubReport,
    ) -> Response:
        content = await _read_and_close(upstream)
        trace.capture(Leg.UPSTREAM_IN, content)
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
        self,
        upstream: httpx.Response,
        trace: RequestTrace,
        session_id: str,
        session: Session,
        report: ScrubReport,
    ) -> Response:
        received = _traced_stream(trace, Leg.UPSTREAM_IN, EventSource(upstream).aiter_sse())
        restored = restore_stream(received, session.vault, report)
        body = _encoded(_traced_stream(trace, Leg.CLIENT_OUT, restored))
        response = _StreamedResponse(body, upstream.status_code, self._headers(upstream, report))
        # Run last to first: the report's unissued count is final only once the body is closed.
        response.on_end.callback(self._log, session_id, session, report)
        response.on_end.push_async_callback(upstream.aclose)
        response.on_end.push_async_callback(body.aclose)
        return response

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


def _traced_stream(
    trace: RequestTrace, leg: Leg, events: AsyncGenerator[ServerSentEvent]
) -> AsyncGenerator[ServerSentEvent]:
    if not trace.wants(leg):
        return events
    # Opened here, not in the generator: a generator's body runs only once it is first read.
    trace.open_stream(leg)
    return _fed_to(trace, leg, events)


# Each stage closes the one it reads from, so closing the body closes the whole stream.


async def _fed_to(
    trace: RequestTrace, leg: Leg, events: AsyncGenerator[ServerSentEvent]
) -> AsyncGenerator[ServerSentEvent]:
    async with aclosing(events):
        async for event in events:
            trace.feed(leg, event)
            yield event


async def _encoded(events: AsyncGenerator[ServerSentEvent]) -> AsyncGenerator[bytes]:
    async with aclosing(events):
        async for event in events:
            yield encode(event)


class _StreamedResponse(StreamingResponse):
    """Runs its `on_end` steps once the response ends, however it ends. A step in the body's
    `finally` would not do: on a client disconnect Starlette abandons the body where it is, to
    be finalized only by the garbage collector, and a body never started has no `finally` to run.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.on_end = AsyncExitStack()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async with self.on_end:
            await super().__call__(scope, receive, send)


async def _not_found(request: Request, exc: Exception) -> Response:
    return await _proxy_error(request, RouteNotFound(f"route not supported: {request.method}"))


async def _proxy_error(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, ProxyError)
    headers = exc.headers()
    if request.url.path in ("/v1/messages", "/v1/messages/count_tokens"):
        headers[SCRUB_HEADER] = "none"  # nothing was forwarded
    return JSONResponse(exc.body(), exc.status, headers)
